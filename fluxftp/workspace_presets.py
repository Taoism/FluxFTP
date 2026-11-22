"""Quick save, switch and remove named site/folder workspace presets."""
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)


class WorkspacePresetDialog(QDialog):
    """Manage named snapshots of workspace tabs and application layout."""
    SETTINGS_KEY = 'Workspaces/presets'
    MAX_PRESETS = 50
    MAX_BYTES = 2_000_000

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Workspace Presets')
        self.setMinimumSize(560, 380)
        layout = QVBoxLayout(self)
        self.help = QLabel('Save site pairs, folders, tabs and layout for a quick switch. Passwords are never included.')
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        self.list = QListWidget()
        self.list.setAccessibleName('Saved workspace presets')
        self.list.itemDoubleClicked.connect(lambda _item: self.apply_selected())
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        for label, callback in (('Save Current…', self.save_current), ('Apply', self.apply_selected),
                                ('Delete', self.delete_selected), ('Close', self.accept)):
            button = QPushButton(label)
            if label == 'Apply':
                button.setObjectName('primary')
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh()

    def presets(self):
        raw = self.window.preferences.value(self.SETTINGS_KEY, '{}', type=str)
        if len(raw.encode('utf-8')) > self.MAX_BYTES:
            raise ValueError('Saved workspace presets exceed the 2 MB limit.')
        result = json.loads(raw)
        if not isinstance(result, dict) or len(result) > self.MAX_PRESETS:
            raise ValueError('Saved workspace presets are invalid.')
        normalized_names = set()
        for name, preset in result.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                raise ValueError('A saved workspace preset has an invalid name.')
            self.window.validate_workspace_preset(preset)
            if preset['name'] != name or name.casefold() in normalized_names:
                raise ValueError('Saved workspace preset names must be unique and match their records.')
            normalized_names.add(name.casefold())
        return result

    def refresh(self, selected=None):
        self.list.clear()
        try:
            values = self.presets()
        except (ValueError, TypeError, UnicodeError) as error:
            QMessageBox.warning(self, 'Workspace Presets', str(error))
            values = {}
        for name in sorted(values, key=str.casefold):
            preset = values[name]
            count = len(preset['items'])
            item = QListWidgetItem(f'{name}  ·  {count} tab{"s" if count != 1 else ""}')
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)
        if selected:
            for index in range(self.list.count()):
                item = self.list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == selected:
                    self.list.setCurrentItem(item)
                    break

    def selected_name(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ''

    def save_current(self):
        values = self.presets()
        name, accepted = QInputDialog.getText(self, 'Save Workspace Preset', 'Preset name (1–80 characters):')
        if not accepted:
            return
        name = name.strip()
        if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
            QMessageBox.warning(self, 'Workspace Presets', 'Enter a name containing 1–80 characters without control characters.')
            return
        existing = next((item for item in values if item.casefold() == name.casefold()), None)
        if len(values) >= self.MAX_PRESETS and existing is None:
            QMessageBox.warning(self, 'Workspace Presets', f'At most {self.MAX_PRESETS} presets can be saved.')
            return
        if existing and QMessageBox.question(self, 'Replace Workspace Preset',
                f'Replace “{existing}” with the current tabs, folders and layout?') != QMessageBox.StandardButton.Yes:
            return
        try:
            values.pop(existing, None)
            values[name] = self.window.workspace_preset_data(name)
            encoded = json.dumps(values, ensure_ascii=False, separators=(',', ':'))
            if len(encoded.encode('utf-8')) > self.MAX_BYTES:
                raise ValueError('Workspace presets exceed the 2 MB storage limit.')
            self.window.preferences.setValue(self.SETTINGS_KEY, encoded)
            self.window.preferences.sync()
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, 'Workspace Presets', str(error))
            return
        self.refresh(name)

    def apply_selected(self):
        name = self.selected_name()
        if not name:
            return
        try:
            preset = self.presets()[name]
            if self.window.apply_workspace_preset(preset):
                self.accept()
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
            QMessageBox.warning(self, 'Workspace Presets', f'Could not apply “{name}”: {error}')

    def delete_selected(self):
        name = self.selected_name()
        if not name:
            return
        if QMessageBox.question(self, 'Delete Workspace Preset', f'Delete “{name}”?') != QMessageBox.StandardButton.Yes:
            return
        values = self.presets()
        values.pop(name, None)
        self.window.preferences.setValue(self.SETTINGS_KEY, json.dumps(values, ensure_ascii=False))
        self.window.preferences.sync()
        self.refresh()


def show_workspace_presets(window):
    dialog = WorkspacePresetDialog(window)
    dialog.exec()
    dialog.deleteLater()
