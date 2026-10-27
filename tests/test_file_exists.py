import ftplib
import os
from dataclasses import replace
from threading import Event
from pathlib import Path

import pytest

from fluxftp.core import Connection, Cancelled, transfer_tree
from fluxftp.transfer_options import TransferOptions
from test_integration import servers

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.mark.parametrize('route', ['upload', 'download', 'fxp'])
@pytest.mark.parametrize('relation', ['smaller', 'same', 'larger'])
def test_default_conflict_actions(servers, tmp_path, route, relation):
    from contextlib import ExitStack
    payload = b'file contents' * 5000
    existing = payload[:30000] if relation == 'smaller' else b'x' * (len(payload) + (10 if relation == 'larger' else 0))
    options = TransferOptions(exists_enabled=True, rollback_kib=0)
    with ExitStack() as stack:
        if route == 'upload':
            source = None
            src = str(tmp_path / 'source.bin')
            root, site = servers()
            destination = stack.enter_context(Connection(site, 'password', transfer_options=options))
            dst = '/target.bin'
            target = root / 'target.bin'
            Path(src).write_bytes(payload)
        else:
            root, site = servers()
            (root / 'source.bin').write_bytes(payload)
            source = stack.enter_context(Connection(site, 'password', transfer_options=options))
            src = '/source.bin'
            if route == 'fxp':
                target_root, target_site = servers()
                destination = stack.enter_context(Connection(target_site, 'password', transfer_options=options))
                target = target_root / 'target.bin'
                dst = '/target.bin'
            else:
                destination = None
                target = tmp_path / 'target.bin'
                dst = str(target)
        target.write_bytes(existing)
        skipped, renamed = [], []
        result = transfer_tree(source, destination, src, dst, False, Event(), lambda *_: None,
                               options=options, skipped=lambda *args: skipped.append(args),
                               renamed=lambda *args: renamed.append(args))
    if relation == 'smaller':
        assert target.read_bytes() == payload
        assert result is True
    elif relation == 'same':
        assert target.read_bytes() == existing and result is False and skipped
    else:
        assert target.read_bytes() == existing
        assert target.with_name('target (1).bin').read_bytes() == payload and renamed


@pytest.mark.parametrize('fallback', [True, False])
def test_rejected_upload_resume_overwrite_fallback(servers, tmp_path, monkeypatch, fallback):
    root, site = servers()
    src = tmp_path / 'source'
    src.write_bytes(b'abcdef')
    (root / 'target').write_bytes(b'abc')
    options = TransferOptions(exists_enabled=True, overwrite_resume_failed=fallback)
    with Connection(site, 'password', transfer_options=options) as connection:
        original = connection._transfer_once
        calls = []
        def reject_resume(local, remote, upload, cancel, progress, resume):
            calls.append(resume)
            if resume: raise ftplib.error_perm('502 REST unsupported')
            return original(local, remote, upload, cancel, progress, resume)
        monkeypatch.setattr(connection, '_transfer_once', reject_resume)
        def run():
            return transfer_tree(None, connection, str(src), '/target', False, Event(), lambda *_: None, options=options)
        if fallback: run()
        else:
            with pytest.raises(ftplib.error_perm): run()
    assert calls == ([True, False] if fallback else [True])
    assert (root / 'target').read_bytes() == (b'abcdef' if fallback else b'abc')


def test_rename_avoids_existing_numbered_file_and_partial(tmp_path):
    src, dst = tmp_path / 'source', tmp_path / 'file.bin'
    src.write_bytes(b'new')
    dst.write_bytes(b'larger')
    dst.with_name('file (1).bin').write_bytes(b'keep')
    dst.with_name('file (2).bin.fluxpart').write_bytes(b'partial')
    transfer_tree(None, None, str(src), str(dst), False, Event(), lambda *_: None,
                  options=TransferOptions(exists_enabled=True))
    assert dst.with_name('file (3).bin').read_bytes() == b'new'
    assert dst.read_bytes() == b'larger'


def test_cancelled_resume_never_overwrites(servers, tmp_path, monkeypatch):
    root, site = servers()
    src = tmp_path / 'source'
    src.write_bytes(b'abcdef')
    (root / 'target').write_bytes(b'abc')
    options = TransferOptions(exists_enabled=True)
    with Connection(site, 'password', transfer_options=options) as connection:
        calls = []
        def cancelled(*args):
            calls.append(True)
            raise Cancelled('Cancelled')
        monkeypatch.setattr(connection, '_transfer_once', cancelled)
        with pytest.raises(Cancelled):
            transfer_tree(None, connection, str(src), '/target', False, Event(), lambda *_: None, options=options)
    assert calls == [True] and (root / 'target').read_bytes() == b'abc'


def test_download_resume_failure_preserves_target(servers, tmp_path, monkeypatch):
    root, site = servers()
    (root / 'source').write_bytes(b'abcdef')
    target = tmp_path / 'target'
    target.write_bytes(b'abc')
    options = TransferOptions(exists_enabled=True, overwrite_resume_failed=False)
    with Connection(site, 'password', transfer_options=options) as connection:
        def fail(*_): raise OSError('Download failed')
        monkeypatch.setattr(connection, '_transfer_once', fail)
        with pytest.raises(OSError):
            transfer_tree(connection, None, '/source', str(target), False, Event(), lambda *_: None, options=options)
    assert target.read_bytes() == b'abc'


def test_file_exists_ui_persistence(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from fluxftp import app as module
    from fluxftp.preferences import PreferencesDialog
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'exists.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(module, 'settings_store', lambda: settings)
    window = module.Window()
    dialog = PreferencesDialog(window)
    assert len(dialog.exists_actions) == 9
    assert dialog.overwrite_resume_failed.isChecked()
    for (route, relation), combo in dialog.exists_actions.items():
        assert combo.count() == 5 and combo.findText('Ask') >= 0
        assert combo.currentText() == {'smaller': 'Auto Resume', 'same': 'Auto Skip', 'larger': 'Auto Rename'}[relation]
        combo.setCurrentText('Auto Overwrite')
    dialog.overwrite_resume_failed.setChecked(False)
    options = TransferOptions.from_settings(settings)
    assert options.exists_enabled and not options.overwrite_resume_failed
    assert all(getattr(options, f'{route}_{relation}') == 'Auto Overwrite' for route, relation in dialog.exists_actions)
    dialog.close()
    window.close()


@pytest.mark.parametrize('action', ['Auto Resume', 'Auto Skip', 'Auto Rename', 'Auto Overwrite'])
@pytest.mark.parametrize('old', [b'ab', b'abcdef', b'abcdefghi'])
def test_all_actions_for_all_size_relations(tmp_path, action, old):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_bytes(b'abcdef')
    target.write_bytes(old)
    options = TransferOptions(exists_enabled=True, download_smaller=action, download_same=action, download_larger=action)
    transfer_tree(None, None, str(source), str(target), False, Event(), lambda *_: None, options=options)
    assert target.read_bytes() == (old if action in ('Auto Skip', 'Auto Rename') else b'abcdef')
    if action == 'Auto Rename':
        assert target.with_name('target (1)').read_bytes() == b'abcdef'


def test_recursive_conflicts_do_not_reapply_manual_skip(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'file.log').write_bytes(b'abcdef')
    (target / 'file.log').write_bytes(b'ab')
    options = TransferOptions(exists_enabled=True, skip_enabled=True, skip_mode='Manual Transfer')
    transfer_tree(None, None, str(source), str(target), True, Event(), lambda *_: None, options=options)
    assert (target / 'file.log').read_bytes() == b'abcdef'
