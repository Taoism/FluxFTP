import os
import ftplib
import zlib
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from fluxftp.core import Connection, Entry, transfer_tree
from fluxftp.crc_check import CRCMismatch, parse_sfv, sfv_names, record_status, expected_crc
from fluxftp.transfer_options import TransferOptions, MISC_DEFAULTS
from fluxftp.preferences import PreferencesDialog
from test_integration import servers
from test_queue import window, job_for
from test_ui import settle


def test_misc_preferences(window):
    dialog = PreferencesDialog(window)
    for key, value in MISC_DEFAULTS.items():
        if key != 'crc_retries': assert dialog.misc_checks[key].isChecked() == value
    dialog.crc_retries.setValue(2)
    dialog.misc_checks['crc_keep_bad'].setChecked(True)
    options = TransferOptions.from_settings(window.preferences)
    assert options.crc_retries == 2 and options.crc_keep_bad and options.crc_auto
    dialog.close()


def test_new_tabs_and_quick_connect(window, monkeypatch):
    from fluxftp import app as module
    pane = window.current_panes()[0]
    pane.change_mode('Local')
    window.add_workspace()
    assert window.current_panes()[0].mode == 'Local'
    window.preferences.setValue('Misc/new_tab_remote', True)
    window.add_workspace()
    assert all(p.mode == 'Remote' for p in window.current_panes())
    window.preferences.setValue('Misc/quick_new_tab', True)
    window.quick_host.setText('example.test')
    window.quick_password.setText('secret')
    monkeypatch.setattr(module.Pane, 'refresh', lambda *_: None)
    count = window.tabs.count()
    window.quick_connect()
    assert window.tabs.count() == count + 1
    connected = [p for p in window.current_panes() if p.site]
    assert len(connected) == 1 and connected[0].site.host == 'example.test'
    assert connected[0].password == 'secret'


def test_enter_created_remote_folder(window, servers, monkeypatch):
    from fluxftp import app as module
    root, site = servers()
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password')
    window.preferences.setValue('Misc/enter_created', True)
    monkeypatch.setattr(module.QInputDialog, 'getText', lambda *_: ('created', True))
    pane.mkdir()
    settle(window)
    assert (root / 'created').is_dir() and pane.path == '/created'


@pytest.mark.parametrize('tls', [False, True])
def test_verified_download_and_timestamp(servers, tmp_path, tls):
    root, site = servers(tls)
    payload = b'verified payload' * 100
    (root / 'data.bin').write_bytes(payload)
    os.utime(root / 'data.bin', (1600000000, 1600000000))
    (root / 'files.sfv').write_text(f'data.bin {zlib.crc32(payload):08X}\n')
    target = tmp_path / 'target.bin'
    with Connection(site, 'password', transfer_options=TransferOptions(crc_auto=True, keep_file_time=True)) as connection:
        connection.transfer(target, '/data.bin', False, Event(), lambda *_: None)
    assert target.read_bytes() == payload
    assert abs(target.stat().st_mtime - 1600000000) < 1
    assert not (tmp_path / '.fluxftp-crc.log').exists()
    assert not (tmp_path / '.fluxftp-crc-status').exists()


@pytest.mark.parametrize('keep', [False, True])
def test_bad_crc_preserves_completed_target(servers, tmp_path, keep):
    root, site = servers()
    (root / 'data.bin').write_bytes(b'bad')
    (root / 'files.sfv').write_text(f'data.bin {zlib.crc32(b"good"):08X}\n')
    target = tmp_path / 'target.bin'
    target.write_bytes(b'existing completed file')
    options = TransferOptions(crc_auto=True, crc_keep_bad=keep)
    with Connection(site, 'password', transfer_options=options) as connection:
        with pytest.raises(CRCMismatch):
            connection.transfer(target, '/data.bin', False, Event(), lambda *_: None)
    assert target.read_bytes() == b'existing completed file'
    assert target.with_name(target.name + '.fluxpart').exists() == keep
    assert 'Mismatch' in (tmp_path / '.fluxftp-crc.log').read_text()


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'C:\\outside', 'sub/../../outside'])
def test_manifest_rejects_escape(name):
    with pytest.raises(ValueError): parse_sfv((name + ' 12345678').encode(), '/root')


def test_sfv_precedence_and_first_filename(tmp_path):
    entries = [Entry('b.sfv', False), Entry('A.sfv', False), Entry('data', False)]
    options = TransferOptions(single_sfv=True, skip_multiple_sfv=True)
    assert sfv_names(entries, options) == []
    assert sfv_names(entries, replace(options, skip_multiple_sfv=False)) == ['A.sfv']
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    for entry in entries: (source / entry.name).write_text('test')
    transfer_tree(None, None, str(source), str(target), True, Event(), lambda *_: None, options=options)
    assert {p.name for p in target.iterdir()} == {'data'}


