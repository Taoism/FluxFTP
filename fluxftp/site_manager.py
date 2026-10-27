from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView,
    QDialogButtonBox, QMessageBox, QPlainTextEdit, QMenu, QComboBox, QWidget,
    QStackedWidget, QInputDialog, QApplication)
from PySide6.QtGui import QKeySequence, QShortcut

class ImportDialog(QDialog):
    def __init__(self, parent, result):
        super().__init__(parent)
        self.setWindowTitle("Preview site import")
        self.resize(920, 560)
        self.result = result
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{result.source} · {len(result.sites)} valid sites · {result.skipped} skipped"))
        note = QLabel("Choose sites to add. Existing names are preserved; duplicates receive a numbered suffix. Passwords are not imported.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(True)
        self.tree.setHeaderLabels(["Import / name", "Host", "Port", "Protocol", "Username"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for site in result.sites:
            row = QTreeWidgetItem([site.name, site.host, str(site.port), site.protocol, site.username])
            row.setCheckState(0, Qt.CheckState.Checked)
            self.tree.addTopLevelItem(row)
        layout.addWidget(self.tree, 3)
        selection = QHBoxLayout()
        for title, state in [("Select all", Qt.CheckState.Checked), ("Select none", Qt.CheckState.Unchecked)]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, value=state: self.check_all(value))
            selection.addWidget(button)
        selection.addStretch()
        layout.addLayout(selection)
        notices = QPlainTextEdit()
        notices.setReadOnly(True)
        notices.setPlainText("\n".join(result.warnings))
        layout.addWidget(notices, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.import_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.import_button.setText("Import selected")
        self.import_button.setEnabled(bool(result.sites))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.tree.itemChanged.connect(lambda *_: self.import_button.setEnabled(bool(self.selected_sites())))
        layout.addWidget(buttons)

    def check_all(self, state):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)

    def selected_sites(self):
        return [site for i, site in enumerate(self.result.sites)
                if self.tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]


class SitesDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.connection = None
        self.setWindowTitle("Site Manager")
        self.resize(1000, 640)
        outer = QVBoxLayout(self)
        title = QLabel("Sites")
        title.setObjectName("brand")
        outer.addWidget(title)
        body = QHBoxLayout()
        outer.addLayout(body, 1)

        self.navigation = QTreeWidget()
        self.navigation.setObjectName("siteNavigation")
        self.navigation.setAccessibleName('Site Manager categories')
        self.navigation.setHeaderHidden(True)
        self.navigation.setFixedWidth(185)
        self.sites_nav = QTreeWidgetItem(["Saved sites"])
        self.navigation.addTopLevelItem(self.sites_nav)
        self.navigation.setCurrentItem(self.sites_nav)
        body.addWidget(self.navigation)

        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        sites_page = QWidget()
        sites_layout = QVBoxLayout(sites_page)
        sites_layout.setContentsMargins(12, 0, 0, 0)
        sites_heading = QLabel("Saved sites")
        sites_heading.setStyleSheet("font-size:18px; font-weight:600;")
        sites_layout.addWidget(sites_heading)
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Connect selected site to:"))
        self.connect_target = QComboBox()
        self.connect_target.setAccessibleName('Connect selected site to pane')
        self.connect_target.addItems(["Left pane", "Right pane"])
        target_row.addWidget(self.connect_target)
        self.connect_button = QPushButton('Connect')
        self.connect_button.clicked.connect(lambda: self.connect_to(self.connect_target.currentIndex()))
        target_row.addWidget(self.connect_button)
        target_row.addStretch()
        sites_layout.addLayout(target_row)
        self.search = QLineEdit()
        self.search.setAccessibleName('Search saved sites')
        self.search.setPlaceholderText("Search names, hosts, usernames…")
        self.search.textChanged.connect(self.filter)
        sites_layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setAccessibleName('Saved sites')
        self.tree.itemSelectionChanged.connect(self.selection_changed)
        self.tree.setHeaderLabels(["Name", "Host", "Port", "Protocol", "Username"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self.single_click_site)
        self.tree.itemClicked.connect(self.schedule_site_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)
        self.tree.itemDoubleClicked.connect(self.double_click_site)
        for sequence, callback in ((QKeySequence('Ctrl+A'), self.select_all_sites),
                                   (QKeySequence("F2"), self.rename),
                                   (QKeySequence(Qt.Key.Key_Delete), self.delete),
                                   (QKeySequence("Alt+Return"), self.edit)):
            shortcut = QShortcut(sequence, self.tree)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._site_shortcuts = getattr(self, "_site_shortcuts", []) + [shortcut]
        sites_layout.addWidget(self.tree, 1)
        self.status = QLabel()
        sites_layout.addWidget(self.status)
        actions = QHBoxLayout()
        for label, callback in (("New site…", self.new), ("Edit selected…", self.edit), ("Delete selected", self.delete)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        sites_layout.addLayout(actions)
        formats = QLabel("Import and export saved sites from Options → Sites. Supported imports: FileZilla, WinSCP, FTP Rush, FlashFXP, CSV, and FluxFTP JSON")
        formats.setObjectName("muted")
        formats.setWordWrap(True)
        sites_layout.addWidget(formats)
        self.pages.addWidget(sites_page)

        self.navigation.currentItemChanged.connect(self.navigation_changed)

        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        outer.addLayout(footer)
        self.refresh()

    def schedule_site_click(self, item, _column):
        # Single clicks belong to selection, including Ctrl/Shift range selection.
        self.click_timer.stop()

    def single_click_site(self):
        self.click_timer.stop()

    def selection_changed(self):
        selected = self.selected()
        if hasattr(self, 'status'):
            self.status.setText(f'{len(self.window.sites)} saved sites · {len(selected)} selected · Ctrl/Shift-click to select · right-click for actions')
        self.connect_button.setEnabled(len(selected) == 1 and not self.window.sites[selected[0]].get('_disabled'))

    def select_all_sites(self):
        self.click_timer.stop()
        self.tree.clearSelection()
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            row = iterator.value()
            if isinstance(row.data(0, Qt.ItemDataRole.UserRole), str) and not row.isHidden() and not (row.parent() and row.parent().isHidden()):
                row.setSelected(True)
            iterator += 1

    def double_click_site(self, item, _column):
        self.click_timer.stop()
        if not isinstance(item.data(0, Qt.ItemDataRole.UserRole), str):
            return
        self.tree.clearSelection()
        self.tree.setCurrentItem(item)
        item.setSelected(True)
        if self.window.mouse_action('site') == 'Connect to Site':
            self.connect_to(self.connect_target.currentIndex(), item.data(0, Qt.ItemDataRole.UserRole))
        else:
            self.edit()

    def done(self, result):
        self.click_timer.stop()
        super().done(result)

    def navigation_changed(self, item, _previous):
        if item is not None:
            self.pages.setCurrentIndex(0)

    def show_context_menu(self, position):
        self.click_timer.stop()
        row = self.tree.itemAt(position)
        if row is None:
            self.tree.clearSelection()
        elif row not in self.tree.selectedItems():
            self.tree.clearSelection()
            row.setSelected(True)
        self._build_context_menu(row).exec(self.tree.viewport().mapToGlobal(position))

    def _build_context_menu(self, row=None):
        menu = QMenu(self)
        menu.addAction("New Folder", self.new_folder)
        menu.addAction("New Site", self.new)
        menu.addSeparator()
        selected = self.selected()
        disabled = bool(selected) and all(self.window.sites[name].get('_disabled', False) for name in selected)
        toggle = menu.addAction("Disabled")
        toggle.setCheckable(True)
        toggle.setChecked(disabled)
        toggle.setEnabled(bool(selected))
        toggle.setToolTip('Apply disabled state to all selected sites')
        toggle.triggered.connect(self.toggle_disabled)
        menu.addSeparator()
        connect = menu.addAction("Connect", lambda: self.connect_to(self.connect_target.currentIndex()))
        connect.setEnabled(len(selected) == 1 and not disabled)
        panes = menu.addMenu('Connect to')
        panes.setEnabled(len(selected) == 1 and not disabled)
        panes.addAction('Left pane', lambda: self.connect_to(0))
        panes.addAction('Right pane', lambda: self.connect_to(1))
        rename = menu.addAction("Rename", self.rename)
        rename.setShortcut("F2")
        rename.setEnabled(len(selected) == 1 or self.selected_folder() is not None)
        delete = menu.addAction("Delete", self.delete)
        delete.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        delete.setEnabled(bool(selected) or self.selected_folder() is not None)
        move = menu.addMenu('Move to Folder')
        move.setEnabled(bool(selected))
        move.addAction('New Folder…', self.new_folder)
        move.addSeparator()
        move.addAction('Saved sites (root)', lambda: self.move_sites(''))
        for folder in self.folders():
            move.addAction(folder, lambda checked=False, name=folder: self.move_sites(name))
        menu.addSeparator()
        copy = menu.addAction("Copy Sites" if len(selected) > 1 else "Copy Site", self.copy_site)
        copy.setEnabled(bool(selected))
        menu.addAction("Test…", self.test_site).setEnabled(len(selected) == 1)
        properties = menu.addAction("Site Properties", self.edit)
        properties.setShortcut("Alt+Enter")
        properties.setEnabled(len(selected) == 1)
        menu.addSeparator()
        menu.addAction('Select All', self.select_all_sites)
        menu.addAction('Clear Selection', self.tree.clearSelection)
        return menu

    def refresh(self):
        selection = set(self.selected())
        self.tree.clear()
        folders = self.folders()
        folder_rows = {}
        for folder in sorted(folders, key=str.casefold):
            group = QTreeWidgetItem([folder])
            group.setData(0, Qt.ItemDataRole.UserRole, ("folder", folder))
            self.tree.addTopLevelItem(group)
            folder_rows[folder] = group
        for name, data in sorted(self.window.sites.items(), key=lambda p: p[0].casefold()):
            row = QTreeWidgetItem([name, data["host"], str(data["port"]), data["protocol"], data["username"]])
            row.setData(0, Qt.ItemDataRole.UserRole, name)
            if data.get("_disabled"):
                row.setText(0, f"{name} (disabled)")
                row.setForeground(0, Qt.GlobalColor.gray)
            folder = data.get("_folder")
            if folder in folder_rows:
                folder_rows[folder].addChild(row)
            else:
                self.tree.addTopLevelItem(row)
            row.setSelected(name in selection)
        self.filter()
        self.tree.expandAll()
        self.selection_changed()

    def folders(self):
        # Recover referenced folders even if settings were restored separately.
        return sorted(set(self.window.preferences.value('Sites/folders', [], type=list)) |
                      {p['_folder'] for p in self.window.sites.values() if p.get('_folder')}, key=str.casefold)

    def selected_folder(self):
        rows = self.tree.selectedItems()
        if len(rows) == 1:
            data = rows[0].data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, (list, tuple)) and len(data) == 2 and data[0] == 'folder':
                return data[1]
        return None

    def valid_folder(self, name, old=None):
        if not name or len(name) > 128 or any(ord(c) < 32 or c in '/\\' for c in name):
            QMessageBox.warning(self, 'Invalid folder name', 'Enter 1–128 characters without slashes or control characters.')
            return False
        if any(f.casefold() == name.casefold() and f != old for f in self.folders()):
            QMessageBox.warning(self, 'Folder exists', 'A folder with that name already exists.')
            return False
        return True

    def move_sites(self, folder):
        self.click_timer.stop()
        if folder and folder not in self.folders():
            return
        selected = self.selected()
        proposed = {name: dict(value) for name, value in self.window.sites.items()}
        for name in selected:
            if folder: proposed[name]['_folder'] = folder
            else: proposed[name].pop('_folder', None)
        if selected and self.window.save_sites(proposed):
            self.refresh()

    def rename_folder(self, old):
        name, accepted = QInputDialog.getText(self, 'Rename Folder', 'Folder name:', text=old)
        name = name.strip()
        if not accepted or name == old or not self.valid_folder(name, old): return
        proposed = {key: dict(value) for key, value in self.window.sites.items()}
        for value in proposed.values():
            if value.get('_folder') == old: value['_folder'] = name
        folders = [name if f == old else f for f in self.folders()]
        if self.window.save_sites(proposed):
            self.window.preferences.setValue('Sites/folders', folders)
            self.window.preferences.sync()
            self.refresh()

    def filter(self):
        text = self.search.text().casefold()
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if group.childCount():
                visible = False
                for j in range(group.childCount()):
                    row = group.child(j)
                    name = row.data(0, Qt.ItemDataRole.UserRole)
                    data = self.window.sites.get(name, {})
                    shown = not text or text in group.text(0).casefold() or any(text in value.casefold() for value in
                        (name, data.get("host", ""), data.get("username", "")))
                    row.setHidden(not shown)
                    visible |= shown
                group.setHidden(not visible and bool(text))
            else:
                name = group.data(0, Qt.ItemDataRole.UserRole)
                if not isinstance(name, str):
                    group.setHidden(bool(text) and text not in group.text(0).casefold())
                    continue
                data = self.window.sites.get(name, {})
                group.setHidden(bool(text) and not any(text in value.casefold() for value in
                    (name, data.get("host", ""), data.get("username", ""))))
        self.selection_changed()

    def selected(self):
        names = []
        for row in self.tree.selectedItems():
            name = row.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(name, str) and name in self.window.sites and not row.isHidden() and not (row.parent() and row.parent().isHidden()):
                names.append(name)
        return names

    def new(self):
        self.edit(new=True)

    def new_folder(self):
        name, accepted = QInputDialog.getText(self, "New Folder", "Folder name:")
        name = name.strip()
        if not accepted or not name:
            return
        folders = self.folders()
        if not self.valid_folder(name): return
        folders.append(name)
        selected = self.selected()
        if selected:
            proposed = {key: dict(value) for key, value in self.window.sites.items()}
            for site_name in selected:
                proposed[site_name]["_folder"] = name
            if not self.window.save_sites(proposed):
                return
        self.window.preferences.setValue("Sites/folders", folders)
        self.window.preferences.sync()
        self.refresh()
        self.tree.expandAll()

    def rename(self):
        self.click_timer.stop()
        folder = self.selected_folder()
        if folder is not None:
            self.rename_folder(folder)
            return
        selected = self.selected()
        if len(selected) != 1:
            return
        old_name = selected[0]
        name, accepted = QInputDialog.getText(self, 'Rename Site', 'Site name:', text=old_name)
        name = name.strip()
        if not accepted or not name or name == old_name:
            return
        if any(key.casefold() == name.casefold() and key != old_name for key in self.window.sites):
            QMessageBox.warning(self, 'Name already exists', 'Choose a different site name.')
            return
        proposed = dict(self.window.sites)
        profile = dict(proposed.pop(old_name))
        profile['name'] = name
        proposed[name] = profile
        if self.window.save_sites(proposed):
            self.refresh()

    def toggle_disabled(self, checked=None):
        selected = self.selected()
        if not selected:
            return
        proposed = {name: dict(value) for name, value in self.window.sites.items()}
        disabled = (not all(proposed[name].get('_disabled', False) for name in selected)) if checked is None else bool(checked)
        for name in selected:
            proposed[name]['_disabled'] = disabled
        if self.window.save_sites(proposed):
            self.refresh()

    def copy_site(self):
        selected = self.selected()
        if not selected:
            return
        from copy import deepcopy
        proposed = dict(self.window.sites)
        copies = []
        for source_name in selected:
            source = self.window.sites[source_name]
            base = f'{source_name} (copy)'
            name, suffix = base, 2
            while any(existing.casefold() == name.casefold() for existing in proposed):
                name = f'{base} {suffix}'
                suffix += 1
            clone = deepcopy(source)
            clone['name'] = name
            clone.pop('_credential_id', None)
            proposed[name] = clone
            copies.append((name, source))
        if self.window.save_sites(proposed):
            failures = []
            for name, source in copies:
                try:
                    password = self.window.get_site_password(source)
                    if password:
                        self.window.set_site_password(name, password)
                except RuntimeError:
                    failures.append(name)
            if failures:
                QMessageBox.warning(self, 'Passwords not copied', f'{len(failures)} copied site(s) need their passwords entered again.')
            self.refresh()

    def test_site(self):
        selected = self.selected()
        if len(selected) != 1:
            return
        name = selected[0]
        profile = self.window.sites[name]
        if profile.get("_disabled"):
            self.status.setText("That site is disabled. Re-enable it from the right-click menu first.")
            return
        if not self.window.ensure_proxy_credentials(self, profile.get("host", ""), self.window.site_from_profile(profile)):
            return
        try:
            password = self.window.get_site_password(profile)
        except RuntimeError as error:
            QMessageBox.warning(self, "Secure password storage unavailable", str(error))
            return
        if not password and not profile.get('cloud_account') and profile.get('username', '').casefold() != 'anonymous' and not (profile.get('managed_key') and profile.get('protocol') == 'SFTP · SSH'):
            password, accepted = QInputDialog.getText(self, "Test site", f"Password for {profile.get('username', '')}@{profile.get('host', '')}:", QLineEdit.EchoMode.Password)
            if not accepted:
                return
        site = self.window.site_from_profile(profile)
        def check(_worker):
            connection = self.window.new_connection(site, password)
            try:
                connection.connect()
                entries = connection.list(site.root)
                return connection.security, len(entries)
            finally:
                connection.close()
        def completed(result):
            QMessageBox.information(self, "Site test successful", f"Connection and directory test succeeded.\nTransport: {result[0]}\n{result[1]} entries listed in {site.root}.")
        self.window.run(check, completed, lambda error: QMessageBox.warning(self, "Site test failed", str(error)))

    def edit(self, new=False):
        from .app import SiteDialog
        from dataclasses import asdict
        selected = self.selected()
        if not new and len(selected) != 1:
            self.status.setText("Select one site to edit.")
            self.navigation.setCurrentItem(self.sites_nav)
            return
        old_name = None if new else selected[0]
        editor = SiteDialog(self, {}, editing=not new, profile_only=new)
        if old_name:
            editor.load(self.window.sites[old_name])
        if editor.exec():
            site = editor.site()
            proposed = dict(self.window.sites)
            if any(name.casefold() == site.name.casefold() and name != old_name for name in proposed):
                QMessageBox.warning(self, "Name already exists", "Choose a different site name.")
                return
            old_profile = proposed.get(old_name) if old_name else None
            if old_name:
                del proposed[old_name]
            profile = asdict(site)
            if new and self.selected_folder() is not None:
                profile['_folder'] = self.selected_folder()
            if old_profile:
                for key in ("_credential_id", "_folder", "_disabled"):
                    if old_profile.get(key) is not None:
                        profile[key] = old_profile[key]
            proposed[site.name] = profile
            if self.window.save_sites(proposed):
                try:
                    self.window.set_site_password(site.name, editor.password.text())
                except RuntimeError as error:
                    QMessageBox.warning(self, "Password not remembered", str(error))
                self.refresh()
                self.navigation.setCurrentItem(self.sites_nav)

    def delete(self):
        self.click_timer.stop()
        folder = self.selected_folder()
        if folder is not None:
            if QMessageBox.question(self, 'Delete folder', 'Remove this folder? Its saved sites will move to the root.') != QMessageBox.StandardButton.Yes:
                return
            proposed = {name: dict(value) for name, value in self.window.sites.items()}
            for profile in proposed.values():
                if profile.get('_folder') == folder: profile.pop('_folder', None)
            folders = [f for f in self.folders() if f != folder]
            if self.window.save_sites(proposed):
                self.window.preferences.setValue('Sites/folders', folders)
                self.window.preferences.sync()
                self.refresh()
            return
        selected = self.selected()
        if selected and QMessageBox.question(self, "Delete saved sites", f"Remove {len(selected)} saved site(s)? This only removes connection profiles.") == QMessageBox.StandardButton.Yes:
            proposed = {name: value for name, value in self.window.sites.items() if name not in selected}
            if self.window.save_sites(proposed):
                self.refresh()

    def connect_to(self, index, site_name=None):
        selected = [site_name] if site_name in self.window.sites else self.selected()
        if len(selected) != 1:
            self.status.setText("Select one site to connect.")
            return
        if self.window.sites[selected[0]].get("_disabled"):
            self.status.setText("That site is disabled. Re-enable it from the right-click menu first.")
            return
        pane = self.window.tabs.currentWidget().widget(index)
        if pane.busy:
            self.status.setText("That pane is busy. Wait for its current operation to finish.")
            return
        self.connection = (index, selected[0])
        self.accept()

