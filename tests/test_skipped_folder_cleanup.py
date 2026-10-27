import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidgetItem

from fluxftp.queue_tree import GROUP_ROLE, rows
from fluxftp.transfer_options import TransferOptions
from test_queue import window, job_for
from test_ui import settle


@pytest.mark.parametrize('route', ['Copy', 'Upload', 'Download'])
def test_skipped_folder_clears_without_deleting_files(window, tmp_path, route):
    source = tmp_path / 'folder.log'
    source.mkdir()
    (source / 'keep.bin').write_bytes(b'source')
    target = tmp_path / 'target'
    target.mkdir()
    (target / 'keep.bin').write_bytes(b'existing target')
    job = job_for(window, None, route, 'folder.log')
    job.update(src=str(source), dst=str(target), name=source.name, directory=True,
               resume=False, options=TransferOptions(skip_enabled=True, skip_folders=True))
    job['task'] = window.make_queue_task(job)
    window.start_next()
    settle(window)
    assert not window.queue_jobs and window.queue.topLevelItemCount() == 0
    assert (source / 'keep.bin').read_bytes() == b'source'
    assert (target / 'keep.bin').read_bytes() == b'existing target'
    assert 'Skip List mask' in window.transfer_log.toPlainText()
    assert not window.queue_batch_failed


def test_nested_skipped_folders_stay_in_log_not_queue(window, tmp_path):
    source = tmp_path / 'release'
    source.mkdir()
    (source / 'empty').mkdir()
    (source / 'folder.log').mkdir()
    (source / 'folder.log' / 'preserved.bin').write_bytes(b'preserved')
    (source / 'file.log').write_bytes(b'log')
    (source / 'keep.bin').write_bytes(b'keep')
    target = tmp_path / 'target'
    job = job_for(window, None, 'Copy', 'release')
    job.update(src=str(source), dst=str(target), name='release', directory=True,
               resume=False, options=TransferOptions(skip_enabled=True, skip_folders=True))
    job['task'] = window.make_queue_task(job)
    window.start_next()
    settle(window)
    names = [row.text(0) for row in rows(window.queue)]
    assert 'empty' not in names and 'folder.log' not in names
    assert 'file.log' in names
    assert (target / 'keep.bin').read_bytes() == b'keep'
    assert (source / 'folder.log' / 'preserved.bin').read_bytes() == b'preserved'
    assert 'empty' in window.transfer_log.toPlainText()
    assert 'folder.log' in window.transfer_log.toPlainText()


def test_context_delete_removes_leftover_folder_without_job(window, tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'keep.bin').write_bytes(b'keep')
    row = QTreeWidgetItem(['leftover', '', '', '', '', str(source), '', 'Skipped'])
    row.setData(0, GROUP_ROLE, 'old-folder')
    window.queue.addTopLevelItem(row)
    window.preferences.setValue('Confirm/delete_queue_items', False)
    window.show()
    window.show_transfer_queue()
    QApplication.processEvents()
    seen = []
    def popup():
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            try:
                action = next(action for action in menu.actions() if action.text() == 'Delete')
                seen.append(action.isEnabled())
                action.trigger()
            finally:
                menu.close()
    QTimer.singleShot(30, popup)
    window.queue.customContextMenuRequested.emit(window.queue.visualItemRect(row).center())
    assert seen == [True]
    assert window.queue.topLevelItemCount() == 0
    assert (source / 'keep.bin').read_bytes() == b'keep'


def test_folder_delete_guards_hidden_active_descendants(window):
    root = QTreeWidgetItem(['folder'])
    root.setData(0, GROUP_ROLE, 'group')
    window.queue.addTopLevelItem(root)
    job = job_for(window, None)
    window.queue.takeTopLevelItem(window.queue.indexOfTopLevelItem(job['row']))
    root.addChild(job['row'])
    job['row'].setHidden(True)
    root.setSelected(True)
    window.active['test-worker'] = {'row': job['row']}
    try:
        menu = window.queue_commands.context_menu()
        assert not window.queue_commands.delete_action.isEnabled()
        menu.close()
    finally:
        window.active.pop('test-worker')


def test_all_existing_files_skip_and_clear_the_folder(window, tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    for name in ('one.bin', 'two.bin'):
        (source / name).write_bytes(b'new')
        (target / name).write_bytes(b'old')
    job = job_for(window, None, 'Copy', 'source')
    job.update(src=str(source), dst=str(target), name='source', directory=True,
               resume=False, options=TransferOptions(exists_enabled=True))
    job['task'] = window.make_queue_task(job)
    window.start_next()
    settle(window)
    assert not window.queue_jobs and window.queue.topLevelItemCount() == 0
    assert all((target / name).read_bytes() == b'old' for name in ('one.bin', 'two.bin'))
    assert all((source / name).read_bytes() == b'new' for name in ('one.bin', 'two.bin'))
    assert 'Skip' in window.transfer_log.toPlainText()


@pytest.mark.parametrize('other_outcome', ['complete', 'failed', 'cancelled'])
def test_download_folder_skip_cleanup_keeps_failures(window, tmp_path, other_outcome):
    from test_queue_folders import folder
    parent, children = folder(window, tmp_path)
    window.pending.clear()
    for item in (parent, *children):
        item['route'] = 'Download'
    children[0].update(state='complete', transfer_outcome='skipped')
    children[1].update(state='complete' if other_outcome == 'complete' else other_outcome,
                       transfer_outcome=other_outcome)
    window.clear_finished_download(children[0])
    assert bool(window.queue_jobs) is (other_outcome != 'complete')
