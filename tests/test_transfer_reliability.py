import ftplib
import hashlib
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from fluxftp.core import Connection, Cancelled, transfer_tree
from fluxftp.transfer_options import TransferOptions
from fluxftp.transfer_reliability import ReliabilityError, UnsupportedHash, digest
from test_integration import servers
from test_sftp import ssh_server


def run(src, dst, source=None, destination=None, **kwargs):
    return transfer_tree(source, destination, str(src), str(dst), False, kwargs.pop('cancel', Event()),
                         kwargs.pop('progress', lambda *_: None), **kwargs)


@pytest.mark.parametrize('action', ['Auto Overwrite', 'Auto Rename', 'Auto Resume', 'Auto Skip'])
def test_conflict_decisions(tmp_path, action):
    src, dst = tmp_path / 'source', tmp_path / 'target'
    src.write_bytes(b'abcdef'); dst.write_bytes(b'abc')
    requests = []
    def choose(details):
        requests.append(details)
        return action
    result = run(src, dst, options=TransferOptions(exists_enabled=True, conflict_prompt=True), conflict=choose)
    assert requests[0]['can_resume'] and requests[0]['target_size'] == 3
    assert dst.read_bytes() == (b'abc' if action in ('Auto Rename', 'Auto Skip') else b'abcdef')
    if action == 'Auto Rename': assert (tmp_path / 'target (1)').read_bytes() == b'abcdef'
    assert result == (action != 'Auto Skip')


def test_conflict_changed_or_cancelled_preserves_destination(tmp_path):
    src, dst = tmp_path / 'source', tmp_path / 'target'
    src.write_bytes(b'new'); dst.write_bytes(b'old')
    def change(_):
        dst.write_bytes(b'changed')
        return 'Auto Overwrite'
    with pytest.raises(ReliabilityError, match='changed'):
        run(src, dst, options=TransferOptions(exists_enabled=True, conflict_prompt=True), conflict=change)
    assert dst.read_bytes() == b'changed'
    with pytest.raises(Cancelled):
        run(src, dst, options=TransferOptions(exists_enabled=True, conflict_prompt=True), conflict=lambda _: '')


def test_local_hash_resume_detects_bad_prefix_and_preserves_target(tmp_path):
    src, dst = tmp_path / 'source', tmp_path / 'target'
    src.write_bytes(b'abcdef'); dst.write_bytes(b'bad')
    with pytest.raises(ReliabilityError, match='mismatch'):
        run(src, dst, resume=True, options=TransferOptions(hash_verification='Required'))
    assert dst.read_bytes() == b'bad'
    assert not list(tmp_path.glob('.flux-*'))
    messages = []
    run(src, dst, options=TransferOptions(hash_verification='Required'), notice=messages.append)
    assert dst.read_bytes() == b'abcdef'
    assert any('Hash verified: SHA-256' in m for m in messages)


@pytest.mark.parametrize('tls', [False, True])
def test_atomic_upload_preserves_target_until_rename(servers, tmp_path, tls):
    root, site = servers(tls, atomic_rename=True, hash_support=True)
    src, dst = tmp_path / 'source', root / 'target'
    src.write_bytes(b'new' * 100000); dst.write_bytes(b'old')
    options = TransferOptions(atomic_upload=True, hash_verification='Required')
    with Connection(site, 'password', transfer_options=options) as connection:
        seen = []
        def progress(*_):
            seen.append(True)
            assert dst.read_bytes() == b'old'
        messages = []
        run(src, '/target', destination=connection, options=options, progress=progress, notice=messages.append)
        assert any('Hash verified: SHA-256' in m for m in messages)
    assert seen and dst.read_bytes() == src.read_bytes()
    assert not list(root.glob('.flux-*'))


