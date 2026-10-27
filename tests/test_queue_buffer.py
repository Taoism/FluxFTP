from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt

from fluxftp.queue_buffer import DiskBuffer, BUFFER_LIMIT, BUFFER_ROLE
from fluxftp.queue_manager import read_queue, write_queue
from fluxftp.transfer_options import TransferOptions
from test_queue import window, job_for
from test_ui import settle


def buffered_folder(window, tmp_path, count):
    if not hasattr(window, 'queue_file'):
        window.initialize_queue_storage(tmp_path / 'queue.json')
    parent = job_for(window, None, 'Copy', 'folder')
    parent.update(src=str(tmp_path / 'source'), dst=str(tmp_path / 'target'),
                  name='folder', directory=True, resume=False, options=TransferOptions(), state='complete')
    window.pending.remove(parent)
    files = DiskBuffer()
    for i in range(count):
        files.append((str(tmp_path / 'source' / f'{i:05}.bin'), str(tmp_path / 'target' / f'{i:05}.bin'), 1))
    window.expand_queue_files(parent, files)
    return parent


def test_large_folder_has_bounded_rows_and_complete_portable_snapshot(window, tmp_path):
    parent = buffered_folder(window, tmp_path, 12002)
    assert len(window.queue_jobs) == BUFFER_LIMIT + 1
    assert parent['row'].childCount() == BUFFER_LIMIT
    assert len(parent['buffer']) == 12002 - BUFFER_LIMIT
    path = tmp_path / 'export.json'
    write_queue(path, window.iter_queue_snapshot())
    saved = read_queue(path)
    assert len(saved) == 12002
    assert len({item['src'] for item in saved}) == 12002
    window.discard_queue_jobs(list(window.queue_jobs))
    window.restore_queue_items(saved)
    assert len(window.queue_jobs) <= BUFFER_LIMIT + 1
    assert len(window.queue_snapshot()) == 12002
    assert window.paused


def test_multiple_batches_copy_every_file_and_release_completed_rows(window, tmp_path, monkeypatch):
    import fluxftp.queue_buffer as buffer_module
    import fluxftp.queue_manager as manager_module
    monkeypatch.setattr(buffer_module, 'BUFFER_LIMIT', 8)
    monkeypatch.setattr(manager_module, 'BUFFER_LIMIT', 8)
    source = tmp_path / 'source'
    source.mkdir()
    for i in range(31):
        (source / f'{i:05}.bin').write_bytes(b'x')
    parent = buffered_folder(window, tmp_path, 31)
    peaks = []
    original = window.refill_queue_buffers
    def refill():
        original()
        peaks.append(len(window.queue_jobs))
    monkeypatch.setattr(window, 'refill_queue_buffers', refill)
    window.start_next()
    settle(window)
    assert len(list((tmp_path / 'target').glob('*.bin'))) == 31
    assert all(path.read_bytes() == b'x' for path in (tmp_path / 'target').glob('*.bin'))
    assert max(peaks) <= 9
    assert parent['row'].childCount() == 0
    assert parent['row'].data(0, BUFFER_ROLE)['completed'] == 31
    assert parent['row'].text(7) == '31/31 files · Complete'
    assert 'transferred 31 file(s)' in window.transfer_log.toPlainText()
    assert not window.pending and not window.active
    assert read_queue(window.queue_file) == []


def test_cancel_and_clear_restore_include_unloaded_files(window, tmp_path):
    parent = buffered_folder(window, tmp_path, 300)
    window.preferences.setValue('Confirm/delete_queue_items', False)
    window.queue_commands.clear_current()
    assert not window.queue_jobs and not window.pending
    window.queue_commands.restore()
    assert len(window.queue_snapshot()) == 300
    window.queue.topLevelItem(0).setSelected(True)
    window.cancel_selected()
    window.refill_queue_buffers()
    assert not window.pending
    assert window.queue_snapshot() == []


def test_spool_order_and_removal(tmp_path):
    spool = DiskBuffer(rank=lambda item: item['rank'])
    path = Path(spool.path)
    for rank in (3, 1, 2, 1):
        spool.append(dict(rank=rank))
    assert [item['rank'] for item in spool.take(2)] == [1, 1]
    assert [item['rank'] for item in spool] == [2, 3]
    assert len(spool) == 2
    spool.close()
    assert not path.exists()


def test_held_large_selection_releases_on_play(window, tmp_path, monkeypatch):
    import fluxftp.queue_buffer as buffer_module
    import fluxftp.queue_manager as manager_module
    from fluxftp.core import Entry
    monkeypatch.setattr(buffer_module, 'BUFFER_LIMIT', 8)
    monkeypatch.setattr(manager_module, 'BUFFER_LIMIT', 8)
    window.initialize_queue_storage(tmp_path / 'queue.json')
    source, destination = window.current_panes()
    folder = tmp_path / 'source'
    folder.mkdir()
    target = tmp_path / 'target'
    target.mkdir()
    source.mode = destination.mode = 'Local'
    source.path, destination.path = str(folder), str(target)
    source.busy = destination.busy = False
    for i in range(31):
        (folder / f'{i}.bin').write_bytes(b'z')
    window.preferences.setValue('Confirm/queue', False)
    window.enqueue_items(source, destination, [Entry(f'{i}.bin', False, 1) for i in range(31)], start=False)
    assert len(window.queue_jobs) == 1
    assert len(window.queue_snapshot()) == 31
    window.play_queue()
    settle(window)
    assert len(list(target.iterdir())) == 31
    assert not window.pending


