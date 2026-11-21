"""Private authenticated rclone process service with stable cloud exports."""
import atexit
import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import RLock
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

from .cloud_registry import CLOUD_PROTOCOLS, PROVIDERS, cloud_path, executable, operation_error
from .cloud_account_store import CloudAccountStore
from .cloud_jobs import CloudJobs


class CloudService(CloudAccountStore, CloudJobs):
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