def test_crc_artifacts_cleanup_preserves_foreign_contents(tmp_path):
    target = tmp_path / 'data'
    options = TransferOptions()
    record_status(target, 'Mismatch', options)
    tags = tmp_path / '.fluxftp-crc-status'
    (tags / 'user.txt').write_text('keep')
    record_status(target, 'OK', options)
    assert not (tmp_path / '.fluxftp-crc.log').exists()
    assert (tags / 'user.txt').read_text() == 'keep'
    assert {p.name for p in tags.iterdir()} == {'.owner', 'user.txt'}
    (tags / 'user.txt').unlink()
    record_status(target, 'OK', options)
    assert not tags.exists()
    log = tmp_path / '.fluxftp-crc.log'
    log.write_text('not owned by FluxFTP')
    record_status(target, 'OK', options)
    assert log.read_text() == 'not owned by FluxFTP'


def test_xcrc_and_unsupported_cached():
    calls = []
    def command(cmd):
        calls.append(cmd)
        raise ftplib.error_perm('502 unsupported')
    connection = SimpleNamespace(transfer_options=TransferOptions(crc_xcrc=True), ftp=SimpleNamespace(sendcmd=command))
    assert expected_crc(connection, '/data', Event()) is None
    assert expected_crc(connection, '/other', Event()) is None
    assert calls == ['XCRC /data']
    connection._xcrc_unsupported = False
    connection.ftp.sendcmd = lambda _: '250 1234ABCD'
    assert expected_crc(connection, '/data', Event()) == 0x1234ABCD


def test_empty_folders_and_same_parent(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    (source / 'empty').mkdir()
    options = TransferOptions(no_empty_dirs=True)
    transfer_tree(None, None, str(source), str(target), True, Event(), lambda *_: None, options=options)
    assert not target.exists()
    target.mkdir()
    transfer_tree(None, None, str(source), str(target), True, Event(), lambda *_: None, options=options)
    assert target.is_dir() and not list(target.iterdir())
    with pytest.raises(ValueError, match='same name'):
        transfer_tree(None, None, str(source), str(target / 'target'), True, Event(), lambda *_: None,
                      options=TransferOptions(deny_parent_name=True))


def test_remove_only_empty_targets_without_source(servers, tmp_path):
    root, site = servers()
    target = root / 'target'
    target.mkdir()
    for name, content in [('missing', b''), ('present', b''), ('nonempty', b'keep')]:
        (target / name).write_bytes(content)
    (target / 'directory').mkdir()
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'present').write_bytes(b'')
    options = TransferOptions(remove_missing_zero=True)
    with Connection(site, 'password', transfer_options=options) as connection:
        transfer_tree(None, connection, str(source), '/target', True, Event(), lambda *_: None, options=options)
    assert not (target / 'missing').exists()
    assert (target / 'present').exists() and (target / 'nonempty').read_bytes() == b'keep'
    assert (target / 'directory').is_dir()


@pytest.mark.parametrize('repair', [False, True])
def test_crc_queue_retries_only_bad_file(window, servers, tmp_path, monkeypatch, repair):
    window.initialize_queue_storage(tmp_path / 'crc-queue.json')
    root, site = servers()
    folder = root / 'folder'
    folder.mkdir()
    (folder / 'bad.bin').write_bytes(b'bad')
    (folder / 'good.bin').write_bytes(b'good')
    checksum = zlib.crc32(b'good')
    (folder / 'files.sfv').write_text(f'bad.bin {checksum:08X}\ngood.bin {checksum:08X}\n')
    options = TransferOptions(crc_auto=True, crc_retries=2)
    connections = []
    def connect(*args):
        if any(j.get('crc_job') for j in window.queue_jobs) and repair:
            (folder / 'bad.bin').write_bytes(b'good')
        connections.append(True)
        return Connection(site, 'password', transfer_options=options)
    monkeypatch.setattr(window, 'new_connection', connect)
    target = tmp_path / 'downloads'
    job = job_for(window, None)
    job.update(source_site=site, source_password='password', src='/folder', dst=str(target),
               directory=True, resume=False, name='folder', options=options)
    job['task'] = window.make_queue_task(job)
    window.start_next()
    import time
    from PySide6.QtTest import QTest
    deadline = time.monotonic() + 40
    while window.workers or window.active or window.pending:
        QTest.qWait(10)
        assert time.monotonic() < deadline, 'CRC queue timed out'
    assert (target / 'good.bin').read_bytes() == b'good'
    retries = [j for j in window.queue_jobs if j.get('crc_job')]
    assert len(retries) == 1 and retries[0]['src'] == '/folder/bad.bin'
    assert retries[0]['state'] == ('complete' if repair else 'failed')
    assert len(connections) == (5 if repair else 6)
    assert window.queue_batch_failed == (not repair)
    if repair:
        assert (target / 'bad.bin').read_bytes() == b'good'
        assert not (target / '.fluxftp-crc.log').exists()
    else:
        window.save_queue_state()
        from fluxftp.queue_manager import read_queue
        saved = read_queue(window.queue_file)
        assert saved[0]['crc_attempt'] == 2 and saved[0]['crc_root'] == '/folder'
