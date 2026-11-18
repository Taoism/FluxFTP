"""Editable shortcuts for the actions exposed by the main menu bar."""
import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QKeySequenceEdit, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout)


SCOPES = ('Global', 'Remote', 'Local', 'Queue', 'Local, Remote')


def _scope_set(value):
    parts = {part.strip() for part in value.split(',')}
    return {'Global', 'Remote', 'Local', 'Queue'} if 'Global' in parts else parts


def scopes_overlap(left, right):
    return bool(_scope_set(left) & _scope_set(right))


def _shortcut_key(sequence):
    return sequence.toString(QKeySequence.SequenceFormat.PortableText).casefold()


def _walk_menu(menu, prefix=()):
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            yield from _walk_menu(submenu, prefix + (submenu.title(),))
        elif not action.isSeparator():
            label = action.text().replace('&', '').replace('…', '...').strip()
            if label:
                yield '/'.join(prefix + (label,)), action


def _default_scope(action_id):
    parts = action_id.casefold().split('/')
    if parts[-1].startswith('options') or parts[-1].startswith('hotkey manager'):
        return 'Global'
    if any('queue' in part for part in parts):
        return 'Queue'
    if any(part in ('ftp', 'remote') for part in parts):
        return 'Remote'
    if any(part in ('local', 'explorer', 'local drives') for part in parts):
        return 'Local'
    if parts[0] in ('edit', 'mark'):
        return 'Local, Remote'
    return 'Global'


class HotkeyManager:
    """Collect, persist and apply user shortcut bindings."""
    def __init__(self, window):
        self.window = window
        self.bindings = []
        seen = set()
        for action_id, action in _walk_menu(window.menuBar()):
            if id(action) in seen:
                continue
            seen.add(id(action))
            default = action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
            self.bindings.append({'id': action_id, 'name': action_id.replace('/', ' › '), 'action': action,
                                  'default': default, 'default_scope': _default_scope(action_id)})
        counts = {}
        for action in window.findChildren(QAction):
            if id(action) in seen or not action.text().strip():
                continue
            base = action.text().replace('&', '').replace('…', '...').strip()
            counts[base] = counts.get(base, 0) + 1
            action_id = f"Window/{base}" + (f" #{counts[base]}" if counts[base] > 1 else '')
            seen.add(id(action))
            default = action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
            self.bindings.append({'id': action_id, 'name': action_id.replace('/', ' › '), 'action': action,
                                  'default': default, 'default_scope': _default_scope(action_id)})
        self.dialog = None

    def initialize(self):
        self._apply_saved()

    def _saved(self):
        try:
            value = self.window.preferences.value('Hotkeys/overrides', '{}', type=str)
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}

    def _apply_saved(self):
        saved = self._saved()
        for binding in self.bindings:
            value = saved.get(binding['id'], {})
            shortcut = value.get('shortcut', binding['default']) if isinstance(value, dict) else binding['default']
            scope = value.get('scope', binding['default_scope']) if isinstance(value, dict) else binding['default_scope']
            self._apply_binding(binding, shortcut, scope)

    def _apply_binding(self, binding, shortcut, scope):
        action = binding['action']
        for index in range(getattr(self.window, 'tabs', None).count() if hasattr(self.window, 'tabs') else 0):
            split = self.window.tabs.widget(index)
            for side in range(split.count()):
                split.widget(side).tree.removeAction(action)
        if hasattr(self.window, 'queue'):
            self.window.queue.removeAction(action)
        action.setShortcut(QKeySequence(shortcut))
        if scope == 'Global':
            # Global means available throughout the application. Qt's
            # WindowShortcut can miss keys while a child widget has focus on
            # Linux, so use the application-level context for this scope.
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            self.window.addAction(action)
            return
        self.window.removeAction(action)
        action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        for index in range(self.window.tabs.count()):
            split = self.window.tabs.widget(index)
            for side in range(split.count()):
                self.refresh_pane(split.widget(side), binding, scope)
        if 'Queue' in _scope_set(scope):
            self.window.queue.addAction(action)

    def attach_pane(self, pane):
        for binding in self.bindings:
            data = self._saved().get(binding['id'], {})
            scope = data.get('scope', binding['default_scope']) if isinstance(data, dict) else binding['default_scope']
            self.refresh_pane(pane, binding, scope)

    @staticmethod
    def refresh_pane(pane, binding, scope):
        pane.tree.removeAction(binding['action'])
        if pane.mode in _scope_set(scope):
            pane.tree.addAction(binding['action'])

    def show(self):
        if self.dialog is None:
            self.dialog = HotkeyDialog(self)
        self.dialog.reload()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def apply(self, values):
        conflicts = []
        for index, current in enumerate(values):
            shortcut = current['shortcut']
            key = _shortcut_key(QKeySequence(shortcut))
            if not key:
                continue
            for other in values[index + 1:]:
                other_key = _shortcut_key(QKeySequence(other['shortcut']))
                if key == other_key and scopes_overlap(current['scope'], other['scope']):
                    conflicts.append(f"{current['name']} ({current['scope']}) and {other['name']} ({other['scope']}) use {shortcut}")
        if conflicts:
            QMessageBox.warning(self.dialog, 'Shortcut conflict', 'Resolve overlapping shortcuts before saving:\n\n' + '\n'.join(conflicts))
            return False
        saved = {}
        for binding, current in zip(self.bindings, values):
            self._apply_binding(binding, current['shortcut'], current['scope'])
            if current['shortcut'] != binding['default'] or current['scope'] != binding['default_scope']:
                saved[binding['id']] = {'shortcut': current['shortcut'], 'scope': current['scope']}
        self.window.preferences.setValue('Hotkeys/overrides', json.dumps(saved, ensure_ascii=False, sort_keys=True))
        self.window.preferences.sync()
        return True

    def restore_defaults(self):
        self.window.preferences.remove('Hotkeys/overrides')
        for binding in self.bindings:
            self._apply_binding(binding, binding['default'], binding['default_scope'])
        self.window.preferences.sync()


