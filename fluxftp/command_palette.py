"""Keyboard-first command, site, folder, deployment and workspace switcher."""
import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem
from PySide6.QtWidgets import QInputDialog


class CommandPalette(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('FluxFTP Command Palette')
        self.setModal(True)
        self.resize(680, 440)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText('Search commands, sites, folders, profiles, and workspaces…')
        self.search.setAccessibleName('Command palette search')
        layout.addWidget(self.search)
        self.items = QListWidget(self)
        self.items.setAccessibleName('Command palette results')
        layout.addWidget(self.items)
        self.entries = []
        self.search.textChanged.connect(self.filter)
        self.search.returnPressed.connect(self.activate_current)
        self.items.itemActivated.connect(lambda _: self.activate_current())
        self.shortcut = QShortcut(QKeySequence('Ctrl+Shift+P'), window)
        self.shortcut.activated.connect(self.open)

    def open(self):
        self.rebuild()
        self.search.clear()
        self.filter('')
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def rebuild(self):
        self.entries = []
        def add(label, category, callback):
            self.entries.append((category, label, callback))
        def actions(menu, prefix=''):
            for action in menu.actions():
                if action.isSeparator():
                    continue
                text = action.text().replace('&', '').replace('…', '').strip()
                sub = action.menu()
                if sub:
                    actions(sub, prefix + text + ' / ')
                elif action.isEnabled() and action.text():
                    add(prefix + text, 'Command', action.trigger)
        # QMenuBar.actions() gives the actual top-level menu actions.
        for top in self.window.menuBar().actions():
            if top.menu():
                actions(top.menu(), top.text().replace('&', '') + ' / ')
        for name, profile in sorted(self.window.sites.items(), key=lambda pair: pair[0].casefold()):
            if isinstance(profile, dict):
                add(name, 'Site', lambda name=name: self._connect(name))
        for pane in self.window.current_panes():
            if pane.path:
                add(f'Pane {pane.number}: {pane.path}', 'Folder', lambda pane=pane: self._navigate(pane))
            history = pane.address.history().get(pane.address.scope(), []) if pane.address.scope() else []
            for address in history if isinstance(history, list) else []:
                if isinstance(address, str) and address and address != pane.path:
                    add(f'Pane {pane.number}: {address}', 'Folder', lambda pane=pane, path=address: pane.navigate(path))
        profile_path = self.window.sites_file.with_name('deployment-profiles.json')
        try:
            profiles = json.loads(profile_path.read_text('utf-8')) if profile_path.exists() else {}
            for name in profiles:
                add(name, 'Deployment profile', lambda: self._deployments())
        except (OSError, ValueError, TypeError):
            pass
        for index in range(self.window.tabs.count()):
            name = self.window.tabs.tabText(index)
            add(name, 'Workspace', lambda index=index: self.window.tabs.setCurrentIndex(index))
        add('Save current workspace as preset…', 'Workspace preset', self.save_preset)
        try:
            presets = json.loads(self.window.preferences.value('Workspaces/presets', '{}', type=str))
            if isinstance(presets, dict):
                for name in sorted(presets, key=str.casefold):
                    add(name, 'Workspace preset', lambda name=name: self.apply_preset(name))
        except (ValueError, TypeError):
            pass
        for category, label, callback in self.entries:
            item = QListWidgetItem(f'{category}  ·  {label}')
            item.setData(Qt.ItemDataRole.UserRole, callback)
            self.items.addItem(item)

    def filter(self, value):
        query = value.casefold().strip()
        visible = []
        for index, (category, label, _) in enumerate(self.entries):
            match = not query or query in category.casefold() or query in label.casefold()
            self.items.item(index).setHidden(not match)
            if match:
                visible.append(self.items.item(index))
        if visible:
            self.items.setCurrentItem(visible[0])

    def activate_current(self):
        item = self.items.currentItem()
        if item and not item.isHidden():
            callback = item.data(Qt.ItemDataRole.UserRole)
            self.hide()
            callback()

    def _connect(self, name):
        pane = self.window.mark_commands.pane()
        if pane:
            pane.connect_saved_site(name)

    def _navigate(self, pane):
        pane.navigate(pane.path)

    def _deployments(self):
        from .deployment_ui import show_deployments
        show_deployments(self.window)

    def save_preset(self):
        name, accepted = QInputDialog.getText(self, 'Save workspace preset', 'Preset name:')
        name = name.strip()
        if not accepted or not name:
            return
        split = self.window.tabs.currentWidget()
        presets = json.loads(self.window.preferences.value('Workspaces/presets', '{}', type=str))
        presets[name] = dict(modes=[split.widget(i).mode for i in range(2)],
                             splitter=bytes(split.saveState()).hex())
        self.window.preferences.setValue('Workspaces/presets', json.dumps(presets))
        self.window.statusBar().showMessage(f'Saved workspace preset “{name}”.', 4000)

    def apply_preset(self, name):
        try:
            presets = json.loads(self.window.preferences.value('Workspaces/presets', '{}', type=str))
            preset = presets[name]
            if not isinstance(preset, dict) or len(preset.get('modes', [])) != 2:
                raise ValueError
            self.window.add_workspace()
            index = self.window.tabs.currentIndex()
            split = self.window.tabs.widget(index)
            for side, mode in enumerate(preset['modes']):
                if mode in ('Local', 'Remote'):
                    split.widget(side).change_mode(mode)
            self.window.tabs.setTabText(index, name[:80])
            try:
                split.restoreState(bytes.fromhex(preset['splitter']))
            except (ValueError, TypeError):
                pass
            self.window.save_workspaces()
            self.window.refresh_workspace_names()
        except (KeyError, ValueError, TypeError):
            self.window.statusBar().showMessage('That workspace preset is invalid.', 5000)