@pytest.mark.parametrize('failure', ['cancel', 'rename', 'transfer'])
def test_atomic_failure_preserves_target(servers, tmp_path, monkeypatch, failure):
    root, site = servers()
    src, dst = tmp_path / 'source', root / 'target'
    src.write_bytes(b'x' * 500000); dst.write_bytes(b'old')
    cancel = Event()
    options = TransferOptions(atomic_upload=True)
    with Connection(site, 'password', transfer_options=options) as connection:
        if failure == 'rename':
            monkeypatch.setattr(connection, 'rename', lambda *_: (_ for _ in ()).throw(ftplib.error_perm('550 Cannot rename')))
        if failure == 'transfer':
            monkeypatch.setattr(connection, 'transfer', lambda *_: (_ for _ in ()).throw(OSError('broken')))
        with pytest.raises((Cancelled, ReliabilityError, OSError)):
            run(src, '/target', destination=connection, options=options, cancel=cancel,
                progress=lambda *_: cancel.set() if failure == 'cancel' else None)
    assert dst.read_bytes() == b'old'


@pytest.mark.parametrize('policy', ['When available', 'Required'])
def test_unsupported_ftp_hash_is_explicit(servers, tmp_path, policy):
    root, site = servers()
    src = tmp_path / 'source'; src.write_bytes(b'new')
    options = TransferOptions(hash_verification=policy)
    messages = []
    with Connection(site, 'password', transfer_options=options) as connection:
        if policy == 'Required':
            with pytest.raises(UnsupportedHash):
                run(src, '/target', destination=connection, options=options)
            assert not (root / 'target').exists()
        else:
            run(src, '/target', destination=connection, options=options, notice=messages.append)
            assert any('Hash not verified' in m for m in messages)


@pytest.mark.parametrize('bad', [False, True])
@pytest.mark.parametrize('multiline', [False, True])
def test_ftp_hash_checks_complete_reply_range(bad, multiline):
    class FTP:
        def voidcmd(self, _): pass
        def sendcmd(self, command):
            if command.startswith('OPTS'): return '200 OK'
            return ('213-Calculating hash\n' if multiline else '') + '213 SHA-256 0-' + ('3' if bad else '5') + ' ' + hashlib.sha256(b'abcdef').hexdigest() + ' path with spaces'
    connection = SimpleNamespace(ftp=FTP(), sftp=None, size=lambda _: 6)
    if bad:
        with pytest.raises(ReliabilityError, match='complete'):
            digest(connection, '/path', 'SHA-256', Event())
    else:
        assert digest(connection, '/path', 'SHA-256', Event()) == hashlib.sha256(b'abcdef').hexdigest()


def test_sftp_real_hash_and_unsupported_atomic_rename(ssh_server, tmp_path):
    root, site = ssh_server
    src = tmp_path / 'source'; src.write_bytes(b'abcdef' * 1000)
    (root / 'target').write_bytes(b'old')
    options = TransferOptions(hash_verification='Required')
    messages = []
    with Connection(site, 'password', transfer_options=options) as connection:
        run(src, '/hashed', destination=connection, options=options, notice=messages.append)
        assert any('Hash verified: SHA-1' in m for m in messages)
        with pytest.raises(ReliabilityError, match='promotion failed'):
            run(src, '/target', destination=connection, options=replace(options, atomic_upload=True))
    assert (root / 'target').read_bytes() == b'old'
    assert not list(root.glob('.flux-*'))


def test_ascii_required_and_cloud_atomic_fail_before_transfer(tmp_path):
    src = tmp_path / 'source'; src.write_bytes(b'a\n')
    remote = SimpleNamespace(is_cloud=True)
    with pytest.raises(ReliabilityError, match='cloud'):
        run(src, '/target', destination=remote, options=TransferOptions(atomic_upload=True))
    with pytest.raises(ReliabilityError, match='Binary'):
        run(src, '/target', destination=remote, options=TransferOptions(mode='ASCII', hash_verification='Required'))


@pytest.mark.parametrize('ssh_server', [True], indirect=True)
def test_sftp_atomic_replace_with_hash(ssh_server, tmp_path):
    root, site = ssh_server
    src = tmp_path / 'source'; src.write_bytes(b'new' * 1000)
    (root / 'target').write_bytes(b'old')
    options = TransferOptions(atomic_upload=True, hash_verification='Required')
    with Connection(site, 'password', transfer_options=options) as connection:
        run(src, '/target', destination=connection, options=options,
            progress=lambda *_: (root / 'target').read_bytes() == b'old')
    assert (root / 'target').read_bytes() == src.read_bytes()