class HotkeyDialog(QDialog):
    def __init__(self, manager):
        super().__init__(manager.window)
        self.manager = manager
        self.setWindowTitle('Hotkey Manager')
        self.resize(900, 640)
        layout = QVBoxLayout(self)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel('Search'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search actions or groups…')
        self.search.setAccessibleName('Search hotkey actions')
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Name', 'Hotkey', 'Apply to'])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.clear_button = QPushButton('Clear selected shortcut')
        self.defaults_button = QPushButton('Restore factory defaults')
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.defaults_button)
        buttons.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.addWidget(self.buttons)
        layout.addLayout(buttons)
        self.search.textChanged.connect(self.filter_rows)
        self.clear_button.clicked.connect(self.clear_selected)
        self.defaults_button.clicked.connect(self.restore_defaults)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        self.reload()

    def reload(self):
        saved = self.manager._saved()
        self.table.setRowCount(len(self.manager.bindings))
        for row, binding in enumerate(self.manager.bindings):
            data = saved.get(binding['id'], {})
            shortcut = data.get('shortcut', binding['default']) if isinstance(data, dict) else binding['default']
            scope = data.get('scope', binding['default_scope']) if isinstance(data, dict) else binding['default_scope']
            name = QTableWidgetItem(binding['name'])
            name.setData(Qt.ItemDataRole.UserRole, binding['id'])
            self.table.setItem(row, 0, name)
            editor = QKeySequenceEdit(QKeySequence(shortcut))
            editor.setAccessibleName(f"Hotkey for {binding['name']}")
            self.table.setCellWidget(row, 1, editor)
            combo = QComboBox()
            combo.addItems(SCOPES)
            combo.setCurrentText(scope if scope in SCOPES else binding['default_scope'])
            combo.setAccessibleName(f"Apply {binding['name']} to")
            self.table.setCellWidget(row, 2, combo)
            self.table.setRowHeight(row, 30)
        self.table.resizeColumnToContents(0)
        self.filter_rows(self.search.text())

    def filter_rows(self, query):
        terms = query.casefold().split()
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().casefold()
            self.table.setRowHidden(row, not all(term in name for term in terms))

    def clear_selected(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.cellWidget(row, 1).clear()

    def restore_defaults(self):
        self.manager.restore_defaults()
        self.reload()

    def save(self):
        values = []
        for row, binding in enumerate(self.manager.bindings):
            values.append({'id': binding['id'], 'name': binding['name'],
                           'shortcut': self.table.cellWidget(row, 1).keySequence().toString(
                               QKeySequence.SequenceFormat.PortableText),
                           'scope': self.table.cellWidget(row, 2).currentText()})
        if self.manager.apply(values):
            self.accept()
