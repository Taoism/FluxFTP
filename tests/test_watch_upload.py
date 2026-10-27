import json
import time
from dataclasses import asdict
from threading import Event

import pytest
from fluxftp.watch_upload import stable_candidates, excluded, WatchUploads, validate_watch
from fluxftp.folder_sync import scan
from fluxftp.core import Connection
from fluxftp.queue_manager import read_queue
from test_queue import window
from test_integration import servers
from test_ui import settle
from test_folder_sync import wait_idle


def test_stability_resets_on_changes_and_excludes_acknowledged():
    info = dict(directory=False, link=False, size=2, modified='1000')
    observed = {}
    assert not stable_candidates({'a': info}, observed, {}, 10, 2)
    assert not stable_candidates({'a': info}, observed, {}, 11, 2)
    assert stable_candidates({'a': info}, observed, {}, 12, 2) == ['a']
    changed = dict(info, size=3)
    assert not stable_candidates({'a': changed}, observed, {}, 13, 2)
    assert not stable_candidates({'a': changed}, observed, {'a': changed}, 20, 2)
    stable_candidates({}, observed, {}, 21, 2)
    assert not observed
    assert excluded('nested/cache/file', ['cache'])
    assert excluded('nested/File.TMP', ['*.tmp'])
    assert not excluded('nested/file.jpg', ['*.tmp'])


def setup_watch(window, tmp_path, servers, monkeypatch, existing=True, overwrite=True):
    from fluxftp import watch_upload
    root, site = servers()
    local = tmp_path / 'watched'
    local.mkdir()
    window.initialize_queue_storage(tmp_path / 'queue.json')
    controller = window.watch_uploads
    controller.timer.stop()
    assert window.automation.owner
    monkeypatch.setattr(watch_upload, 'saved_connection', lambda *a, **k: Connection(site, 'password'))
    item = validate_watch(dict(id='test', name='Test watch', local=str(local), remote='/', site=site.name,
        settle=2, excludes=['*.tmp'], existing=existing, overwrite=overwrite, auto=False, enabled=True))
    controller.watches['test'] = item
    controller.save()
    return controller, local, root


def poll(controller, window):
    controller.next_scan = 0
    controller.states.setdefault('test', {})['next'] = 0
    controller.tick()
    from PySide6.QtTest import QTest
    deadline = time.monotonic() + 20
    while controller.worker or window.workers or window.active:
        QTest.qWait(10)
        assert time.monotonic() < deadline
    assert controller.worker is None


def mature(controller):
    controller.observations['test'] = {key: (value[0], time.monotonic()-5) for key, value in controller.observations['test'].items()}


def test_watch_upload_queue_persistence_dedup_and_completion(window, tmp_path, servers, monkeypatch):
    controller, local, remote = setup_watch(window, tmp_path, servers, monkeypatch)
    (local / 'nested').mkdir()
    (local / 'nested' / 'photo').write_bytes(b'photo data')
    (local / 'partial.tmp').write_bytes(b'partial')
    poll(controller, window)
    assert not window.pending
    mature(controller)
    poll(controller, window)
    assert len(window.pending) == 1 and window.pending[0]['held']
    saved = read_queue(window.queue_file)
    assert saved[0]['watch']['relative'] == 'nested/photo'
    assert 'password' not in window.queue_file.read_text()
    poll(controller, window)
    assert len(window.pending) == 1
    window.play_queue()
    wait_idle(window)
    assert (remote / 'nested' / 'photo').read_bytes() == b'photo data'
    assert 'nested/photo' in controller.states['test']['acknowledged']
    poll(controller, window)
    assert not window.pending
    restored = WatchUploads(window)
    restored.timer.stop()
    assert restored.states['test']['acknowledged'] == controller.states['test']['acknowledged']
    restored.deleteLater()


def test_watch_ignores_initial_files_but_detects_later_changes(window, tmp_path, servers, monkeypatch):
    controller, local, remote = setup_watch(window, tmp_path, servers, monkeypatch, existing=False)
    file = local / 'file'
    file.write_bytes(b'initial')
    poll(controller, window)
    mature(controller)
    poll(controller, window)
    assert not window.pending
    file.write_bytes(b'changed contents')
    poll(controller, window)
    assert not window.pending
    mature(controller)
    poll(controller, window)
    assert len(window.pending) == 1


def test_watch_preserves_remote_existing_by_default(window, tmp_path, servers, monkeypatch):
    controller, local, remote = setup_watch(window, tmp_path, servers, monkeypatch, overwrite=False)
    (local / 'file').write_bytes(b'local')
    (remote / 'file').write_bytes(b'remote')
    poll(controller, window)
    mature(controller)
    poll(controller, window)
    assert not window.pending and (remote / 'file').read_bytes() == b'remote'


def test_watch_stale_source_pauses_and_keeps_destination(window, tmp_path, servers, monkeypatch):
    controller, local, remote = setup_watch(window, tmp_path, servers, monkeypatch)
    file = local / 'file'
    file.write_bytes(b'initial')
    (remote / 'file').write_bytes(b'keep')
    poll(controller, window)
    mature(controller)
    poll(controller, window)
    file.write_bytes(b'modified before execution')
    window.play_queue()
    wait_idle(window)
    assert (remote / 'file').read_bytes() == b'keep'
    assert not controller.watches['test']['enabled']
    assert 'file' not in controller.states['test']['acknowledged']


def test_corrupt_watch_file_is_preserved(window):
    path = window.watch_uploads.path
    path.write_text('{invalid', encoding='utf-8')
    controller = WatchUploads(window)
    controller.timer.stop()
    assert controller.problem
    with pytest.raises(ValueError): controller.save()
    assert path.read_text() == '{invalid'
    controller.deleteLater()


def test_watch_automatic_start_and_cancel_pause(window, tmp_path, servers, monkeypatch):
    controller, local, remote = setup_watch(window, tmp_path, servers, monkeypatch)
    controller.watches['test']['auto'] = True
    (local / 'file').write_bytes(b'automatic')
    poll(controller, window)
    mature(controller)
    poll(controller, window)
    assert (remote / 'file').read_bytes() == b'automatic'
    assert not window.pending
    controller.watches['test']['auto'] = False
    (local / 'next').write_bytes(b'held')
    poll(controller, window)
    mature(controller)
    poll(controller, window)
    window.stop_queue()
    assert not controller.watches['test']['enabled']
    assert not (remote / 'next').exists()


def test_watch_restored_queue_does_not_duplicate(window, tmp_path, servers, monkeypatch):
    controller, local, remote = setup_watch(window, tmp_path, servers, monkeypatch)
    (local / 'file').write_bytes(b'pending')
    poll(controller, window)
    mature(controller)
    poll(controller, window)
    saved = read_queue(window.queue_file)
    window.pending.clear()
    window.queue_jobs.clear()
    window.queue.clear()
    window.restore_queue_items(saved)
    controller.observations.clear()
    poll(controller, window)
    mature(controller)
    poll(controller, window)
    assert len(window.pending) == 1
    assert window.pending[0]['watch']['relative'] == 'file'


def test_watch_recreated_file_is_detected(window, tmp_path, servers, monkeypatch):
    controller, local, remote = setup_watch(window, tmp_path, servers, monkeypatch, existing=False)
    file = local / 'file'
    file.write_bytes(b'initial')
    poll(controller, window)
    file.unlink()
    poll(controller, window)
    assert 'file' not in controller.states['test']['acknowledged']
    file.write_bytes(b'recreated')
    poll(controller, window)
    mature(controller)
    poll(controller, window)
    assert len(window.pending) == 1
