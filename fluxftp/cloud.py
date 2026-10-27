"""Cloud provider definitions and a private, authenticated rclone process."""
import atexit
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from threading import Event, RLock
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

PROVIDERS = {
    'Amazon S3': ('s3', {'provider': 'AWS'}),
    'Backblaze B2': ('b2', {}),
    'Box': ('box', {}),
    'Dropbox': ('dropbox', {}),
    'Google Cloud Storage': ('google cloud storage', {}),
    'Google Drive': ('drive', {}),
    'Microsoft Azure Blob Storage': ('azureblob', {}),
    'Microsoft OneDrive': ('onedrive', {}),
    'Microsoft OneDrive for Business': ('onedrive', {}),
    'Microsoft SharePoint Online': ('onedrive', {}),
    'OpenStack Swift': ('swift', {}),
    'WebDAV': ('webdav', {}),
}
CLOUD_PROTOCOLS = list(PROVIDERS)


def operation_error(details):
    """Return fixed guidance only; backend errors can contain credentials."""
    text = str(details).lower()
    if 'listbuckets' in text:
        return ('S3 could not list buckets. For a public bucket, set the site’s '
                'remote path to /bucket-name/folder in Site Manager instead of /.')
    if 'failed to start auth webserver' in text:
        port = '18342' if '127.0.0.1:18342' in text else '53682'
        if 'access permissions' in text or 'permission denied' in text:
            return ('Cloud sign-in could not start because the operating system blocked its local '
                    f'callback port (127.0.0.1:{port}). On Windows, check whether TCP port {port} '
                    'is in an excluded port range. Resolve the port restriction, then retry setup.')
        if 'address already in use' in text or 'only one usage of each socket address' in text:
            return ('Cloud sign-in could not start because another application or sign-in session '
                    f'is using local port {port}. Finish or cancel that sign-in, then retry setup.')
        return (f'Cloud sign-in could not start its local callback listener on port {port}. '
                'Check local port availability and security software, then retry setup.')
    if 'invalid_grant' in text:
        return ('Cloud authorization expired or was revoked. Sign in to the cloud account again, '
                'then reconnect the site. The provider rejected the saved authorization (invalid_grant).')
    return 'Cloud operation failed. Check account authorization, permissions and the selected path.'


def executable():
    name = 'rclone.exe' if sys.platform == 'win32' else 'rclone'
    path = (Path(sys._MEIPASS) / 'cloud_tools' / name if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[1] / 'runtime' / 'rclone' / name)
    if not path.is_file():
        raise RuntimeError('The bundled cloud backend is missing. Reinstall FluxFTP.')
    return path


