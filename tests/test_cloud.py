import json
import socket
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event, Thread, Timer
from uuid import uuid4

import pytest
from urllib.request import urlopen
from urllib.error import HTTPError
from fluxftp.cloud import CloudService, PROVIDERS, cloud_path, executable
from fluxftp.cloud_connection import CloudConnection, relay
from fluxftp.core import Site, PROTOCOLS, Cancelled, transfer_tree
from fluxftp.credentials import CredentialVault
from fluxftp.transfer_options import TransferOptions
from fluxftp.app import SiteDialog
from fluxftp.cloud_accounts import CloudAccountWizard
from test_queue import window
from test_ui import settle
from test_integration import servers
from test_sftp import ssh_server


@pytest.fixture
def service(tmp_path):
    service = CloudService(tmp_path / 'cloud', CredentialVault(tmp_path / 'vault.enc'))
    service.start()
    yield service
    service.close()


def local_remote(service, tmp_path, name='first'):
    identity = uuid4().hex
    root = tmp_path / name
    root.mkdir()
    service.call('config/create', {'name': 'c' + identity, 'type': 'alias', 'parameters': {'remote': str(root)}})
    service.save_account(identity, name, 'WebDAV')
    site = Site(name=name, host='cloud-' + identity, protocol='WebDAV', cloud_account=identity, username='')
    return root, site


def test_backend_providers_encryption_and_auth(service):
    providers = service.call('config/providers')['providers']
    available = {p['Name'] for p in providers}
    assert {backend for backend, _ in PROVIDERS.values()} <= available
    assert service.config_path.read_text().lstrip().startswith('RCLONE_ENCRYPT_V0:')
    with pytest.raises(HTTPError):
        urlopen(service.url + 'config/listremotes', timeout=3)
    service.call('config/create', {'name': 'test_secret', 'type': 'webdav',
        'parameters': {'url': 'https://example.invalid/', 'user': 'test-user', 'pass': 'secret-password'},
        'opt': {'nonInteractive': True, 'obscure': True}})
    assert 'secret-password' not in service.config_path.read_text()
    assert 'test-user' not in service.config_path.read_text()
    pid = service.process.pid
    service.close()
    service.start()
    assert service.process.pid != pid
    assert 'test_secret' in service.call('config/listremotes')['remotes']


def test_cloud_file_roundtrip_and_mutations(service, tmp_path):
    root, site = local_remote(service, tmp_path)
    data = b'cloud binary\x00\r\n' * 10000
    local = tmp_path / 'source.bin'
    local.write_bytes(data)
    progress = []
    with CloudConnection(site, service) as connection:
        connection.mkdir('/folder')
        connection.transfer(local, '/folder/data.bin', True, Event(), lambda *p: progress.append(p))
        assert connection.size('/folder/data.bin') == len(data)
        assert [e.name for e in connection.list('/folder')] == ['data.bin']
        output = tmp_path / 'download.bin'
        output.write_bytes(b'previous')
        connection.transfer(output, '/folder/data.bin', False, Event(), lambda *p: progress.append(p))
        assert output.read_bytes() == data
        connection.rename('/folder/data.bin', '/folder/new.bin')
        assert (root / 'folder/new.bin').read_bytes() == data
        with pytest.raises(OSError):
            connection.remove('/folder', True)
        connection.rename('/folder', '/renamed')
        assert (root / 'renamed/new.bin').is_file()
        connection.remove('/renamed/new.bin')
        connection.remove('/renamed', True)
    assert progress[-1] == (len(data), len(data))


def test_cloud_conflicts_recursive_relay_and_cancel(service, tmp_path):
    root, site = local_remote(service, tmp_path)
    other_root, other_site = local_remote(service, tmp_path, 'second')
    local = tmp_path / 'payload'
    local.write_bytes(b'abc')
    with CloudConnection(site, service) as source, CloudConnection(other_site, service) as destination:
        source.transfer(local, '/payload', True, Event(), lambda *_: None)
        relay(source, destination, '/payload', '/copied', Event())
        assert (other_root / 'copied').read_bytes() == b'abc'
        with pytest.raises(ValueError, match='resume'):
            destination.transfer(local, '/copied', True, Event(), lambda *_: None, True)
        (root / 'folder').mkdir()
        (root / 'folder/nested').mkdir()
        (root / 'folder/nested/data').write_bytes(b'nested')
        download = tmp_path / 'download'
        transfer_tree(source, None, '/folder', str(download), True, Event(), lambda *_: None)
        assert (download / 'nested/data').read_bytes() == b'nested'
        cancel = Event()
        cancel.set()
        with pytest.raises(Cancelled):
            destination.transfer(local, '/cancelled', True, cancel, lambda *_: None)
        assert not (other_root / 'cancelled').exists()
        with pytest.raises(ValueError):
            source.rename('/payload', '/folder')
        for invalid in ('/../outside', '/x/../../y', '/x\\y', '/x\nname'):
            with pytest.raises(ValueError):
                cloud_path(invalid)


