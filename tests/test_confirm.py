from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QFileDialog
from PySide6.QtGui import QCloseEvent

from fluxftp.confirm_options import DEFAULTS
from fluxftp.preferences import PreferencesDialog
from fluxftp.core import Site, Entry
from fluxftp.queue_manager import read_queue, write_queue
from test_queue import window, job_for
from test_mouse import local_pair


def answer(monkeypatch, accepted):
    calls = []
    def ask(*args, **kwargs):
        calls.append(args)
        return QMessageBox.StandardButton.Yes if accepted else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', ask)
    return calls


def test_reference_defaults_persistence(window):
    dialog = PreferencesDialog(window)
    assert {key: value.isChecked() for key, value in dialog.confirm_checks.items()} == DEFAULTS
    for check in dialog.confirm_checks.values():
        check.toggle()
    dialog.close()
    again = PreferencesDialog(window)
    assert {key: value.isChecked() for key, value in again.confirm_checks.items()} == {key: not value for key, value in DEFAULTS.items()}
    again.close()
    window.preferences.setValue('Confirm/exit', False)


def test_exit_accept_cancel_and_disabled(window, monkeypatch):
    calls = answer(monkeypatch, False)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and calls[-1][1] == 'Exit FluxFTP'
    calls = answer(monkeypatch, True)
    window.closeEvent(event)
    assert event.isAccepted() and len(calls) == 1
    calls = answer(monkeypatch, False)
    window.preferences.setValue('Confirm/exit', False)
    window.closeEvent(event)
    assert event.isAccepted() and not calls


def test_connected_workspace_cancel_accept_and_disabled(window, monkeypatch):
    window.add_workspace()
    pane = window.current_panes()[0]
    pane.site = pane.remote_site = Site(host='example.test')
    calls = answer(monkeypatch, False)
    window.close_tab(1)
    assert window.tabs.count() == 2 and calls[-1][1] == 'Close connected workspace'
    window.preferences.setValue('Confirm/close_connected', False)
    window.close_tab(1)
    assert window.tabs.count() == 1 and len(calls) == 1
    window.preferences.setValue('Confirm/exit', False)


def test_close_queued_workspace_preserves_files_and_cancel_is_nonmutating(window, tmp_path, monkeypatch):
    window.add_workspace()
    left, right, source, target = local_pair(window, tmp_path)
    file = source / 'file.txt'
    file.write_text('source')
    window.enqueue_items(left, right, [Entry(file.name, False, 6)], start=False)
    calls = answer(monkeypatch, False)
    window.close_tab(1)
    assert len(window.pending) == 1 and window.tabs.count() == 2
    assert calls[-1][1] == 'Close workspace with queue files'
    answer(monkeypatch, True)
    window.close_tab(1)
    assert not window.pending and not window.queue_jobs and window.tabs.count() == 1
    assert file.read_text() == 'source' and not (target / file.name).exists()
    assert read_queue(window.queue_file) == []


def test_local_delete_checkbox_only_controls_local_files(window, tmp_path, monkeypatch):
    left, _, source, _ = local_pair(window, tmp_path)
    file = source / 'file.txt'
    file.write_text('keep')
    left.refresh()
    left.tree.topLevelItem(0).setSelected(True)
    window.preferences.setValue('Confirm/delete_local', True)
    calls = answer(monkeypatch, False)
    left.delete()
    assert file.exists() and calls[-1][1] == 'Delete local files'
    window.preferences.setValue('Confirm/delete_local', False)
    left.delete()
    assert not file.exists() and len(calls) == 1
    window.preferences.setValue('Confirm/exit', False)