@pytest.mark.parametrize('tls', [False, True])
def test_verified_download_keeps_original_on_corruption(servers, tmp_path, monkeypatch, tls):
    root, site = servers(tls, hash_support=True)
    (root / 'source').write_bytes(b'new file content')
    target = tmp_path / 'target'; target.write_bytes(b'old')
    options = TransferOptions(hash_verification='Required')
    with Connection(site, 'password', transfer_options=options) as connection:
        original = connection.transfer
        def corrupt(local, *args, **kwargs):
            original(local, *args, **kwargs)
            Path(local).write_bytes(b'BAD file content')
        monkeypatch.setattr(connection, 'transfer', corrupt)
        with pytest.raises(ReliabilityError, match='mismatch'):
            run('/source', target, source=connection, options=options)
        assert target.read_bytes() == b'old'
        monkeypatch.setattr(connection, 'transfer', original)
        run('/source', target, source=connection, options=options)
    assert target.read_bytes() == b'new file content'
    assert not list(tmp_path.glob('.flux-*'))


def test_atomic_hash_failure_never_promotes(servers, tmp_path, monkeypatch):
    root, site = servers(atomic_rename=True, hash_support=True)
    src = tmp_path / 'source'; src.write_bytes(b'new file')
    (root / 'target').write_bytes(b'old')
    options = TransferOptions(atomic_upload=True, hash_verification='Required', exists_enabled=True)
    with Connection(site, 'password', transfer_options=options) as connection:
        original = connection.transfer
        def corrupt(local, remote, *args, **kwargs):
            original(local, remote, *args, **kwargs)
            (root / remote.lstrip('/')).write_bytes(b'BAD file')
        monkeypatch.setattr(connection, 'transfer', corrupt)
        with pytest.raises(ReliabilityError, match='mismatch'):
            run(src, '/target', destination=connection, options=options)
    assert (root / 'target').read_bytes() == b'old' and not list(root.glob('.flux-*'))


def test_verified_fxp(servers):
    from contextlib import ExitStack
    source_root, source_site = servers(hash_support=True)
    target_root, target_site = servers(hash_support=True, atomic_rename=True)
    (source_root / 'source').write_bytes(b'FXP data' * 1000)
    (target_root / 'target').write_bytes(b'old')
    options = TransferOptions(atomic_upload=True, hash_verification='Required')
    messages = []
    with ExitStack() as stack:
        source = stack.enter_context(Connection(source_site, 'password', transfer_options=options))
        destination = stack.enter_context(Connection(target_site, 'password', transfer_options=options))
        run('/source', '/target', source=source, destination=destination, options=options, notice=messages.append)
    assert (source_root / 'source').read_bytes() == (target_root / 'target').read_bytes()
    assert any('Hash verified' in m for m in messages)


@pytest.mark.parametrize('keep', [False, True])
def test_crc_policy_survives_verified_download(servers, tmp_path, keep):
    import zlib
    from fluxftp.crc_check import CRCMismatch
    root, site = servers(hash_support=True)
    (root / 'data.bin').write_bytes(b'bad')
    (root / 'files.sfv').write_text(f'data.bin {zlib.crc32(b"good"):08X}\n')
    target = tmp_path / 'target.bin'; target.write_bytes(b'old')
    options = TransferOptions(hash_verification='Required', crc_auto=True, crc_keep_bad=keep)
    with Connection(site, 'password', transfer_options=options) as connection:
        with pytest.raises(CRCMismatch) as error:
            run('/data.bin', target, source=connection, options=options)
    assert error.value.local == str(target)
    assert target.read_bytes() == b'old'
    assert (tmp_path / 'target.bin.fluxpart').exists() == keep
    assert not list(tmp_path.glob('.flux-*'))


def test_verified_resume_retires_unchanged_partial(tmp_path):
    src, dst = tmp_path / 'source', tmp_path / 'target'
    src.write_bytes(b'abcdef'); dst.write_bytes(b'old')
    partial = tmp_path / 'target.fluxpart'; partial.write_bytes(b'abc')
    run(src, dst, resume=True, options=TransferOptions(hash_verification='Required'))
    assert dst.read_bytes() == b'abcdef' and not partial.exists()
