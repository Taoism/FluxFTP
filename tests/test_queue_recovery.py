from PySide6.QtWidgets import QMessageBox
from PySide6.QtTest import QTest
from fluxftp.queue_manager import write_queue, read_queue
from fluxftp.queue_recovery import LoadQueueDialog
from test_queue import window, descriptor
from test_ui import settle


def saved(window, tmp_path):
    path = tmp_path / 'queue.json'
    jobs = [descriptor(tmp_path, name='first', workspace=0, size=120),
            descriptor(tmp_path, name='folder', workspace=1, directory=True)]
    write_queue(path, jobs)
    window.initialize_queue_storage(path, defer=True)
    return path


def test_deferred_queue_close_and_save_preserve_items(window, tmp_path):
    path = saved(window, tmp_path)
    assert not window.pending and not window.queue_jobs
    dialog = LoadQueueDialog(window)
    assert dialog.files.count() == 2
    assert dialog.preview.topLevelItem(0).text(0) == 'first'
    assert dialog.preview.topLevelItem(0).text(1) != '—'
    assert list(dialog.buttons) == ['Load', 'Load to New Window', 'Load All', 'Start All', 'Delete', 'Delete All', 'Close']
    dialog.reject()
    assert window.save_queue_state()
    assert len(read_queue(path)) == 2


def test_load_selected_keeps_other_workspace_saved(window, tmp_path):
    path = saved(window, tmp_path)
    dialog = LoadQueueDialog(window)
    dialog.load()
    assert window.paused and len(window.pending) == 1
    assert window.pending[0]['row'].data(4, 256) == 120
    assert len(window.deferred_queue) == 1 and dialog.files.count() == 1
    assert len(read_queue(path)) == 2
    dialog.load()
    assert len(window.pending) == 2 and not window.deferred_queue
    assert len(read_queue(path)) == 2


def test_load_to_new_window_and_no_duplicate_load(window, tmp_path):
    saved(window, tmp_path)
    dialog = LoadQueueDialog(window)
    count = window.tabs.count()
    dialog.load(new_window=True)
    assert window.tabs.count() == count + 1
    assert window.pending[0]['workspace'] is window.tabs.currentWidget()
    dialog.load(all_jobs=True)
    dialog.load(all_jobs=True)
    assert len(window.pending) == 2


def test_delete_queue_keeps_files_and_other_saved_workspace(window, tmp_path, monkeypatch):
    path = saved(window, tmp_path)
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_text('source data')
    target.write_text('downloaded data')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    dialog = LoadQueueDialog(window)
    dialog.delete()
    assert len(read_queue(path)) == 1
    dialog.delete(all_jobs=True)
    assert read_queue(path) == []
    assert source.read_text() == 'source data' and target.read_text() == 'downloaded data'
    assert not dialog.buttons['Load'].isEnabled()


def test_recovery_write_failure_retains_saved_items(window, tmp_path, monkeypatch):
    import fluxftp.queue_manager as module
    path = saved(window, tmp_path)
    before = path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError('Disk unavailable')
    monkeypatch.setattr(module, 'write_queue', fail)
    dialog = LoadQueueDialog(window)
    dialog.load(all_jobs=True)
    assert len(window.deferred_queue) == 2 and not window.queue_jobs
    assert path.read_bytes() == before
    assert 'Disk unavailable' in dialog.status.text()


def test_start_all_runs_restored_local_copy(window, tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'recovered contents')
    path = tmp_path / 'queue.json'
    write_queue(path, [descriptor(tmp_path, state='active')])
    window.initialize_queue_storage(path, defer=True)
    dialog = LoadQueueDialog(window)
    dialog.load(all_jobs=True, start=True)
    QTest.qWait(20)
    settle(window)
    assert (tmp_path / 'target').read_bytes() == b'recovered contents'
    assert read_queue(path) == []
