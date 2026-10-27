import hashlib
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from fluxftp.drive_upload import DriveUpload
from fluxftp.core import Cancelled
from fluxftp.transfer_reliability import ReliabilityError


def uploader(tmp_path, monkeypatch):
    import fluxftp.drive_upload as module
    monkeypatch.setattr(module, 'CHUNK', 4)
    state = dict(data=b'', requests=[], sessions=0, lose=False, expire=False)
    local = tmp_path / 'source.bin'
    if not local.exists(): local.write_bytes(b'abcdefghijkl')
    connection = SimpleNamespace(service=SimpleNamespace(directory=tmp_path), fs='account:', upload_limiter=None)
    class Upload(DriveUpload):
        def configuration(self): return {}
        def stat(self, remote): return None
        def request(self, method, url, body=None, headers=None):
            state['requests'].append((method, headers, body))
            if method == 'POST':
                state['sessions'] += 1
                state['data'] = b''
                return 200, {'Location': 'https://www.googleapis.com/session'}, b''
            if state['expire']:
                state['expire'] = False
                return 404, {}, b''
            if body:
                offset = int(headers['Content-Range'].split()[1].split('-')[0])
                assert offset == len(state['data'])
                state['data'] += body
                if state['lose']:
                    state['lose'] = False
                    raise OSError('response lost')
            if state['data'] == local.read_bytes():
                return 200, {}, json.dumps({'size': len(state['data']), 'md5Checksum': hashlib.md5(state['data']).hexdigest()}).encode()
            return 308, ({'Range': f'bytes=0-{len(state["data"])-1}'} if state['data'] else {}), b''
    return Upload, connection, local, state


def test_restart_queries_server_after_lost_ack_and_keeps_session_encrypted(tmp_path, monkeypatch):
    Upload, connection, local, state = uploader(tmp_path, monkeypatch)
    state['lose'] = True
    with pytest.raises(OSError):
        Upload(connection).upload(local, 'file.bin', Event(), lambda *_: None)
    saved = next((tmp_path / 'drive-sessions').glob('*.enc'))
    assert 'googleapis' not in saved.read_text()
    assert state['data'] == b'abcd'
    progress = []
    Upload(connection).upload(local, 'file.bin', Event(), lambda done, total: progress.append(done))
    assert state['sessions'] == 1 and state['data'] == local.read_bytes()
    assert progress[0] == 4 and progress[-1] == 12
    assert not saved.exists()


def test_cancel_and_resume_new_instance(tmp_path, monkeypatch):
    Upload, connection, local, state = uploader(tmp_path, monkeypatch)
    cancel = Event()
    def progress(done, _):
        if done == 4: cancel.set()
    with pytest.raises(Cancelled):
        Upload(connection).upload(local, 'file.bin', cancel, progress)
    Upload(connection).upload(local, 'file.bin', Event(), lambda *_: None)
    assert state['sessions'] == 1 and state['data'] == local.read_bytes()


@pytest.mark.parametrize('change', ['source', 'expired'])
def test_changed_source_or_expired_session_restarts(tmp_path, monkeypatch, change):
    Upload, connection, local, state = uploader(tmp_path, monkeypatch)
    state['lose'] = True
    with pytest.raises(OSError): Upload(connection).upload(local, 'file.bin', Event(), lambda *_: None)
    if change == 'source': local.write_bytes(b'ABCDEFGHIJKL')
    else: state['expire'] = True
    Upload(connection).upload(local, 'file.bin', Event(), lambda *_: None)
    assert state['sessions'] == 2 and state['data'] == local.read_bytes()


def test_same_size_skip_requires_matching_hash(tmp_path, monkeypatch):
    Upload, connection, local, state = uploader(tmp_path, monkeypatch)
    upload = Upload(connection)
    monkeypatch.setattr(upload, 'stat', lambda _: {'Size': 12, 'Hashes': {'md5': hashlib.md5(local.read_bytes()).hexdigest()}})
    assert upload.verified_same(local, 'file.bin', Event())
    monkeypatch.setattr(upload, 'stat', lambda _: {'Size': 12, 'Hashes': {'md5': '0' * 32}})
    with pytest.raises(ReliabilityError): upload.verified_same(local, 'file.bin', Event())


def test_session_address_cannot_receive_token_on_other_host(tmp_path):
    upload = DriveUpload(SimpleNamespace(service=None))
    with pytest.raises(ReliabilityError): upload.request('PUT', 'https://example.com/stolen', b'')