def cloud_path(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('Invalid cloud path.')
    value = value.lstrip('/')
    if any(part in ('.', '..') for part in value.split('/')):
        raise ValueError('Cloud paths cannot contain . or .. components.')
    return value


class CloudService:
    def __init__(self, directory, vault):
        self.directory = Path(directory)
        self.vault = vault
        self.lock = RLock()
        self.process = None
        self.release_child = None
        self.url = ''
        self.authorization = ''
        self.settings_path = self.directory / 'cloud-accounts.json'
        self.config_path = self.directory / 'cloud.conf'
        atexit.register(self.close)

    def begin_edit(self, identity):
        """Stage account edits in an encrypted snapshot, preserving the account ID."""
        with self.lock:
            if identity not in self.accounts():
                raise ValueError('The cloud account no longer exists.')
            self.start()
            draft_dir = self.directory / ('edit-' + uuid.uuid4().hex)
            draft_dir.mkdir()
            draft = CloudService(draft_dir, self.vault)
            draft.edit_origin = self
            draft.edit_baseline = (self.config_path.read_bytes(), self.settings_path.read_bytes())
            draft.edit_identity = identity
            try:
                draft.config_path.write_bytes(draft.edit_baseline[0])
                draft.settings_path.write_bytes(draft.edit_baseline[1])
            except BaseException:
                draft.discard_edit()
                raise
            return draft

    def discard_edit(self):
        self.close()
        origin = self.edit_origin
        directory = self.directory.resolve()
        if directory.parent != origin.directory.resolve() or not directory.name.startswith('edit-'):
            raise RuntimeError('Invalid cloud edit directory.')
        if directory.exists():
            shutil.rmtree(directory)

    @staticmethod
    def _replace_file(path, content):
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def commit_edit(self):
        """Commit a staged encrypted snapshot only if the source is unchanged."""
        origin = self.edit_origin
        with origin.lock:
            baseline = self.edit_baseline
            current = (origin.config_path.read_bytes(), origin.settings_path.read_bytes())
            if current != baseline:
                raise RuntimeError('Cloud settings changed while editing. Cancel and open Edit again to keep those changes.')
            self.close()
            updated = (self.config_path.read_bytes(), self.settings_path.read_bytes())
            origin.close()
            if (origin.config_path.read_bytes(), origin.settings_path.read_bytes()) != baseline:
                raise RuntimeError('Cloud settings changed while editing. Cancel and open Edit again.')
            paths = (origin.config_path, origin.settings_path)
            try:
                for path, content in zip(paths, updated):
                    self._replace_file(path, content)
            except BaseException:
                for path, content in zip(paths, baseline):
                    self._replace_file(path, content)
                raise

    def accounts(self):
        with self.lock:
            if not self.settings_path.exists():
                return {}
            records = json.loads(self.settings_path.read_text('utf-8'))
            if not isinstance(records, dict):
                raise ValueError('Invalid cloud account list.')
            for identity, record in records.items():
                if not re.fullmatch(r'[0-9a-f]{32}', identity) or not isinstance(record, dict) or record.get('provider') not in PROVIDERS:
                    raise ValueError('Invalid cloud account entry.')
            return records

    def save_account(self, identity, name, provider):
        if not re.fullmatch(r'[0-9a-f]{32}', identity) or provider not in PROVIDERS:
            raise ValueError('Invalid cloud account.')
        name = name.strip()
        if not name or len(name) > 128 or any(ord(c) < 32 for c in name):
            raise ValueError('Enter a cloud account name of 1–128 characters.')
        with self.lock:
            records = self.accounts()
            if any(r['name'].casefold() == name.casefold() for k, r in records.items() if k != identity):
                raise ValueError('That cloud account name already exists.')
            records[identity] = dict(name=name, provider=provider)
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(records, indent=2), encoding='utf-8')
            temporary.replace(self.settings_path)

    def finish_account(self, identity, name, provider):
        details = self.call('config/get', {'name': 'c' + identity})
        if details.get('type') != PROVIDERS[provider][0]:
            raise ValueError('This authorization belongs to a different storage provider. Cancel and add that provider separately.')
        expected = {'Microsoft OneDrive': 'personal', 'Microsoft OneDrive for Business': 'business',
                    'Microsoft SharePoint Online': 'documentLibrary'}.get(provider)
        if expected and details.get('drive_type') != expected:
            raise ValueError('The selected drive does not match this provider. Choose Personal, Business, or a SharePoint document library as appropriate.')
        self.save_account(identity, name, provider)

    def remove_account(self, identity):
        with self.lock:
            records = self.accounts()
            self.call('config/delete', {'name': 'c' + identity})
            records.pop(identity, None)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(records, indent=2), encoding='utf-8')
            temporary.replace(self.settings_path)

    def start(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return
            if self.process is not None:
                self.close()
            binary = executable()
            password = self.vault.get('cloud-config-password')
            if not password:
                if self.config_path.exists():
                    raise RuntimeError('The cloud configuration encryption key is missing from the credential vault.')
                password = secrets.token_urlsafe(48)
                self.vault.set('cloud-config-password', password)
            self.directory.mkdir(parents=True, exist_ok=True)
            if not self.config_path.exists():
                # rclone's documented V0 config uses NaCl secretbox with this derived key.
                from nacl.secret import SecretBox
                key = hashlib.sha256(('[' + password + '][rclone-config]').encode()).digest()
                encrypted = bytes(SecretBox(key).encrypt(b''))
                with self.config_path.open('xb') as stream:
                    stream.write(b'RCLONE_ENCRYPT_V0:\n' + base64.b64encode(encrypted) + b'\n')
            else:
                with self.config_path.open('rb') as stream:
                    if b'RCLONE_ENCRYPT_V0:' not in stream.read(512):
                        raise RuntimeError('The cloud configuration must be encrypted. Restore its encrypted backup.')
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            username, rc_password = 'fluxftp', secrets.token_urlsafe(48)
            self.authorization = 'Basic ' + base64.b64encode(f'{username}:{rc_password}'.encode()).decode()
            self.url = f'http://127.0.0.1:{port}/'
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith('RCLONE_')}
            env.update(RCLONE_CONFIG_PASS=password, RCLONE_RC_USER=username, RCLONE_RC_PASS=rc_password)
            self.process = subprocess.Popen([str(binary), 'rcd', '--config', str(self.config_path),
                '--cache-dir', str(self.directory / 'cache'),
                '--rc-addr', f'127.0.0.1:{port}', '--rc-serve=false', '--rc-web-gui=false',
                '--ask-password=false', '--log-level', 'ERROR', '--retries', '2', '--low-level-retries', '3'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            from .child_process import contain
            try:
                self.release_child = contain(self.process)
            except OSError:
                self.close()
                raise
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    self._request('core/version', {}, 1)
                    return
                except (OSError, ValueError):
                    time.sleep(.05)
            self.close()
            raise RuntimeError('Cloud backend could not start or unlock its encrypted configuration.')

    def _request(self, endpoint, parameters, timeout=30):
        request = Request(self.url + endpoint, data=json.dumps(parameters).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': self.authorization})
        # Loopback control traffic must never go through system proxy settings.
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            # Never expose backend response bodies: they can contain OAuth tokens or request parameters.
            raise OSError(f'Cloud operation failed (HTTP {error.code}). Check account access, endpoint and path.') from None
        except URLError:
            raise OSError('The cloud backend is unavailable.') from None

    def call(self, endpoint, parameters=None, timeout=30):
        self.start()
        return self._request(endpoint, parameters or {}, timeout)

    def job(self, endpoint, parameters, cancel=None, progress=None, total=0, timeout=300):
        manager = getattr(self, 'bandwidth_manager', None)
        if manager and endpoint == 'operations/copyfile':
            self.start()
            directions = []
            if not Path(parameters.get('dstFs', '')).is_absolute(): directions.append(0)
            if not Path(parameters.get('srcFs', '')).is_absolute(): directions.append(1)
            with manager.lease('cloud', directions):
                return self._job(endpoint, parameters, cancel, progress, total, timeout)
        return self._job(endpoint, parameters, cancel, progress, total, timeout)

    def _job(self, endpoint, parameters, cancel=None, progress=None, total=0, timeout=300):
        cancel = cancel or Event()
        from .core import Cancelled
        if cancel.is_set():
            raise Cancelled('Cancelled')
        group = 'fluxftp-' + uuid.uuid4().hex
        launch = self.call(endpoint, {**parameters, '_async': True, '_group': group})
        job = launch['jobid']
        process = self.process
        started = time.monotonic()
        try:
            while True:
                if self.process is not process or process.poll() is not None:
                    raise OSError('The cloud backend stopped during this operation. Retry the transfer.')
                if cancel.is_set():
                    raise Cancelled('Cancelled')
                status = self._request('job/status', {'jobid': job})
                if launch.get('executeId') and status.get('executeId') != launch['executeId']:
                    raise OSError('The cloud backend restarted during this operation. Retry the transfer.')
                if status.get('finished'):
                    if not status.get('success'):
                        raise OSError(operation_error(status.get('error', '')))
                    if progress:
                        progress(total, total)
                    return status.get('output', {})
                if progress:
                    stats = self._request('core/stats', {'group': group})
                    progress(min(int(stats.get('bytes', 0)), total), total)
                if timeout and time.monotonic() - started > timeout:
                    raise TimeoutError('Cloud operation timed out.')
                cancel.wait(.2)
        except BaseException:
            if self.process is not process or process.poll() is not None:
                raise
            try:
                if endpoint.startswith('config/'):
                    try:
                        self._request('config/oauthstop', {}, timeout=2)
                    except OSError:
                        pass
                self._request('job/stop', {'jobid': job})
                # Do not release a destination while the backend still writes to it.
                until = time.monotonic() + 20
                while time.monotonic() < until:
                    if self._request('job/status', {'jobid': job}).get('finished'):
                        break
                    time.sleep(.1)
                else:
                    self.close()
            except OSError:
                self.close()
            raise
        finally:
            try:
                self._request('core/stats-delete', {'group': group}, 2)
            except OSError:
                pass

    def fs(self, site):
        account = self.accounts().get(site.cloud_account)
        if account is None or account['provider'] != site.protocol:
            raise ValueError('Select an authorized cloud account matching this provider in site properties.')
        return 'c' + site.cloud_account + ':'

    def close(self):
        with self.lock:
            process, self.process = self.process, None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)
            if self.release_child:
                self.release_child()
                self.release_child = None
