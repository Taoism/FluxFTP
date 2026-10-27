import json
import time
from PySide6.QtCore import Qt, QDateTime, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QSpinBox, QDateTimeEdit, QFileDialog, QInputDialog

from fluxftp.queue_manager import read_queue
from test_queue import window, job_for
from test_mouse import local_pair
from test_ui import settle


def queued_files(window, tmp_path):
    left, right, source, target = local_pair(window, tmp_path)
    from fluxftp.core import Entry
    for name in ('a.txt', 'b.nfo', 'c.txt'):
        (source / name).write_text(name)
        window.enqueue_items(left, right, [Entry(name, False, len(name))], start=False)
    return left, right, source, target


def names(window):
    return [job['name'] for job in window.queue_jobs]


def test_queue_menu_order_shortcuts_and_focus_conflict(window, tmp_path):
    left, right, source, target = queued_files(window, tmp_path)
    menu = window.queue_commands
    assert [a.text() if not a.isSeparator() else '---' for a in menu.menu.actions()] == [
        'Transfer Queue', 'Add the selected to queue', 'Conditional Transfer…', 'Clear Current Queue Files', 'Move Queues',
        '---', 'Edit Queue…', 'Failed Queue', 'Queue Information…', '---', 'Restore Queue…',
        'Load Queue from file…', 'Save Queue to file…', 'Schedule Queue to Task…', '---', 'Limit Upload Speed', 'Limit Download Speed']
    assert [a.shortcut().toString() for a in menu.move_menu.actions()] == ['Ctrl+U', 'Ctrl+Y', 'Ctrl+O', 'Ctrl+M']
    assert menu.actions['Restore Queue…'].shortcut().toString() == 'Ctrl+Shift+L'
    window.queue.setCurrentItem(window.queue_jobs[-1]['row'])
    window.show()
    window.activateWindow()
    window.queue.setFocus()
    QTest.qWait(20)
    QTest.keyClick(window.queue, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
    assert names(window) == ['c.txt', 'a.txt', 'b.nfo']
    assert not menu.move_menu.actions()[0].isEnabled()
    assert menu.move_menu.actions()[1].isEnabled()


def test_manual_order_changes_execution_and_survives_recovery(window, tmp_path, monkeypatch):
    queued_files(window, tmp_path)
    menu = window.queue_commands
    window.queue_jobs[2]['row'].setSelected(True)
    menu.move('top')
    window.queue.clearSelection()
    window.queue_jobs[2]['row'].setSelected(True)
    menu.move('up')
    assert names(window) == ['c.txt', 'b.nfo', 'a.txt']
    saved = read_queue(window.queue_file)
    assert [job['manual_order'] for job in saved] == [0, 1, 2]
    window.discard_queue_jobs(list(window.queue_jobs))
    window.restore_queue_items(saved)
    assert window.paused and names(window) == ['c.txt', 'b.nfo', 'a.txt']
    ran = []
    for job in window.queue_jobs:
        job['task'] = lambda worker, name=job['name']: ran.append(name)
    window.preferences.setValue('Transfer/single_connection', True)
    window.play_queue()
    settle(window)
    assert ran == ['c.txt', 'b.nfo', 'a.txt']


def test_multiselect_stability_and_active_guard(window, tmp_path):
    queued_files(window, tmp_path)
    menu = window.queue_commands
    for job in window.queue_jobs[1:]:
        job['row'].setSelected(True)
    menu.move('up')
    assert names(window) == ['b.nfo', 'c.txt', 'a.txt']
    menu.move('bottom')
    assert names(window) == ['a.txt', 'b.nfo', 'c.txt']
    window.queue_jobs[1]['state'] = 'active'
    menu.move('top')
    assert names(window) == ['a.txt', 'b.nfo', 'c.txt']
    window.queue_jobs[1]['state'] = 'queued'


def test_clear_restore_after_workspace_closed_keeps_files(window, tmp_path):
    left, right, source, target = queued_files(window, tmp_path)
    menu = window.queue_commands
    window.preferences.setValue('Confirm/close_queue', False)
    window.preferences.setValue('Confirm/close_connected', False)
    menu.clear_current()
    assert not window.queue_jobs and not window.pending
    assert len(list(source.iterdir())) == 3
    window.add_workspace()
    window.close_tab(0)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    menu.restore()
    assert window.paused and names(window) == ['a.txt', 'b.nfo', 'c.txt']
    assert all(job['workspace'] is window.tabs.currentWidget() for job in window.queue_jobs)
    assert len(list(source.iterdir())) == 3 and not list(target.iterdir())


def test_load_is_paused_and_rejects_unsafe_paths_without_changes(window, tmp_path, monkeypatch):
    queued_files(window, tmp_path)
    path = tmp_path / 'export.json'
    path.write_text(json.dumps({'version': 1, 'jobs': window.queue_snapshot()}))
    window.queue_commands.clear_current()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(path), ''))
    window.queue_commands.load()
    assert len(window.queue_jobs) == 3 and window.paused
    assert all(job['needs_credentials'] for job in window.queue_jobs)
    before = list(window.queue_jobs)
    saved = json.loads(path.read_text())
    saved['jobs'][0]['dst'] = saved['jobs'][0]['src']
    path.write_text(json.dumps(saved))
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    window.queue_commands.load()
    assert errors and window.queue_jobs == before