def test_process_exit_after_ack_recovers_from_durable_session(tmp_path):
    import os
    import subprocess
    import sys
    script = tmp_path / 'crash_worker.py'
    script.write_text('''
import os, sys
from pathlib import Path
from threading import Event
import pytest
from test_drive_upload import uploader
from fluxftp import credentials
credentials._key = lambda create=False: b'X' * 32
directory = Path(sys.argv[1])
Upload, connection, local, state = uploader(directory, pytest.MonkeyPatch())
remote = directory / 'remote.bin'
if remote.exists(): state['data'] = remote.read_bytes()
class DurableServer(Upload):
    def request(self, *args, **kwargs):
        result = super().request(*args, **kwargs)
        remote.write_bytes(state['data'])
        if sys.argv[2] == 'crash' and len(state['data']) == 4:
            os._exit(23)
        return result
DurableServer(connection).upload(local, 'file.bin', Event(), lambda *_: None)
assert state['sessions'] == 0, 'Restart must reuse the original session'
''', encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root) + os.pathsep + str(root / 'tests'))
    first = subprocess.run([sys.executable, str(script), str(tmp_path), 'crash'], env=env, timeout=30,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), capture_output=True)
    assert first.returncode == 23, first.stderr.decode()
    assert (tmp_path / 'remote.bin').read_bytes() == b'abcd'
    second = subprocess.run([sys.executable, str(script), str(tmp_path), 'resume'], env=env, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), capture_output=True)
    assert second.returncode == 0, second.stderr.decode()
    assert (tmp_path / 'remote.bin').read_bytes() == b'abcdefghijkl'


def test_service_account_jwt_is_signed_and_scope_preserved(monkeypatch):
    import base64
    from urllib.parse import parse_qs
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from fluxftp.drive_upload import service_account_token
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    account = dict(client_email='test@example.invalid', private_key=private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return b'{"access_token":"test-only","expires_in":3600}'
    def open_request(request, timeout):
        assert request.full_url == 'https://oauth2.googleapis.com/token'
        assertion = parse_qs(request.data.decode())['assertion'][0]
        header, payload, signature = assertion.split('.')
        decode = lambda value: base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        private.public_key().verify(decode(signature), (header + '.' + payload).encode(), padding.PKCS1v15(), hashes.SHA256())
        assert json.loads(decode(payload))['scope'] == 'https://www.googleapis.com/auth/drive.file'
        return Response()
    monkeypatch.setattr('fluxftp.drive_upload.build_opener', lambda *_: SimpleNamespace(open=open_request))
    token, expiry = service_account_token(dict(service_account_credentials=json.dumps(account), scope='drive.file'))
    assert token == 'test-only'


def test_finished_file_must_match_checksum(tmp_path, monkeypatch):
    Upload, connection, local, state = uploader(tmp_path, monkeypatch)
    class WrongHash(Upload):
        def request(self, *args, **kwargs):
            code, headers, payload = super().request(*args, **kwargs)
            if payload: payload = b'{"size":12,"md5Checksum":"wrong"}'
            return code, headers, payload
    with pytest.raises(ReliabilityError, match='matching size and MD5'):
        WrongHash(connection).upload(local, 'file.bin', Event(), lambda *_: None)
    assert list((tmp_path / 'drive-sessions').glob('*.enc'))


def test_empty_file_uses_backend_and_verifies_hash(tmp_path, monkeypatch):
    Upload, connection, local, state = uploader(tmp_path, monkeypatch)
    local.write_bytes(b'')
    calls = []
    connection.service.job = lambda *a, **k: calls.append(a)
    upload = Upload(connection)
    monkeypatch.setattr(upload, 'stat', lambda _: {'Size': 0, 'Hashes': {'md5': hashlib.md5(b'').hexdigest()}})
    upload.upload(local, 'empty.bin', Event(), lambda *_: None)
    assert calls[0][0] == 'operations/copyfile' and state['sessions'] == 0


def test_fresh_overwrite_is_honored_but_expired_completed_session_is_not_duplicated(tmp_path, monkeypatch):
    Upload, connection, local, state = uploader(tmp_path, monkeypatch)
    existing = {'ID': 'file-id', 'Size': 12, 'Hashes': {'md5': hashlib.md5(local.read_bytes()).hexdigest()}}
    class Overwrite(Upload):
        def stat(self, remote): return existing
        def request(self, method, url, body=None, headers=None):
            if method == 'PATCH':
                assert '/files/file-id?' in url
                method = 'POST'
            return super().request(method, url, body, headers)
    Overwrite(connection).upload(local, 'file.bin', Event(), lambda *_: None)
    assert state['sessions'] == 1
    state['lose'] = True
    with pytest.raises(OSError): Overwrite(connection).upload(local, 'file.bin', Event(), lambda *_: None)
    state['expire'] = True
    Overwrite(connection).upload(local, 'file.bin', Event(), lambda *_: None)
    assert state['sessions'] == 2  # Recovery recognized completion without a third session.
