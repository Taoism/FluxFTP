from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import ftplib
import subprocess
import sys

import pytest

from fluxftp.protocol_adapter import (ProtocolAdapter, adapter_for, capabilities_for,
    connection_capabilities, transfer_route, supports_resume, supports_payload_pause, HashSupport)


def site(protocol, **kwargs):
    return SimpleNamespace(protocol=protocol, cloud_account='', **kwargs)


@pytest.mark.parametrize('protocol,pause,resume,fxp,hash_support', [
    ('FTP · Unencrypted', True, True, True, HashSupport.NEGOTIATED),
    ('FTPS · AUTH TLS', True, True, True, HashSupport.NEGOTIATED),
    ('FTPS · AUTH SSL', True, True, True, HashSupport.NEGOTIATED),
    ('FTPS · Implicit TLS', True, True, True, HashSupport.NEGOTIATED),
    ('SFTP · SSH', True, True, False, HashSupport.NEGOTIATED),
    ('SCP · SSH', True, False, False, HashSupport.NEGOTIATED),
    ('Google Drive', False, False, False, HashSupport.NONE),
    ('unregistered backend', False, False, False, HashSupport.NONE),
])
def test_backend_capabilities_are_conservative(protocol, pause, resume, fxp, hash_support):
    caps = capabilities_for(site(protocol))
    assert (caps.payload_pause, caps.resume_download, caps.resume_upload, caps.direct_fxp, caps.native_hash) == (pause, resume, resume, fxp, hash_support)
    with pytest.raises(FrozenInstanceError): caps.payload_pause = True


def test_all_cloud_providers_and_local_capabilities():
    from fluxftp.cloud import CLOUD_PROTOCOLS
    for protocol in CLOUD_PROTOCOLS:
        caps = capabilities_for(site(protocol))
        assert caps.backend == 'cloud' and not caps.payload_pause and not caps.resume_upload
    assert capabilities_for(None).native_hash == HashSupport.LOCAL
    assert supports_resume(None, None)


def test_contract_import_does_not_load_desktop_or_qt():
    code = "from fluxftp.protocol_adapter import capabilities_for; import sys; from types import SimpleNamespace; capabilities_for(SimpleNamespace(protocol='Google Drive')); assert 'fluxftp.app' not in sys.modules; assert not any(n.startswith('PySide') for n in sys.modules)"
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('source', ['FTP · Unencrypted', 'SFTP · SSH', 'SCP · SSH', 'Amazon S3'])
@pytest.mark.parametrize('destination', ['FTPS · AUTH TLS', 'SFTP · SSH', 'SCP · SSH', 'Google Drive'])
def test_route_and_pause_depend_on_both_endpoints(source, destination):
    a, b = site(source), site(destination)
    route = transfer_route(a, b)
    direct = source == 'FTP · Unencrypted' and destination == 'FTPS · AUTH TLS'
    assert route == ('FXP' if direct else 'Relay')
    assert supports_payload_pause(dict(source_site=a, destination_site=b, route=route)) == (not direct and source != 'Amazon S3' and destination != 'Google Drive')
    expected_resume = source not in ('SCP · SSH', 'Amazon S3') and destination not in ('SCP · SSH', 'Google Drive')
    assert supports_resume(SimpleNamespace(site=a), SimpleNamespace(site=b)) == expected_resume


def test_local_routes_and_queued_holds_are_distinct_from_payload_pause():
    from fluxftp.transfer_control import can_pause
    cloud = site('Google Drive')
    assert transfer_route(None, None) == 'Copy'
    assert transfer_route(None, cloud) == 'Upload'
    assert transfer_route(cloud, None) == 'Download'
    job = dict(state='queued', destination_site=cloud, route='Upload')
    assert can_pause(job)
    job['state'] = 'active'
    assert not can_pause(job)
    job['destination_site'] = site('SFTP · SSH')
    assert can_pause(job)
    job['deployment'] = {'id': 'guarded'}
    assert not can_pause(job)


