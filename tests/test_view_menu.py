from tempfile import TemporaryDirectory
from pathlib import Path

from PySide6.QtCore import Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from fluxftp.core import Site
from fluxftp.remote_editor import RemoteTextDialog
from fluxftp.association_actions import ExternalFileSession
from fluxftp.transfer_options import TransferOptions
from test_queue import window


def visible(window):
    window.show()
    window.activateWindow()
    QTest.qWait(20)


def test_view_menu_reference_order_shortcuts_and_submenus(window):
    actions = window.view_menu.actions()
    assert [a.text() if not a.isSeparator() else '---' for a in actions] == [
        'Refresh', '---', 'Window list', 'Queue Window', 'System log', 'Transfer Log',
        'FTP Log', 'Task Window', '---', 'Remote columns', 'Local columns',
        'Window list columns', 'Grid Lines', 'Column Auto Size', '---', 'Current edit files', '---', 'Reset layout…',
        'Customizable docking layout', 'Dock panels', 'Lock layout', 'Edit Toolbars']
    assert [a.shortcut().toString() for a in actions[:8]] == [
        'F5', '', 'Shift+F1', 'Shift+F3', 'Shift+F5', 'Shift+F4', 'Shift+F2', 'Shift+F7']
    assert all(a.menu() is not None and a.isEnabled() for a in actions[9:14])


def test_panel_shortcuts_reveal_focus_and_restore_collapsed_panels(window):
    visible(window)
    for key, panel_key, widget in (
        (Qt.Key.Key_F2, 'ftp', window.ftp_log),
        (Qt.Key.Key_F4, 'transfer', window.transfer_log),
        (Qt.Key.Key_F5, 'system', window.log),
        (Qt.Key.Key_F7, 'tasks', window.tasks_log)):
        window.log_tabs.hide()
        window.queue.setFocus()
        QTest.keyClick(window.queue, key, Qt.KeyboardModifier.ShiftModifier)
        assert window.log_tabs.isVisible()
        assert window.log_tabs.currentWidget() is window.log_panels[panel_key]
        assert widget.hasFocus()
    window.queue_panel.hide()
    window.queue.hide()
    QTest.keyClick(window.tasks_log, Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier)
    assert window.queue_panel.isVisible() and window.queue.isVisible() and window.queue.hasFocus()
    window.workspace_panel.hide()
    QTest.keyClick(window.queue, Qt.Key.Key_F1, Qt.KeyboardModifier.ShiftModifier)
    assert window.workspace_panel.isVisible() and window.current_panes()[0].tree.hasFocus()
    window.work_area.setSizes([800, 0, 100])
    window.view_commands.show_panel('ftp')
    assert window.work_area.sizes()[1] > 0


def test_refresh_targets_last_active_pane_without_duplicate_shortcuts(window, monkeypatch):
    visible(window)
    left, right = window.current_panes()
    refreshed = []
    monkeypatch.setattr(left, 'refresh', lambda: refreshed.append('left'))
    monkeypatch.setattr(right, 'refresh', lambda: refreshed.append('right'))
    right.address.setFocus()
    QTest.keyClick(right.address, Qt.Key.Key_F5)
    assert refreshed == ['right']
    window.view_commands.show_panel('system')
    window.view_commands.actions['Refresh'].trigger()
    assert refreshed == ['right', 'right']
    left.tree.setFocus()
    QTest.keyClick(left.tree, Qt.Key.Key_F5)
    assert refreshed == ['right', 'right', 'left']


def test_current_edit_files_tracks_only_editors_and_preserves_sessions(window, tmp_path, monkeypatch):
    site = Site(name='Example', host='example.test')
    options = TransferOptions.from_settings(window.preferences)
    editor = RemoteTextDialog(window, site, 'secret', '/text.txt', b'text', options, 1, True)
    viewer = RemoteTextDialog(window, site, 'secret', '/view.txt', b'text', options, 1, False)
    temporary = TemporaryDirectory(dir=tmp_path)
    local = Path(temporary.name) / 'binary.dat'
    local.write_bytes(b'binary')
    external = ExternalFileSession(window, site, 'secret', '/binary.dat', options, 1, {}, True,
                                   temporary, local, b'baseline')
    for session in (editor, viewer, external):
        window.retain_mouse_window(session)
    editor.editor.insertPlainText('changed')
    editor.showMinimized()
    window.view_commands.show_edit_files()
    dialog = window.view_commands.edit_dialog
    assert dialog.files.topLevelItemCount() == 2
    assert dialog.files.topLevelItem(0).text(3) == 'Modified'
    dialog.files.setCurrentItem(dialog.files.topLevelItem(0))
    dialog.open_selected()
    assert editor.isVisible() and not editor.isMinimized()
    dialog.close()
    assert editor.editor.document().isModified() and local.exists()
    window.view_commands.show_edit_files()
    assert window.view_commands.edit_dialog is dialog
    editor.editor.document().setModified(False)
    editor.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    dialog.refresh()
    assert dialog.files.topLevelItemCount() == 1
    assert dialog.files.topLevelItem(0).text(1) == '/binary.dat'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    external.close()
    viewer.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    dialog.refresh()
    assert dialog.files.topLevelItemCount() == 0 and not dialog.open_button.isEnabled()
    dialog.close()
