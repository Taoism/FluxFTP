import json
import time
from pathlib import Path
from threading import Event, get_ident

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QProgressDialog, QFileDialog

from fluxftp.core import Cancelled
from fluxftp.queue_io import Snapshot, background, export, writer
from fluxftp.queue_manager import read_queue, write_queue
from fluxftp.queue_prepare import Settings
from fluxftp.transfer_options import TransferOptions
from test_queue import window, descriptor
from test_queue_buffer import buffered_folder


@pytest.mark.parametrize('content', [
    '{"version":1,"jobs":[],}', '{"version":1,"jobs":[]}x',
    '{"version":true,"jobs":[]}', '{"version":1,"jobs":[],"jobs":[]}',
    '{"version":1,"jobs":[{},]}', '{"version":1}',
])
def test_stream_reader_rejects_invalid_envelopes(tmp_path, content):
    path = tmp_path / 'queue.json'
    path.write_text(content)
    with pytest.raises(ValueError): read_queue(path)


def test_stream_reader_handles_chunk_boundaries_and_unicode(tmp_path):
    path = tmp_path / 'queue.json'
    job = descriptor(tmp_path, name='é' * 70000)
    path.write_text(json.dumps({'jobs': [job], 'version': 1}, ensure_ascii=False), encoding='utf-8')
    assert read_queue(path)[0]['name'] == job['name']


def test_atomic_cancel_keeps_previous_file_and_removes_temporary(tmp_path):
    path = tmp_path / 'queue.json'
    write_queue(path, [descriptor(tmp_path)])
    previous = path.read_bytes()
    cancel = Event()
    def items():
        yield descriptor(tmp_path)
        cancel.set()
        yield descriptor(tmp_path)
    with pytest.raises(Cancelled): write_queue(path, items(), cancel)
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob('queue.json.*.tmp'))


def test_settings_snapshot_matches_gui_defaults(window):
    assert TransferOptions.from_settings(Settings(window.preferences)) == TransferOptions.from_settings(window.preferences)


def test_snapshot_keeps_original_disk_version_after_mutation_and_close(window, tmp_path):
    window.paused = True
    parent = buffered_folder(window, tmp_path, 301)
    snapshot = Snapshot(window)
    parent['buffer'].take(12)
    parent['buffer'].close()
    window.discard_queue_jobs(list(window.queue_jobs))
    try:
        saved = background(window, 'Snapshot test', lambda worker: list(snapshot))
        assert len(saved) == 301
        assert len({item['src'] for item in saved}) == 301
    finally:
        snapshot.close()
        window.discard_queue_jobs(list(window.queue_jobs))


def test_serialized_writer_coalesces_latest_snapshot(window, tmp_path, monkeypatch):
    import fluxftp.queue_manager as module
    window.initialize_queue_storage(tmp_path / 'queue.json')
    window.deferred_queue = [descriptor(tmp_path, name='first')]
    first_started, release = Event(), Event()
    calls, threads = [], []
    original = module.write_queue
    def slow(path, jobs, **kwargs):
        threads.append(get_ident())
        if not calls:
            first_started.set()
            assert release.wait(10)
        captured = list(jobs)
        calls.append(captured)
        original(path, captured, **kwargs)
    monkeypatch.setattr(module, 'write_queue', slow)
    output = writer(window)
    output.save()
    assert first_started.wait(5)
    window.deferred_queue = [descriptor(tmp_path, name='middle')]
    output.save()
    window.deferred_queue = [descriptor(tmp_path, name='latest')]
    output.save()
    release.set()
    assert output.flush()
    assert [batch[0]['name'] for batch in calls] == ['first', 'latest']
    assert all(thread != get_ident() for thread in threads)
    assert read_queue(window.queue_file)[0]['name'] == 'latest'


def test_progress_cancel_signals_worker(window):
    def cancel_dialog():
        for dialog in window.findChildren(QProgressDialog):
            if dialog.isVisible(): dialog.canceled.emit()
    QTimer.singleShot(50, cancel_dialog)
    def work(worker):
        assert worker.cancel.wait(5)
        raise Cancelled('Queue operation cancelled')
    with pytest.raises(Cancelled): background(window, 'Cancellation test', work)
    assert not window._queue_operation_depth


def test_cancelled_startup_retains_recovery_file(window, tmp_path, monkeypatch):
    import fluxftp.queue_manager as module
    path = tmp_path / 'saved.json'
    write_queue(path, (descriptor(tmp_path) for _ in range(5000)))
    before = path.read_bytes()
    def cancelled(*args): raise Cancelled('Cancelled')
    monkeypatch.setattr(module, 'read_queue', cancelled)
    window.initialize_queue_storage(path, defer=True)
    assert not window.queue_storage_enabled
    assert not window.save_queue_state()
    assert not window.queue_jobs and path.read_bytes() == before