def test_conditional_adds_only_matching_selected_files_held(window, tmp_path, monkeypatch):
    left, right, source, target = local_pair(window, tmp_path)
    (source / 'yes.TXT').write_bytes(b'x' * 2048)
    (source / 'small.txt').write_bytes(b'x')
    (source / 'other.bin').write_bytes(b'x' * 2048)
    (source / 'folder').mkdir()
    left.refresh()
    left.tree.selectAll()
    def accept(dialog):
        dialog.findChild(QLineEdit).setText('*.txt')
        dialog.findChildren(QSpinBox)[0].setValue(1)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', accept)
    window.queue_commands.conditional()
    assert names(window) == ['yes.TXT'] and window.pending[0]['held']
    assert not list(target.iterdir())


def test_retry_selected_preserves_other_paused_jobs(window, tmp_path):
    queued_files(window, tmp_path)
    failed, other, third = window.queue_jobs
    failed['state'] = 'failed'
    window.pending.remove(failed)
    failed['row'].setSelected(True)
    ran = []
    failed['task'] = lambda worker: ran.append('retry')
    window.paused = True
    window.queue_commands.retry(False)
    for _ in range(100):
        QTest.qWait(10)
        if not window.active and not window.workers:
            break
    assert ran == ['retry']
    assert other['held'] and third['held'] and other in window.pending


def test_scheduled_jobs_wait_persist_and_only_start_when_due(window, tmp_path, monkeypatch):
    queued_files(window, tmp_path)
    scheduled = window.queue_jobs[0]
    scheduled['row'].setSelected(True)
    window.paused = True
    def accept(dialog):
        dialog.findChild(QDateTimeEdit).setDateTime(QDateTime.currentDateTime().addSecs(120))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', accept)
    ran = []
    scheduled['task'] = lambda worker: ran.append('scheduled')
    window.queue_commands.schedule()
    assert scheduled['scheduled_at'] > time.time()
    assert read_queue(window.queue_file)[0]['scheduled_at'] == scheduled['scheduled_at']
    window.queue_commands.tick()
    assert not ran and not window.active
    scheduled['scheduled_at'] = int(time.time()) - 1
    window.queue_commands.tick()
    for _ in range(100):
        QTest.qWait(10)
        if not window.active and not window.workers:
            break
    assert ran == ['scheduled']
    assert all(job['held'] for job in window.pending)


def test_speed_limits_apply_to_shared_limiter_and_cancel(window, monkeypatch):
    menu = window.queue_commands
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *a, **kw: (64, True))
    menu.actions['Limit Upload Speed'].trigger()
    assert window.upload_limiter.rate == 64 * 1024
    assert window.preferences.value('Transfer/limit_upload', type=bool)
    menu.actions['Limit Upload Speed'].trigger()
    assert window.upload_limiter.rate == 0
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *a, **kw: (50, False))
    menu.actions['Limit Download Speed'].trigger()
    assert not menu.actions['Limit Download Speed'].isChecked()
    assert window.download_limiter.rate == 0


def test_recovered_due_schedule_stays_paused(window, tmp_path):
    queued_files(window, tmp_path)
    saved = window.queue_snapshot()
    saved[0].update(scheduled_at=int(time.time()) - 1, held=False)
    window.discard_queue_jobs(list(window.queue_jobs))
    window.restore_queue_items(saved)
    ran = []
    for job in window.queue_jobs:
        job['needs_credentials'] = False
        job['task'] = lambda worker: ran.append(True)
    window.queue_commands.tick()
    assert window.paused and not ran and not window.active
