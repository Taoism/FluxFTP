from pathlib import Path

from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest

from fluxftp.queue_manager import read_queue, write_queue
from fluxftp.transfer_options import TransferOptions
from test_queue import window, job_for


def folder(window, tmp_path):
    parent = job_for(window, None, 'Copy', 'folder')
    parent.update(src=str(tmp_path / 'source'), dst=str(tmp_path / 'target'), name='folder',
                  directory=True, resume=False, options=TransferOptions(), state='complete')
    window.pending.remove(parent)
    window.expand_queue_files(parent, [(str(tmp_path / 'source' / name), str(tmp_path / 'target' / name), 10)
                                       for name in ('a.bin', 'b.bin')])
    return parent, [job for job in window.queue_jobs if job is not parent]


def test_click_folder_toggles_and_cancel_includes_hidden_files(window, tmp_path):
    parent, children = folder(window, tmp_path)
    row = parent['row']
    assert row.childCount() == 2 and window.queue.topLevelItemCount() == 1
    window.show()
    QTest.qWait(20)
    point = window.queue.visualItemRect(row).topLeft() + QPoint(35, 8)
    assert row.isExpanded()
    QTest.mouseClick(window.queue.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert not row.isExpanded()
    QTest.mouseClick(window.queue.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert row.isExpanded()
    QTest.mouseClick(window.queue.viewport(), Qt.MouseButton.LeftButton, pos=point)
    window.cancel_selected()
    assert all(job['state'] == 'cancelled' for job in children)
    assert not window.pending


def test_remove_folder_and_restore_queue_preserves_children(window, tmp_path):
    parent, children = folder(window, tmp_path)
    window.queue_commands.clear_current()
    assert not window.queue_jobs and window.queue.topLevelItemCount() == 0
    window.queue_commands.restore()
    assert window.queue.topLevelItemCount() == 1
    assert parent['row'].childCount() == 2
    assert all(job['row'].parent() is parent['row'] for job in children)
    parent['row'].setSelected(True)
    window.remove_queue_items()
    assert not window.queue_jobs and not window.pending
    assert window.queue.topLevelItemCount() == 0


def test_saved_files_restore_under_folder_and_can_be_reordered(window, tmp_path):
    parent, children = folder(window, tmp_path)
    path = tmp_path / 'queue.json'
    write_queue(path, window.queue_snapshot())
    saved = read_queue(path)
    assert len(saved) == 2 and all(item['queue_group']['name'] == 'folder' for item in saved)
    window.discard_queue_jobs(list(window.queue_jobs))
    window.restore_queue_items(saved)
    root = window.queue.topLevelItem(0)
    assert window.queue.topLevelItemCount() == 1 and root.childCount() == 2
    first = root.child(0)
    first.setSelected(True)
    window.queue_commands.move('down')
    assert root.child(1) is first and first.parent() is root
    window.queue_commands.clear_current()
    assert window.queue.topLevelItemCount() == 0


def test_release_progress_is_weighted_by_bytes_and_survives_collapse(window, tmp_path):
    from fluxftp.queue_tree import PROGRESS_ROLE, STATE_ROLE
    parent, children = folder(window, tmp_path)
    children[0]['row'].setData(4, Qt.ItemDataRole.UserRole, 100)
    children[1]['row'].setData(4, Qt.ItemDataRole.UserRole, 300)
    children[0]['row'].setData(0, STATE_ROLE, 'active')
    children[1]['row'].setData(0, STATE_ROLE, 'active')
    parent['row'].setExpanded(False)
    window.transfer_display_progress(children[0]['row'], 100, 2, 100, 100)
    window.transfer_display_progress(children[1]['row'], 50, 3, 150, 300)
    assert parent['row'].data(1, PROGRESS_ROLE) == 62
    assert parent['row'].data(3, Qt.ItemDataRole.UserRole) == [250, 400]
    assert not parent['row'].isExpanded()


def test_worker_progress_supports_large_files(window):
    from fluxftp.app import Worker
    worker = Worker(lambda _: None)
    samples = []
    worker.progress.connect(lambda *args: samples.append(args))
    worker.report(3 * 1024**3, 4 * 1024**3)
    assert samples[0][0] == 75
    assert samples[0][2:] == (3 * 1024**3, 4 * 1024**3)
