from PySide6.QtWidgets import QApplication
from test_queue import window, job_for


def test_folder_popup_survives_repeated_native_context_events(window, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QMenu
    from shiboken6 import isValid
    from test_queue_folders import folder

    parent, children = folder(window, tmp_path)
    window.show()
    window.show_transfer_queue()
    QApplication.processEvents()
    commands = window.queue_commands
    started = []
    monkeypatch.setattr(window, 'start_next', lambda: started.append(True))
    expected = ['Transfer Queue', 'Pause Queue Between Files', 'Pause Selected Transfers', 'Resume Selected Transfers', 'Pause All Transfers', 'Stop Selected Transfers', 'Stop All Transfers', 'Clear Current Queue Files', 'Delete', 'Edit Queue…',
        'Move Queues', 'Failed Queue', 'Queue Information…', 'Load Queue from file…',
        'Save Queue to file…', 'Schedule Queue to Task…', 'On Transfer Completed', 'Transfer selected queue']
    seen = []
    for attempt in range(3):
        parent['row'].setExpanded(attempt != 1)
        def inspect_popup():
            popup = QApplication.activePopupWidget()
            if isinstance(popup, QMenu):
                try:
                    actions = [a for a in popup.actions() if not a.isSeparator()]
                    seen.append(([a.text() for a in actions[:18]], actions[0].isEnabled()))
                    if attempt == 2:
                        actions[0].trigger()
                finally:
                    popup.close()
        QTimer.singleShot(30, inspect_popup)
        point = window.queue.visualItemRect(parent['row']).center()
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point,
                                 window.queue.viewport().mapToGlobal(point))
        QApplication.sendEvent(window.queue.viewport(), event)
        # Reproduce the real lifecycle, not just hiding a still-owned popup.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert isValid(commands.move_menu) and isValid(commands.failed_menu)
        commands.tick()
    assert seen == [(expected, True)] * 3
    assert started and not window.paused
    assert all(isValid(action) for action in commands.actions.values())


def selected_job(window, state='queued'):
    job = job_for(window, lambda _: None)
    job.update(src='/remote/file', dst='C:/Downloads/file', state=state, directory=False)
    job['row'].setSelected(True)
    return job


def test_context_reference_order_and_safe_states(window):
    job = selected_job(window)
    menu = window.queue_commands.context_menu()
    names = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert names[:18] == ['Transfer Queue', 'Pause Queue Between Files', 'Pause Selected Transfers', 'Resume Selected Transfers', 'Pause All Transfers', 'Stop Selected Transfers', 'Stop All Transfers', 'Clear Current Queue Files', 'Delete', 'Edit Queue…',
        'Move Queues', 'Failed Queue', 'Queue Information…', 'Load Queue from file…',
        'Save Queue to file…', 'Schedule Queue to Task…', 'On Transfer Completed', 'Transfer selected queue']
    assert window.queue_commands.delete_action.shortcut().toString() == 'Del'
    assert window.queue_commands.delete_action.isEnabled()
    job['state'] = 'active'
    window.queue_commands.update()
    assert not window.queue_commands.delete_action.isEnabled()
    assert not window.queue_commands.actions['Edit Queue…'].isEnabled()
    job['state'] = 'queued'
    menu.close()


def test_context_copies_paths_and_completion_setting(window):
    selected_job(window)
    menu = window.queue_commands.context_menu()
    actions = {a.text(): a for a in menu.actions()}
    actions['Copy Destination Path'].trigger()
    assert QApplication.clipboard().text() == 'C:/Downloads/file'
    actions['Copy Source Path'].trigger()
    assert QApplication.clipboard().text() == '/remote/file'
    submenu = actions['On Transfer Completed'].menu()
    next(a for a in submenu.actions() if a.text() == 'Disconnect').trigger()
    assert window.preferences.value('Transfer/completed') == 'Disconnect'
    window.preferences.setValue('Transfer/completed', 'Idle')
    menu.close()


def test_transfer_selected_releases_only_selection_when_paused(window, monkeypatch):
    one = selected_job(window)
    two = job_for(window, lambda _: None, name='other')
    window.paused = True
    one['held'] = True
    one['scheduled_at'] = 9999999999
    calls = []
    monkeypatch.setattr(window, 'start_next', lambda: calls.append(True))
    window.queue_commands.transfer_selected()
    assert not window.paused and not one['held'] and one['scheduled_at'] is None
    assert two['held'] and calls


def test_information_counts_buffered_files_and_completed_summary(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from test_queue_buffer import buffered_folder
    parent = buffered_folder(window, tmp_path, 300)
    child = next(job for job in window.queue_jobs if job is not parent)
    child.update(state='complete', transfer_outcome='complete')
    window.retire_buffered_success(child)
    parent.update(held=True, scheduled_at=9999999999)
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda _w, _title, text: messages.append(text))
    window.queue_commands.information()
    lines = messages[-1].splitlines()
    assert 'Queued: 299' in lines and 'Complete: 1' in lines
    assert 'Buffered on disk: 44' in lines
    assert 'Held: 44' in lines and 'Scheduled: 44' in lines
    window.stop_queue()
    window.queue_commands.information()
    assert 'Cancelled: 299' in messages[-1].splitlines()
    assert 'Queued:' not in messages[-1]


def test_buffered_placeholder_cannot_be_edited(window, tmp_path):
    from test_queue_buffer import buffered_folder
    parent = buffered_folder(window, tmp_path, 300)
    saved = window.queue_snapshot()
    window.discard_queue_jobs(list(window.queue_jobs))
    window.restore_queue_items(saved)
    assert len(window.queue_jobs) == 1
    window.queue_jobs[0]['row'].setSelected(True)
    menu = window.queue_commands.context_menu()
    assert not window.queue_commands.actions['Edit Queue…'].isEnabled()
    assert next(a for a in menu.actions() if a.text() == 'Transfer selected queue').isEnabled()
    menu.close()


def test_save_menu_exports_deferred_recovery_items(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from test_queue import descriptor
    from fluxftp.queue_manager import read_queue
    window.deferred_queue = [descriptor(tmp_path)]
    output = tmp_path / 'saved.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_: (str(output), ''))
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda _w, _title, text: messages.append(text))
    menu = window.queue_commands.context_menu()
    action = window.queue_commands.actions['Save Queue to file…']
    assert action.isEnabled()
    action.trigger()
    assert len(read_queue(output)) == 1
    assert len(window.deferred_queue) == 1
    window.queue_commands.information()
    assert 'Saved for recovery: 1' in messages[-1]
    menu.close()


def test_disk_flag_counts_and_release_overrides(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from fluxftp.queue_buffer import DiskBuffer
    from test_queue_buffer import buffered_folder
    parent = buffered_folder(window, tmp_path, 300)
    spool = DiskBuffer()
    for held, scheduled in ((True, None), (False, 9999999999), (True, 9999999999)):
        spool.append(dict(size=1, held=held, scheduled_at=scheduled))
    assert spool.waiting_counts() == (2, 2)
    parent['buffer'].close()
    parent['buffer'] = spool
    parent.update(buffer_release=True, buffer_unschedule=True)
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda _w, _title, text: messages.append(text))
    window.queue_commands.information()
    assert 'Held: 0' in messages[-1].splitlines()
    assert 'Scheduled: 0' in messages[-1].splitlines()
    # These minimal descriptors only exercise statistics, never transfer/snapshot.
    window.stop_queue()