def test_cancelled_gui_install_rolls_back_partial_rows(window, tmp_path, monkeypatch):
    from fluxftp.queue_prepare import Prepared, install
    window.restore_queue_items([descriptor(tmp_path, name='existing')])
    before = list(window.queue_jobs)
    prepared = Prepared()
    prepared.singles = [descriptor(tmp_path, name=f'dir{i}', directory=True) for i in range(300)]
    original = window.restore_queue_items
    def restore(items, **kwargs):
        original(items, **kwargs)
        if len(window.queue_jobs) == 3:
            for dialog in window.findChildren(QProgressDialog):
                if dialog.isVisible(): dialog.cancel()
    monkeypatch.setattr(window, 'restore_queue_items', restore)
    with pytest.raises(Cancelled): install(window, prepared)
    assert window.queue_jobs == before
    assert window.pending == before
    assert not window._queue_commit and not window._queue_operation_depth


def test_100001_import_export_recovery_and_expansion_remain_responsive(window, tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    window.show()
    QTest.qWait(250)
    window.paused = True
    window.preferences.setValue('Queue/auto_save', False)
    path = tmp_path / 'import.json'
    count = 100001
    write_queue(path, (descriptor(tmp_path, name=f'{i:06}.bin', src=str(tmp_path / 'source' / f'{i:06}.bin'),
                                  dst=str(tmp_path / 'target' / f'{i:06}.bin'), manual_order=i, size=1)
                       for i in range(count)))
    ticks, reports = [], {}
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    def measure(name, function):
        import sys
        import traceback
        from threading import Thread
        finished = Event()
        main_id = get_ident()
        def watch():
            while not finished.wait(.4):
                if ticks and time.monotonic() - ticks[-1] > .6:
                    print('UI_PAUSE ' + name + '\n' + ''.join(traceback.format_stack(sys._current_frames()[main_id], limit=6)))
        watcher = Thread(target=watch, daemon=True)
        ticks.clear()
        ticks.append(time.monotonic())
        timer.start()
        watcher.start()
        try: result = function()
        finally:
            timer.stop()
            finished.set()
            watcher.join()
        ticks.append(time.monotonic())
        gap = max(b - a for a, b in zip(ticks, ticks[1:]))
        reports[name] = dict(seconds=ticks[-1] - ticks[0], ticks=len(ticks)-2, max_gap=gap)
        assert len(ticks) > 10, reports
        assert gap < .75, reports
        return result
    window.initialize_queue_storage(tmp_path / 'recovery.json')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(path), ''))
    measure('import', window.queue_commands.load)
    assert window.paused and len(window.queue_jobs) <= 257
    out = tmp_path / 'export.json'
    measure('export', lambda: export(window, out))
    saved = read_queue(out)
    assert len(saved) == count
    assert [item['manual_order'] for item in saved] == list(range(count))
    window.discard_queue_jobs(list(window.queue_jobs))
    measure('startup_read', lambda: window.initialize_queue_storage(out, defer=True))
    from fluxftp.queue_recovery import LoadQueueDialog
    dialog = LoadQueueDialog(window)
    dialog.show()
    QTest.qWait(250)
    measure('recovery', lambda: dialog.load(all_jobs=True))
    assert not window.deferred_queue and window.paused
    assert len(read_queue(out)) == count
    window.discard_queue_jobs(list(window.queue_jobs))
    # Actual scan worker converts its disk plan before returning to the GUI.
    import fluxftp.queue_manager as module
    from test_queue import job_for
    parent = job_for(window, None, 'Copy', 'folder')
    parent.update(src=str(tmp_path / 'source'), dst=str(tmp_path / 'target'), directory=True,
                  name='folder', resume=False, options=TransferOptions(), state='complete')
    window.pending.remove(parent)
    gui_thread = get_ident()
    def scan(*args, planned, **kwargs):
        assert get_ident() != gui_thread
        for i in range(count):
            planned.append((str(tmp_path / 'source' / f'{i:06}.bin'), str(tmp_path / 'target' / f'{i:06}.bin'), 1))
        return True
    monkeypatch.setattr(module, 'transfer_tree', scan)
    result = measure('folder_scan', lambda: background(window, 'Scanning test folder', window.make_queue_task(parent)))
    assert result['planned'].descriptors and len(result['planned']) == count
    window.expand_queue_files(parent, result['planned'])
    assert len(window.queue_jobs) <= 257
    print('\nASYNC_100K_METRICS=' + json.dumps(reports))
    (Path(__file__).resolve().parents[1] / 'logs/async-100k-075.json').write_text(json.dumps(reports, indent=2))