def test_adapter_delegates_operations_and_preserves_cancellation_and_errors():
    calls = []
    class Backend:
        site = site('SFTP · SSH')
        def list(self, path): calls.append(('list', path)); return ['entry']
        def size(self, path): calls.append(('size', path)); return 12
        def mkdir(self, path): calls.append(('mkdir', path))
        def rename(self, old, new): calls.append(('rename', old, new))
        def remove(self, path, is_dir=False): calls.append(('remove', path, is_dir))
        def transfer(self, *args): calls.append(('transfer', *args)); return 'done'
    backend = Backend()
    adapter = adapter_for(backend)
    assert isinstance(adapter, ProtocolAdapter)
    assert adapter.list('/a') == ['entry'] and adapter.size('/a') == 12
    adapter.mkdir('/b'); adapter.rename('/a', '/b'); adapter.remove('/b', is_dir=True)
    cancel, progress = Event(), lambda *_: None
    assert adapter.transfer('local', '/remote', True, cancel, progress, True) == 'done'
    assert calls[-1] == ('transfer', 'local', '/remote', True, cancel, progress, True)
    assert calls[:-1] == [('list', '/a'), ('size', '/a'), ('mkdir', '/b'), ('rename', '/a', '/b'), ('remove', '/b', True)]
    error = OSError('backend failure')
    def fail(*_): raise error
    backend.size = fail
    with pytest.raises(OSError) as raised: adapter.size('/a')
    assert raised.value is error


def test_scp_resume_is_rejected_before_backend_writes():
    calls = []
    backend = SimpleNamespace(site=site('SCP · SSH'), transfer=lambda *a: calls.append(a))
    adapter = adapter_for(backend)
    with pytest.raises(ValueError, match='byte-offset resume'):
        adapter.transfer('local', '/remote', True, Event(), lambda *_: None, True)
    assert not calls
    adapter.transfer('local', '/remote', True, Event(), lambda *_: None, False)
    assert len(calls) == 1


def test_native_hashes_require_negotiation_and_cloud_rejects_native_digest():
    from fluxftp.transfer_reliability import UnsupportedHash
    class FTP:
        def sendcmd(self, command):
            if command == 'FEAT': return '211-Features\n HASH SHA-256*;MD5;UNSUPPORTED;\n211 End'
    backend = SimpleNamespace(site=site('FTPS · AUTH TLS'), ftp=FTP(), sftp=None)
    assert adapter_for(backend).hash_algorithms() == {'SHA-256', 'MD5'}
    class NoHash:
        def sendcmd(self, command): raise ftplib.error_perm('502 Not supported')
    assert adapter_for(SimpleNamespace(ftp=NoHash(), sftp=None)).hash_algorithms() == set()
    cloud = adapter_for(SimpleNamespace(site=site('Google Drive'), is_cloud=True))
    assert not cloud.hash_algorithms()
    with pytest.raises(UnsupportedHash): cloud.digest('/file', 'MD5', Event())


def test_sftp_hash_candidates_do_not_claim_extension_is_available():
    from fluxftp.transfer_reliability import UnsupportedHash
    class SFTP:
        def open(self, *_): raise OSError('Operation unsupported')
    adapter = adapter_for(SimpleNamespace(site=site('SFTP · SSH'), sftp=SFTP()))
    assert adapter.capabilities.native_hash == HashSupport.NEGOTIATED
    assert adapter.hash_algorithms() == {'SHA-1', 'MD5'}
    with pytest.raises(UnsupportedHash): adapter.digest('/file', 'MD5', Event())


@pytest.mark.parametrize('protocol', ['SCP · SSH', 'Google Drive'])
def test_conflict_dialog_does_not_offer_unsupported_download_resume(tmp_path, protocol):
    from fluxftp.file_exists import transfer_existing
    from fluxftp.transfer_options import TransferOptions
    target = tmp_path/'target'
    target.write_bytes(b'old')
    connection = SimpleNamespace(site=site(protocol), size=lambda _: 6)
    seen = []
    def choose(details): seen.append(details); return 'Auto Skip'
    result = transfer_existing(connection, None, '/source', str(target), Event(), lambda *_: None,
        False, TransferOptions(conflict_prompt=True), None, None, choose)
    assert not result and not seen[0]['can_resume']
    assert target.read_bytes() == b'old' and not (tmp_path/'target.fluxpart').exists()


def test_direct_fxp_rejects_ssh_before_network_activity():
    from fluxftp.core import fxp
    backend = SimpleNamespace(site=site('SFTP · SSH'))
    with pytest.raises(ValueError, match='two FTP/FTPS'):
        fxp(backend, backend, '/a', '/b')