def test_remove_queue_items_confirm_and_active_guard(window, tmp_path, monkeypatch):
    left, right, source, target = local_pair(window, tmp_path)
    file = source / 'file.txt'
    file.write_text('keep')
    window.enqueue_items(left, right, [Entry(file.name, False, 4)], start=False)
    job = window.pending[0]
    job['row'].setSelected(True)
    window.preferences.setValue('Confirm/delete_queue_items', True)
    calls = answer(monkeypatch, False)
    window.remove_queue_items()
    assert len(window.pending) == 1 and calls[-1][1] == 'Delete queue items'
    job['state'] = 'active'
    window.remove_queue_items()
    assert len(calls) == 1 and window.queue.topLevelItemCount() == 1
    job['state'] = 'queued'
    window.preferences.setValue('Confirm/delete_queue_items', False)
    window.remove_queue_items()
    assert not window.queue_jobs and not window.pending and window.queue.topLevelItemCount() == 0
    assert file.read_text() == 'keep'
    window.preferences.setValue('Confirm/exit', False)


def test_export_and_delete_saved_queue_only(window, tmp_path, monkeypatch):
    left, right, source, target = local_pair(window, tmp_path)
    file = source / 'file.txt'
    file.write_text('keep')
    window.enqueue_items(left, right, [Entry(file.name, False, 4)], start=False)
    export = tmp_path / 'export.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(export), ''))
    window.export_queue_list()
    assert len(read_queue(export)) == 1
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(export), ''))
    window.preferences.setValue('Confirm/delete_queue_files', True)
    calls = answer(monkeypatch, False)
    window.delete_queue_file()
    assert export.exists() and calls[-1][1] == 'Delete saved queue file'
    window.preferences.setValue('Confirm/delete_queue_files', False)
    window.delete_queue_file()
    assert not export.exists() and file.exists() and len(window.pending) == 1
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(window.queue_file), ''))
    window.delete_queue_file()
    assert errors and window.queue_file.exists()
    export.write_text('unrelated JSON')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(export), ''))
    window.delete_queue_file()
    assert export.exists() and len(errors) == 2
    window.preferences.setValue('Confirm/exit', False)


def test_shutdown_confirm_cancel_disable_and_work_guard(window, monkeypatch):
    from fluxftp import confirm_options
    requested = []
    monkeypatch.setattr(confirm_options, 'request_system_shutdown', lambda: requested.append(True))
    calls = answer(monkeypatch, False)
    window.shutdown_computer()
    assert not requested and calls[-1][1] == 'Shutdown computer'
    window.preferences.setValue('Confirm/shutdown', False)
    window.shutdown_computer()
    assert requested == [True] and len(calls) == 1
    window.pending.append({'placeholder': True})
    window.shutdown_computer()
    assert requested == [True]
    window.pending.clear()
    window.preferences.setValue('Confirm/exit', False)


def test_shutdown_failure_stays_open(window, monkeypatch):
    from fluxftp import confirm_options
    def fail():
        raise OSError('request rejected')
    monkeypatch.setattr(confirm_options, 'request_system_shutdown', fail)
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    window.preferences.setValue('Confirm/shutdown', False)
    window.shutdown_computer()
    assert errors == ['request rejected']


def test_windows_shutdown_command_has_no_force_flag(monkeypatch):
    import os
    from types import SimpleNamespace
    from fluxftp import confirm_options
    if os.name != 'nt':
        pytest.skip('Windows command')
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(confirm_options.subprocess, 'run', run)
    confirm_options.request_system_shutdown()
    assert calls[0][0][1:] == ['/s', '/t', '0']
    assert Path(calls[0][0][0]).name == 'shutdown.exe'
    assert calls[0][1]['shell'] is False


def test_queue_activation_during_prompt_prevents_removal(window, monkeypatch):
    job = job_for(window, lambda _: None)
    job['row'].setSelected(True)
    window.preferences.setValue('Confirm/delete_queue_items', True)
    def ask(*args, **kwargs):
        window.active['test'] = {'row': job['row']}
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', ask)
    window.remove_queue_items()
    assert job in window.queue_jobs
    window.active.clear()
    window.preferences.setValue('Confirm/exit', False)
