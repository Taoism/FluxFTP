import threading
from dataclasses import replace
import pytest
from fluxftp.core import Connection, transfer_tree, fxp, Cancelled
from fluxftp.transfer_options import TransferOptions
from test_integration import servers
from test_sftp import ssh_server


@pytest.mark.parametrize('direction', ['ssh-ssh', 'ssh-ftp', 'ftp-ssh', 'ssh-tls', 'tls-ssh'])
@pytest.mark.parametrize('resume', [False, True])
def test_relay_bytes_resume(ssh_server, servers, direction, resume):
    sshroot, sshsite = ssh_server
    ftproot, ftpsite = servers(tls='tls' in direction)
    roots = [sshroot if side == 'ssh' else ftproot for side in direction.split('-')]
    sites = [sshsite if side == 'ssh' else ftpsite for side in direction.split('-')]
    data = bytes(range(256)) * 300
    (roots[0] / 'source').write_bytes(data)
    (roots[1] / 'target').write_bytes(data[:12345] if resume else b'old')
    events = []
    with Connection(sites[0], 'password') as source, Connection(sites[1], 'password') as destination:
        transfer_tree(source, destination, '/source', '/target', False, threading.Event(),
                      lambda *args: events.append(args), resume, options=TransferOptions())
    assert (roots[1] / 'target').read_bytes() == data
    assert events[-1] == (len(data) * 2, len(data) * 2)


def test_relay_cancel_before_upload(ssh_server):
    root, site = ssh_server
    (root / 'source').write_bytes(b'x' * 50000)
    (root / 'target').write_bytes(b'original')
    cancel = threading.Event()
    with Connection(site, 'password') as source, Connection(site, 'password') as destination:
        with pytest.raises(Cancelled):
            transfer_tree(source, destination, '/source', '/target', False, cancel,
                          lambda *_: cancel.set(), options=TransferOptions())
    assert (root / 'target').read_bytes() == b'original'


@pytest.mark.parametrize('method', ['SSCN', 'CPSV'])
@pytest.mark.parametrize('resume', [False, True])
def test_protected_fxp_real_bytes(servers, method, resume):
    aroot, asite = servers(tls=True, fxp_tls='SSCN' if method == 'SSCN' else None)
    broot, bsite = servers(tls=True, fxp_tls='CPSV' if method == 'CPSV' else None)
    data = bytes(range(256)) * 500
    (aroot / 'source').write_bytes(data)
    if resume: (broot / 'target').write_bytes(data[:12345])
    with Connection(asite, 'password') as source, Connection(bsite, 'password') as destination:
        source.ftp.sock.settimeout(5)
        destination.ftp.sock.settimeout(5)
        fxp(source, destination, '/source', '/target', resume=resume)
    assert (broot / 'target').read_bytes() == data


def test_fxp_destination_waits_for_source_before_preliminary_reply(servers, monkeypatch):
    """Model servers that withhold STOR's 150 until the sender connects."""
    aroot, asite = servers(tls=True, fxp_tls='SSCN')
    broot, bsite = servers(tls=True)
    payload = b'late preliminary reply' * 4096
    (aroot / 'source').write_bytes(payload)
    with Connection(asite, 'password') as source, Connection(bsite, 'password') as destination:
        started = threading.Event()
        pending = [False]
        source_put = source.ftp.putcmd
        destination_put = destination.ftp.putcmd
        destination_reply = destination.ftp.getresp
        def send_source(command):
            source_put(command)
            if command.startswith('RETR '):
                started.set()
        def send_destination(command):
            destination_put(command)
            if command.startswith('STOR '):
                pending[0] = True
        def reply():
            if pending[0]:
                if not started.wait(.2):
                    raise TimeoutError('Destination is waiting for the source data connection')
                pending[0] = False
            return destination_reply()
        monkeypatch.setattr(source.ftp, 'putcmd', send_source)
        monkeypatch.setattr(destination.ftp, 'putcmd', send_destination)
        monkeypatch.setattr(destination.ftp, 'getresp', reply)
        fxp(source, destination, '/source', '/target')
    assert (broot / 'target').read_bytes() == payload


@pytest.mark.parametrize('tls', [False, True])
def test_fxp_cancel_interrupts_blocked_control_read(servers, tls):
    import time
    aroot, asite = servers(tls=tls, fxp_tls='SSCN' if tls else None, stall_retr=True)
    _, bsite = servers(tls=tls)
    (aroot / 'source').write_bytes(b'x' * 50000)
    cancel = threading.Event()
    with Connection(asite, 'password') as source, Connection(bsite, 'password') as destination:
        timer = threading.Timer(.4, cancel.set)
        timer.start()
        start = time.monotonic()
        try:
            with pytest.raises(Cancelled):
                fxp(source, destination, '/source', '/target', cancel=cancel)
            assert time.monotonic() - start < 3
            assert source.ftp.sock is None and destination.ftp.sock is None
        finally:
            timer.cancel()


@pytest.mark.parametrize('ssh_server', [True], indirect=True)
def test_relay_atomic_upload_preserves_conflict_path(ssh_server):
    root, site = ssh_server
    data = b'new payload' * 5000
    (root / 'source').write_bytes(data)
    (root / 'target').write_bytes(b'old')
    options = TransferOptions(atomic_upload=True)
    with Connection(site, 'password') as source, Connection(site, 'password') as destination:
        source.transfer_options = destination.transfer_options = options
        transfer_tree(source, destination, '/source', '/target', False, threading.Event(), lambda *_: None, options=options)
    assert (root / 'target').read_bytes() == data
    assert not list(root.glob('*.flux*'))