def test_cloud_failed_download_preserves_target(service, tmp_path):
    _, site = local_remote(service, tmp_path)
    target = tmp_path / 'existing'
    target.write_bytes(b'keep')
    with CloudConnection(site, service) as connection:
        with pytest.raises(OSError):
            connection.transfer(target, '/missing', False, Event(), lambda *_: None)
    assert target.read_bytes() == b'keep'


def test_real_webdav(service, tmp_path):
    root = tmp_path / 'webdav'
    root.mkdir()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    process = subprocess.Popen([str(executable()), 'serve', 'webdav', str(root), '--addr', f'127.0.0.1:{port}'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.1):
                    break
            except OSError:
                time.sleep(.05)
        identity = uuid4().hex
        service.call('config/create', {'name': 'c' + identity, 'type': 'webdav',
            'parameters': {'url': f'http://127.0.0.1:{port}/', 'vendor': 'other'}, 'opt': {'nonInteractive': True}})
        service.save_account(identity, 'WebDAV test', 'WebDAV')
        site = Site(name='WebDAV test', host='cloud-' + identity, protocol='WebDAV', cloud_account=identity)
        source = tmp_path / 'upload'
        source.write_bytes(b'actual WebDAV transport')
        with CloudConnection(site, service) as connection:
            connection.transfer(source, '/upload', True, Event(), lambda *_: None)
            assert connection.list('/')[0].name == 'upload'
            assert (root / 'upload').read_bytes() == source.read_bytes()
            connection.transfer(tmp_path / 'retrieved', '/upload', False, Event(), lambda *_: None)
            assert (tmp_path / 'retrieved').read_bytes() == source.read_bytes()
    finally:
        process.terminate()
        process.wait(5)


def test_setup_question_protocol(service):
    result = service.call('config/create', {'name': 'wizard', 'type': 'webdav', 'parameters': {},
        'opt': {'nonInteractive': True, 'all': True}})
    assert result['State'] and result['Option']['Name'] == 'url'
    result = service.call('config/update', {'name': 'wizard', 'parameters': {}, 'opt': {
        'nonInteractive': True, 'continue': True, 'state': result['State'], 'result': 'https://example.invalid/'}})
    assert result['Option']['Name'] == 'vendor'


def test_active_cancellation_preserves_existing_download(service, tmp_path, monkeypatch):
    root, site = local_remote(service, tmp_path)
    (root / 'large').write_bytes(b'x' * 1024 * 1024)
    target = tmp_path / 'target'
    target.write_bytes(b'keep original')
    cancel = Event()
    with CloudConnection(site, service, bandwidth=(16384, 16384)) as connection:
        # Delay status observation of the real asynchronous copy so cancellation is
        # deterministic even when rclone uses a fast local server-side copy.
        request = service.call
        jobs = []
        def delayed(endpoint, parameters=None, timeout=30):
            result = request(endpoint, parameters, timeout)
            if endpoint == 'operations/copyfile':
                jobs.append(result['jobid'])
                cancel.set()
            return result
        monkeypatch.setattr(service, 'call', delayed)
        timer = Timer(.5, cancel.set)
        timer.start()
        try:
            with pytest.raises(Cancelled):
                connection.transfer(target, '/large', False, cancel, lambda *_: None)
        finally:
            timer.cancel()
    assert target.read_bytes() == b'keep original'
    assert service.process.poll() is None
    assert jobs and service.call('job/status', {'jobid': jobs[0]})['finished']


def test_restored_cloud_jobs_do_not_prompt_for_ftp_password(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    identity = uuid4().hex
    site = Site(name='cloud', host='cloud-' + identity, protocol='WebDAV', cloud_account=identity, username='')
    job = dict(needs_credentials=True, state='queued', source_site=site, destination_site=None)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: pytest.fail('Unexpected FTP password prompt'))
    assert window.prepare_restored_jobs([job])
    assert job['source_password'] == '' and not job['needs_credentials']


def test_cloud_site_selection_and_command_guards(window, tmp_path):
    identity = uuid4().hex
    window.cloud_service.save_account(identity, 'Storage', 'Amazon S3')
    dialog = SiteDialog(window, {})
    dialog.protocol.setCurrentText('Amazon S3')
    dialog.cloud_account.setCurrentIndex(dialog.cloud_account.findData(identity))
    site = dialog.site()
    assert site.cloud_account == identity and site.host == 'cloud-' + identity
    assert not dialog.password.isEnabled() and not dialog.managed_key.isEnabled()
    dialog.load(asdict(site))
    assert dialog.site().cloud_account == identity
    pane = window.current_panes()[0]
    pane.set_remote(site, '', '/')
    window.command_commands.update()
    assert not window.command_commands.raw_action.isEnabled()
    window.ftp_commands.update()
    assert not window.ftp_commands.actions['Attributes(CHMOD)'].isEnabled()
    assert isinstance(window.new_connection(site, ''), CloudConnection)
    assert window.retry_limit({'route': 'Relay'}) == 0
    dialog.close()


@pytest.mark.parametrize('provider,drive_type', [('Microsoft OneDrive', 'personal'),
    ('Microsoft OneDrive for Business', 'business'), ('Microsoft SharePoint Online', 'documentLibrary')])
def test_microsoft_account_classification(tmp_path, monkeypatch, provider, drive_type):
    service = CloudService(tmp_path / 'accounts', CredentialVault(tmp_path / 'credentials.enc'))
    monkeypatch.setattr(service, 'call', lambda *a, **k: {'type': 'onedrive', 'drive_type': drive_type})
    identity = uuid4().hex
    service.finish_account(identity, 'Microsoft account', provider)
    assert service.accounts()[identity]['provider'] == provider
    wrong = 'Microsoft OneDrive' if provider != 'Microsoft OneDrive' else 'Microsoft SharePoint Online'
    with pytest.raises(ValueError, match='does not match'):
        service.finish_account(uuid4().hex, 'Wrong type', wrong)


def test_plaintext_cloud_config_is_refused(tmp_path):
    vault = CredentialVault(tmp_path / 'vault.enc')
    vault.set('cloud-config-password', 'random test password')
    service = CloudService(tmp_path, vault)
    service.config_path.write_text('[unprotected]\ntype=webdav\n')
    with pytest.raises(RuntimeError, match='must be encrypted'):
        service.start()
    assert service.process is None


def test_guided_webdav_setup(window, service):
    window.cloud_service = service
    wizard = CloudAccountWizard(window, provider='WebDAV')
    wizard.name.setText('Guided WebDAV')
    wizard.show()
    wizard.advance()
    settle(window)
    for _ in range(30):
        if wizard.completed:
            break
        if wizard.answer.accessibleName() == 'url':
            wizard.answer.setText('https://example.invalid/')
        wizard.advance()
        settle(window)
    assert wizard.completed
    assert service.accounts()[wizard.identity]['provider'] == 'WebDAV'
    wizard.close()
    assert 'c' + wizard.identity in service.call('config/listremotes')['remotes']


@pytest.mark.parametrize('direction', ['to-cloud', 'from-cloud'])
def test_ftp_cloud_relay(servers, service, tmp_path, direction):
    from fluxftp.core import Connection
    ftp_root, ftp_site = servers(tls=True)
    cloud_root, cloud_site = local_remote(service, tmp_path)
    (ftp_root / 'input').write_bytes(b'FTP source')
    (cloud_root / 'input').write_bytes(b'cloud source')
    with Connection(ftp_site, 'password') as ftp, CloudConnection(cloud_site, service) as cloud:
        source, destination = (ftp, cloud) if direction == 'to-cloud' else (cloud, ftp)
        relay(source, destination, '/input', '/output', Event())
    assert (cloud_root / 'output').read_bytes() == b'FTP source' if direction == 'to-cloud' else (ftp_root / 'output').read_bytes() == b'cloud source'


def test_sftp_cloud_relay(ssh_server, service, tmp_path):
    from fluxftp.core import Connection
    root, site = ssh_server
    cloud_root, cloud_site = local_remote(service, tmp_path)
    (root / 'input').write_bytes(b'SFTP source')
    with Connection(site, 'password') as sftp, CloudConnection(cloud_site, service) as cloud:
        relay(sftp, cloud, '/input', '/output', Event())
    assert (cloud_root / 'output').read_bytes() == b'SFTP source'
