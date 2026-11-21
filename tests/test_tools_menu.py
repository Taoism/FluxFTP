from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from test_queue import window


def test_tools_reference_and_shortcuts(window, monkeypatch):
    assert [a.text() if not a.isSeparator() else '---' for a in window.tools_menu.actions()] == [
        'Queue', 'Transfer History…', 'JSON API Access…', 'Unified Bandwidth Management…', '---', 'Site Manager…', 'Certification Checker…', 'Enable Debug Logging…', 'Connection Diagnostics…', 'SSHFS Mounts…', 'Add to site manager…', '---', 'Command',
        'Command Designer…', 'Send Raw Command…', '---', 'Compare folder contents\tCtrl+D',
        'Synchronize Folders…', 'Deployment Profiles…', 'View RAW Directory…', '---', 'Watch-and-upload Folders…', 'Script Designer…', 'Scheduled Automation…', '---', 'Languages', '---',
        'Layout', '---', 'Hotkey Manager…', 'Manual Download from Site…', 'TFTP Transfer…', '---',
        'Clipboard', '---', 'Transfer Mode', 'On Transfer Completed', '---',
        'Create SFV or MD5 file…', 'IP Address Translate…', '---', 'Restart UPnP service…',
        '---', 'Options…']
    commands = window.tools_commands
    assert commands.actions['Site Manager…'].shortcut().toString() == 'F3'
    assert commands.actions['Options…'].shortcut().toString() == 'F6'
    assert commands.actions['Options…'].shortcutContext() == Qt.ShortcutContext.ApplicationShortcut
    assert len([a for a in window.findChildren(QAction) if a.shortcut().toString() == 'F3']) == 1
    assert commands.actions['Script Designer…'].isEnabled()
    assert commands.actions['Scheduled Automation…'].isEnabled()
    assert window.wizard_action.isEnabled()
    assert commands.actions['Hotkey Manager…'].isEnabled()
    for label in ('View RAW Directory…', 'Restart UPnP service…'):
        assert not commands.actions[label].isEnabled()
    # Exercise the actual key binding without opening a modal dialog.
    calls = []
    action = commands.actions['Options…']
    action.triggered.disconnect()
    action.triggered.connect(lambda: calls.append('options'))
    window.show()
    window.activateWindow()
    window.current_panes()[0].tree.setFocus()
    QTest.qWait(20)
    QTest.keyClick(window.current_panes()[0].tree, Qt.Key.Key_F6)
    assert calls == ['options']


def test_tools_shared_preferences_and_layout(window):
    commands = window.tools_commands
    for key, value in [('Transfer/mode', 'ASCII'), ('Transfer/completed', 'Disconnect')]:
        action = next(a for a, k, v, _ in commands.choices if k == key and v == value)
        action.trigger()
        assert window.preferences.value(key) == value
    window.layout_commands.pane_arrangement_actions['Stacked'].trigger()
    assert window.preferences.value('Display/pane_layout') == 'Stacked'
    assert window.tabs.currentWidget().orientation() == Qt.Orientation.Vertical
    window.preferences.setValue('Transfer/mode', 'Auto')
    commands.update()
    assert next(a for a, k, v, _ in commands.choices if k == 'Transfer/mode' and v == 'Auto').isChecked()
    assert not commands.paste.isEnabled()


def test_tools_debug_logging_starts_and_exposes_log_path(window):
    from fluxftp import diagnostics
    import sys

    previous_hook = sys.excepthook
    try:
        window.tools_commands.actions['Enable Debug Logging…'].trigger()
        path = diagnostics.log_path
        assert path is not None and path.is_file()
        assert path.parent.name == 'logs'
        text = path.read_text('utf-8')
        assert 'Debug logging enabled from the Tools menu.' in text
        assert 'Qt runtime: version=' in text
        assert 'screens=' in text
        from PySide6.QtCore import qWarning
        qWarning('diagnostic test warning')
        assert 'Qt runtime [default]: diagnostic test warning' in path.read_text('utf-8')
        assert window._debug_folder_button.text() == 'Open debug logs folder'
        window._debug_copy_button.click()
        assert window.app_instance.clipboard().text() == str(path)
        assert not window.tools_commands.debug_logging_action.isEnabled()
    finally:
        diagnostics.close_debug()
        assert sys.excepthook is previous_hook
