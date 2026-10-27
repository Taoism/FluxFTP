from threading import Event
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from fluxftp.queue_filter import search_page
from fluxftp.queue_buffer import DiskBuffer
from test_queue import window, job_for


def test_layout_lock_blocks_drag_and_persists(window):
    window.show()
    QTest.qWait(30)
    window.layout_lock.action.setChecked(True)
    assert window.preferences.value('Layout/locked', type=bool)
    grip = window.header_rows.rows['overview'].height_grip
    original = grip.row.height()
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, pos=QPoint(20, 2))
    QTest.mouseMove(grip, QPoint(20, 50))
    QTest.mouseRelease(grip, Qt.MouseButton.LeftButton, pos=QPoint(20, 50))
    assert grip.row.height() == original
    split = window.work_area
    sizes = split.sizes()
    handle = split.handle(1)
    QTest.mousePress(handle, Qt.MouseButton.LeftButton)
    QTest.mouseMove(handle, QPoint(10, 80))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton)
    assert split.sizes() == sizes
    window.add_workspace()
    assert not window.tabs.currentWidget().handle(1).isEnabled()
    window.layout_lock.action.setChecked(False)
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, pos=QPoint(20, 2))
    QTest.mouseMove(grip, QPoint(20, 50))
    QTest.mouseRelease(grip, Qt.MouseButton.LeftButton, pos=QPoint(20, 50))
    assert grip.row.height() != original


def test_filter_paths_status_and_hidden_selection(window):
    first = job_for(window, None, name='one')
    second = job_for(window, None, name='two')
    for job, name in ((first, 'one'), (second, 'two')):
        job.update(name=name, src='/music/' + name, dst='/target/' + name, directory=False, resume=False)
    first.update(state='failed')
    window.pending.remove(first)
    first['row'].setSelected(True)
    window.queue_filter.status.setCurrentText('Queued')
    window.queue_filter.apply()
    assert first['row'].isHidden() and not first['row'].isSelected()
    assert not second['row'].isHidden()
    window.queue_filter.query.setText('/music/ONE')
    window.queue_filter.status.setCurrentText('All')
    window.queue_filter.apply()
    assert not first['row'].isHidden() and second['row'].isHidden()
    window.queue_filter.query.clear()
    window.queue_filter.apply()
    assert not second['row'].isHidden()


def test_search_disk_paging_unicode_and_cancel():
    spool = DiskBuffer()
    try:
        for n in range(501):
            spool.append(dict(name=f'Été-{n}', src=f'/music/{n}', dst='/target', state='queued'))
        spool.db.commit()
        buffers = [(spool.path, {'held': True})]
        first, more = search_page([], buffers, 'ÉTÉ', 'Held', 0, Event())
        assert len(first) == 200 and more
        last, more = search_page([], buffers, 'été', 'Held', 400, Event())
        assert len(last) == 101 and not more
        assert not search_page([], buffers, '', 'Active', 0, Event())[0]
        cancel = Event()
        cancel.set()
        assert search_page([], buffers, '', 'All', 0, cancel) == ([], False)
        assert len(spool) == 501
    finally:
        spool.close()


def test_search_reader_survives_queue_clear_and_releases_file():
    from pathlib import Path
    spool = DiskBuffer()
    spool.append(dict(name='file', src='/a', dst='/b', state='queued'))
    reader = spool.open_reader()
    path = Path(spool.path)
    spool.close()
    assert path.exists()
    items, more = search_page([], [(reader, {})], '', 'All', 0, Event())
    assert len(items) == 1 and not more
    assert not path.exists()


def test_queue_stop_and_selected_cancel_signal_fxp(window):
    from types import SimpleNamespace
    job = job_for(window, None, route='FXP')
    window.pending.remove(job)
    job['state'] = 'active'
    worker = SimpleNamespace(cancel=Event())
    record = dict(job=job, worker=worker, row=job['row'], route='FXP')
    window.active['test'] = record
    try:
        job['row'].setSelected(True)
        window.cancel_selected()
        assert worker.cancel.is_set() and job['suppress_retry']
        worker.cancel.clear()
        window.stop_queue()
        assert worker.cancel.is_set()
    finally:
        window.active.clear()
