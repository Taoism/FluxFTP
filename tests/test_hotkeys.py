import json

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMessageBox

from fluxftp.hotkeys import HotkeyDialog, scopes_overlap
from test_queue import window


def _binding(manager, action_id):
    return next(item for item in manager.bindings if item['id'] == action_id)


def test_manager_lists_actions_scopes_and_searches(window):
    manager = window.hotkey_manager
    dialog = HotkeyDialog(manager)
    names = [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())]
    assert any(name.startswith('Edit › Rename') for name in names)
    assert any(name.startswith('Tools › Queue › Transfer Queue') for name in names)
    assert any(name.startswith('Tools › Script Designer') for name in names)
    edit_row = next(row for row, name in enumerate(names) if name.startswith('Edit › Rename'))
    assert dialog.table.cellWidget(edit_row, 2).currentText() == 'Local, Remote'
    assert scopes_overlap('Global', 'Queue')
    assert not scopes_overlap('Local', 'Queue')
    dialog.search.setText('script designer')
    assert sum(not dialog.table.isRowHidden(row) for row in range(dialog.table.rowCount())) == 1
    dialog.close()


def test_shortcuts_persist_clear_and_restore_factory_defaults(window):
    manager = window.hotkey_manager
    binding = _binding(manager, 'File/New/New Window')
    assert binding['default'] == 'Ctrl+N'
    values = [{'id': item['id'], 'name': item['name'], 'shortcut': item['default'],
               'scope': item['default_scope']} for item in manager.bindings]
    target = next(value for value in values if value['id'] == binding['id'])
    target['shortcut'] = 'Ctrl+Alt+N'
    assert manager.apply(values)
    assert binding['action'].shortcut().toString(QKeySequence.SequenceFormat.PortableText) == 'Ctrl+Alt+N'
    saved = json.loads(window.preferences.value('Hotkeys/overrides', '{}', type=str))
    assert saved[binding['id']]['shortcut'] == 'Ctrl+Alt+N'
    target['shortcut'] = ''
    assert manager.apply(values)
    assert not binding['action'].shortcut().toString()
    manager.restore_defaults()
    assert binding['action'].shortcut().toString(QKeySequence.SequenceFormat.PortableText) == 'Ctrl+N'
    assert not window.preferences.contains('Hotkeys/overrides')


def test_conflicting_shortcuts_are_explained_and_not_applied(window, monkeypatch):
    manager = window.hotkey_manager
    first = _binding(manager, 'File/New/New Window')
    second = _binding(manager, 'Help/About FluxFTP...')
    messages = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: messages.append(args))
    values = [{'id': item['id'], 'name': item['name'], 'shortcut': item['default'],
               'scope': item['default_scope']} for item in manager.bindings]
    values_by_id = {value['id']: value for value in values}
    values_by_id[first['id']]['shortcut'] = 'Ctrl+Alt+9'
    values_by_id[second['id']]['shortcut'] = 'Ctrl+Alt+9'
    assert not manager.apply(values)
    assert messages and 'Shortcut conflict' in messages[0][1]
    assert first['action'].shortcut().toString(QKeySequence.SequenceFormat.PortableText) != 'Ctrl+Alt+9'


def test_key_sequence_editor_can_clear_selected_shortcut(window):
    dialog = HotkeyDialog(window.hotkey_manager)
    row = next(row for row in range(dialog.table.rowCount())
               if dialog.table.item(row, 0).text().startswith('File › New › New Window'))
    editor = dialog.table.cellWidget(row, 1)
    assert editor.keySequence().toString() == 'Ctrl+N'
    dialog.table.setCurrentCell(row, 0)
    dialog.clear_selected()
    assert not editor.keySequence().toString()
    dialog.close()