def test_recovery_releases_held_and_preserves_future_schedule(window, tmp_path, monkeypatch):
    import time
    from PySide6.QtTest import QTest
    import fluxftp.queue_buffer as buffer_module
    import fluxftp.queue_manager as manager_module
    monkeypatch.setattr(buffer_module, 'BUFFER_LIMIT', 8)
    monkeypatch.setattr(manager_module, 'BUFFER_LIMIT', 8)
    source = tmp_path / 'source'
    source.mkdir()
    for i in range(31):
        (source / f'{i:05}.bin').write_bytes(b'x')
    parent = buffered_folder(window, tmp_path, 31)
    saved = window.queue_snapshot()
    for i, item in enumerate(saved):
        item['held'] = True
        item['manual_order'] = i
        item['scheduled_at'] = int(time.time()) + 3600 if i < 20 else None
    window.discard_queue_jobs(list(window.queue_jobs))
    window.restore_queue_items(saved)
    assert len(window.queue_jobs) == 1
    window.play_queue()
    deadline = time.monotonic() + 10
    while len(list((tmp_path / 'target').glob('*.bin'))) < 11 or window.workers:
        QTest.qWait(20)
        assert time.monotonic() < deadline
    assert len(window.queue_snapshot()) == 20
    assert all(item['scheduled_at'] > time.time() and not item['held'] for item in window.queue_snapshot())
    root = window.queue.topLevelItem(0)
    root.setSelected(True)
    window.queue_commands.transfer_selected()
    settle(window)
    assert len(list((tmp_path / 'target').glob('*.bin'))) == 31


def test_failed_file_retained_while_buffer_drains(window, tmp_path, monkeypatch):
    import fluxftp.queue_buffer as buffer_module
    import fluxftp.queue_manager as manager_module
    monkeypatch.setattr(buffer_module, 'BUFFER_LIMIT', 8)
    monkeypatch.setattr(manager_module, 'BUFFER_LIMIT', 8)
    source = tmp_path / 'source'
    source.mkdir()
    for i in range(1, 31):
        (source / f'{i:05}.bin').write_bytes(b'x')
    parent = buffered_folder(window, tmp_path, 31)
    window.preferences.setValue('Queue/retry_download', 0)
    window.start_next()
    settle(window)
    assert len(list((tmp_path / 'target').glob('*.bin'))) == 30
    failed = [job for job in window.queue_jobs if job['state'] == 'failed']
    assert len(failed) == 1 and failed[0]['name'] == '00000.bin'
    assert len(window.queue_snapshot()) == 1
    (source / '00000.bin').write_bytes(b'x')
    window.queue_commands.retry(True)
    settle(window)
    assert parent['row'].text(7) == '31/31 files · Complete'


def test_buffer_read_error_pauses_without_losing_queue(window, tmp_path, monkeypatch):
    parent = buffered_folder(window, tmp_path, 300)
    before = window.queue_snapshot()
    def fail(*args, **kwargs):
        raise OSError('test disk unavailable')
    monkeypatch.setattr(parent['buffer'], 'take', fail)
    window.refill_queue_buffers()
    assert window.paused
    assert window.queue_snapshot() == before
    assert 'cannot read its disk buffer' in window.log.toPlainText()


def test_failed_buffer_batch_rolls_back_every_descriptor():
    import sqlite3
    import pytest
    spool = DiskBuffer()
    for i in range(3):
        spool.append(dict(src=str(i), size=1))
    spool.db.commit()
    spool.db.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON items WHEN old.id=2 BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    spool.db.commit()
    with pytest.raises(sqlite3.Error, match='test failure'):
        spool.take(3)
    assert len(spool) == 3 and spool.bytes == 3
    assert [item['src'] for item in spool] == ['0', '1', '2']
    spool.db.execute('DROP TRIGGER reject_delete')
    assert len(spool.take(3)) == 3
    spool.close()


def test_second_buffer_gets_next_available_slot(window, tmp_path):
    first = buffered_folder(window, tmp_path, 300)
    second = buffered_folder(window, tmp_path / 'other', 300)
    assert second['row'].childCount() == 0
    child = next(job for job in window.queue_jobs if job['row'].parent() is first['row'])
    child['state'] = child['transfer_outcome'] = 'complete'
    window.retire_buffered_success(child)
    window.refill_queue_buffers()
    assert second['row'].childCount() == 1
    assert sum(not job.get('buffered') for job in window.queue_jobs) == BUFFER_LIMIT
