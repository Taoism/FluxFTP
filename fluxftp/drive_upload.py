"""Durable Google Drive resumable uploads. Session URLs are encrypted at rest."""
import hashlib
import base64
import json
import posixpath
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from weakref import WeakValueDictionary
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .transfer_reliability import ReliabilityError, checked

API = 'https://www.googleapis.com'
CHUNK = 8 * 1024 * 1024  # A multiple of Drive's 256 KiB minimum.
_locks = WeakValueDictionary()
_guard = RLock()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def signature(path):
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def checksum(path, cancel):
    value = hashlib.md5()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            checked(cancel)
            value.update(block)
    return value.hexdigest()


class DriveUpload:
    def __init__(self, connection):
        self.connection = connection
        self.service = connection.service
        self.token = ''
        self.expires = 0

    def configuration(self):
        return self.service.call('config/get', {'name': self.connection.fs.rstrip(':')})

    def access_token(self):
        if time.time() >= self.expires:
            config = self.configuration()
            if config.get('service_account_file') or config.get('service_account_credentials'):
                self.token, self.expires = service_account_token(config)
                return self.token
            # An authenticated backend operation refreshes OAuth and saves the
            # token through rclone's existing encrypted configuration writer.
            self.service.job('operations/about', {'fs': self.connection.fs}, cancel=getattr(self, 'cancel', None))
            config = self.configuration()
            token = json.loads(config.get('token') or '{}')
            self.token = token.get('access_token', '')
            if not self.token:
                raise ReliabilityError('Drive resumable uploads require an OAuth cloud account. Reauthorize this account with Google sign-in.')
            try:
                self.expires = datetime.fromisoformat(token['expiry'].replace('Z', '+00:00')).timestamp() - 60
            except (KeyError, ValueError):
                self.expires = time.time() + 60
        return self.token

    def request(self, method, url, body=None, headers=None):
        if getattr(self, 'cancel', None) is not None: checked(self.cancel)
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.hostname != 'www.googleapis.com' or parts.port not in (None, 443) or parts.username:
            raise ReliabilityError('Drive returned an invalid upload session address.')
        request = Request(url, data=body, method=method, headers={
            'Authorization': 'Bearer ' + self.access_token(), **(headers or {})})
        try:
            response = build_opener(NoRedirect()).open(request, timeout=60)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError):
            raise OSError('Drive upload connection interrupted; its saved session will be checked on retry.') from None
        with response:
            payload = response.read(1024 * 1024)
            return response.code, dict(response.headers), payload

    def stat(self, remote):
        return self.connection.request('operations/stat', remote, opt={'showHash': True}).get('item')

    def verified_same(self, local, remote, cancel):
        local = Path(local)
        before = signature(local)
        item = self.stat(remote)
        expected = (item or {}).get('Hashes', {}).get('md5', '')
        if not expected or int(item.get('Size', -1)) != before[1]:
            raise ReliabilityError('Drive file cannot be safely skipped: size or MD5 is unavailable or differs. Choose an explicit overwrite if intended.')
        actual = checksum(local, cancel)
        from .verification_reports import evidence
        evidence('checksum', 'verified' if before == signature(local) and actual.lower() == expected.lower() else 'mismatch', expected, actual, 'MD5')
        if before != signature(local) or actual.lower() != expected.lower():
            raise ReliabilityError('Drive file checksum differs from the local source; it was not skipped or overwritten.')
        return True

    def upload(self, local, remote, cancel, progress):
        self.cancel = cancel
        local = Path(local)
        identity = hashlib.sha256((self.connection.fs + '\0' + remote).encode()).hexdigest()
        with _guard:
            lock = _locks.setdefault(identity, RLock())
        if not lock.acquire(blocking=False):
            raise ReliabilityError('Another upload is already writing this Drive destination.')
        try:
            return self._upload(local, remote, identity, cancel, progress)
        finally:
            lock.release()

    def _upload(self, local, remote, identity, cancel, progress):
        from .credentials import CredentialVault
        # Separate small encrypted records avoid rewriting an ever-growing vault.
        record = CredentialVault(self.service.directory / 'drive-sessions' / (identity + '.enc'))
        before = signature(local)
        total = before[1]
        config = self.configuration()
        destination = [self.connection.fs, remote, config.get('root_folder_id', ''), config.get('team_drive', '')]
        saved = json.loads(record.get('session') or '{}')
        digest = checksum(local, cancel)
        if signature(local) != before:
            raise ReliabilityError('Local source changed while preparing the Drive upload.')
        if total == 0:
            # No byte offset exists for empty files. Let the backend perform its
            # provider-specific empty upload, then apply the same verification.
            self.service.job('operations/copyfile', dict(srcFs=str(local.parent.resolve()), srcRemote=local.name,
                dstFs=self.connection.fs, dstRemote=remote, _config={'IgnoreTimes': True}), cancel=cancel)
            self.verified_same(local, remote, cancel)
            record.set('session', '')
            progress(0, 0)
            return
        if saved and (saved.get('source') != before or saved.get('md5') != digest or saved.get('destination') != destination):
            record.set('session', '')
            saved = {}
        recovering = bool(saved)
        # The source hash also guards same-size, same-timestamp edits on recovery.
        for attempt in range(3):
            checked(cancel)
            if not saved:
                existing = self.stat(remote)
                if existing and existing.get('IsDir'):
                    raise ReliabilityError('Drive destination is a directory.')
                if existing and existing.get('MimeType', '').startswith('application/vnd.google-apps.'):
                    raise ReliabilityError('A native Google document cannot be replaced by this binary upload. Choose a different destination name.')
                # Resolve uncertain completion without making duplicate files.
                if recovering and existing and int(existing.get('Size', -1)) == total and existing.get('Hashes', {}).get('md5', '').lower() == digest:
                    progress(total, total)
                    return
                parent = posixpath.dirname(remote)
                parent_item = self.stat(parent) if parent else None
                config = self.configuration()
                parent_id = (parent_item or {}).get('ID') if parent else config.get('root_folder_id') or config.get('team_drive') or 'root'
                if not parent_id:
                    raise ReliabilityError('Cannot resolve the Drive destination folder.')
                metadata = {'name': posixpath.basename(remote), 'modifiedTime': datetime.fromtimestamp(local.stat().st_mtime, timezone.utc).isoformat()}
                file_id = (existing or {}).get('ID')
                if existing and not file_id:
                    raise ReliabilityError('Cannot resolve the existing Drive file ID.')
                if not file_id:
                    metadata['parents'] = [parent_id]
                url = API + '/upload/drive/v3/files' + ('/' + quote(file_id, safe='') if file_id else '') + '?uploadType=resumable&supportsAllDrives=true&fields=id,size,md5Checksum'
                code, headers, _ = self.request('PATCH' if file_id else 'POST', url, json.dumps(metadata).encode(),
                    {'Content-Type': 'application/json; charset=UTF-8', 'X-Upload-Content-Type': 'application/octet-stream', 'X-Upload-Content-Length': str(total)})
                session = next((v for k, v in headers.items() if k.lower() == 'location'), '')
                if code not in (200, 201) or not session:
                    raise ReliabilityError(f'Drive could not start a resumable upload (HTTP {code}).')
                saved = {'url': session, 'source': before, 'md5': digest, 'destination': destination}
                record.set('session', json.dumps(saved))  # Durable before sending bytes.
            code, headers, payload = self.request('PUT', saved['url'], b'', {'Content-Range': f'bytes */{total}'})
            retries = 0
            previous_offset = -1
            stalled = 0
            with local.open('rb') as stream:
                while True:
                    checked(cancel)
                    if signature(local) != before:
                        raise ReliabilityError('Local source changed during the Drive upload; recovery will rebuild this file.')
                    if code in (200, 201):
                        try:
                            result = json.loads(payload)
                        except ValueError:
                            result = {}
                        from .verification_reports import evidence
                        evidence('checksum', 'verified' if int(result.get('size', -1)) == total and result.get('md5Checksum', '').lower() == digest else 'mismatch',
                                 digest, result.get('md5Checksum', ''), 'MD5')
                        if int(result.get('size', -1)) != total or result.get('md5Checksum', '').lower() != digest:
                            raise ReliabilityError('Drive upload finished without a matching size and MD5; session retained for verification.')
                        record.set('session', '')
                        progress(total, total)
                        return
                    if code in (404, 410):
                        saved = {}
                        record.set('session', '')
                        break  # Expired session: resolve destination, then restart.
                    if code == 401 or code == 429 or code >= 500:
                        if retries >= 3:
                            raise OSError(f'Drive upload temporarily unavailable (HTTP {code}); session retained.')
                        retries += 1
                        if code == 401: self.expires = 0
                        if cancel.wait(2 ** retries): checked(cancel)
                        code, headers, payload = self.request('PUT', saved['url'], b'', {'Content-Range': f'bytes */{total}'})
                        continue
                    if code != 308:
                        raise ReliabilityError(f'Drive upload failed (HTTP {code}); saved session retained.')
                    received = next((v for k, v in headers.items() if k.lower() == 'range'), '')
                    match = re.fullmatch(r'bytes=0-(\d+)', received) if received else None
                    if received and not match:
                        raise ReliabilityError('Invalid Drive upload acknowledgement.')
                    offset = int(match[1]) + 1 if match else 0
                    if offset > total:
                        raise ReliabilityError('Drive acknowledged more bytes than the source contains.')
                    stalled = stalled + 1 if offset == previous_offset else 0
                    if stalled >= 3:
                        raise OSError('Drive upload made no progress; its session is retained for retry.')
                    previous_offset = offset
                    progress(offset, total)
                    stream.seek(offset)
                    limiter = getattr(self.connection, 'upload_limiter', None)
                    rate = getattr(limiter, 'rate', 0)
                    chunk = min(CHUNK, max(256 * 1024, ((rate + 262143) // 262144) * 262144)) if rate else CHUNK
                    block = stream.read(chunk)
                    if not block and total:
                        raise ReliabilityError('Drive did not finalize the complete upload; retry to verify its status.')
                    if limiter: limiter.consume(len(block), cancel)
                    checked(cancel)
                    content_range = f'bytes {offset}-{offset + len(block) - 1}/{total}' if block else 'bytes */0'
                    code, headers, payload = self.request('PUT', saved['url'], block,
                        {'Content-Type': 'application/octet-stream', 'Content-Range': content_range})
        raise OSError('Drive upload sessions repeatedly expired; retry later.')


def service_account_token(config):
    """Exchange a signed service-account JWT without exposing its private key."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    try:
        raw = config.get('service_account_credentials')
        account = json.loads(raw if raw else Path(config['service_account_file']).expanduser().read_text('utf-8'))
        def encode(value):
            return base64.urlsafe_b64encode(value).rstrip(b'=')
        now = int(time.time())
        scopes = config.get('scope') or 'drive'
        claim = dict(iss=account['client_email'], scope=' '.join(
            scope if scope.startswith('https://') else 'https://www.googleapis.com/auth/' + scope
            for scope in scopes.replace(',', ' ').split()), aud='https://oauth2.googleapis.com/token', iat=now, exp=now + 3600)
        if config.get('impersonate'): claim['sub'] = config['impersonate']
        signing = encode(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode()) + b'.' + encode(json.dumps(claim).encode())
        private = serialization.load_pem_private_key(account['private_key'].encode(), password=None)
        assertion = signing + b'.' + encode(private.sign(signing, padding.PKCS1v15(), hashes.SHA256()))
        request = Request('https://oauth2.googleapis.com/token', data=urlencode({
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion.decode()}).encode(),
            headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            token = json.load(response)
        return token['access_token'], time.time() + max(1, int(token['expires_in']) - 60)
    except Exception:
        raise ReliabilityError('Could not authorize the Drive service account. Check its key file, scope and account permissions.') from None
