"""Two-pane workspace UI and file-tree drag/drop behavior."""
import json
import os
import posixpath
import time
from dataclasses import asdict
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from PySide6.QtCore import Qt, QMimeData, QSignalBlocker
from PySide6.QtGui import QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QMenu, QMessageBox, QStyle, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget)

from .browse_session import BrowseSession
from .compare_menu import RESULT_ROLE
from .core import Cancelled, Entry, PROTOCOLS, remove_remote_items, safe_name
from .display_options import AddressBar
from .site_dialog import SiteDialog
from .site_options import SiteSettings, decode as site_options
from .sort_options import sorted_entries


class TransferTree(QTreeWidget):
    """Copy drops into the displayed directory through the normal queue."""
    def __init__(self, pane):
        super().__init__(pane)
        self.pane = pane
        self.drag_source = None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDropIndicatorShown(False)
        self.setToolTip("Drag files here to copy into the current directory")

    def mouseDoubleClickEvent(self, event):
        row = self.itemAt(event.position().toPoint())
        entry = row.data(0, Qt.ItemDataRole.UserRole) if row else None
        if event.button() == Qt.MouseButton.LeftButton and row is not None and entry is not None:
            # Route file and folder activation through the pane's shared handler.
            event.accept()
            if not self.pane.busy:
                self.setFocus()
                self.setCurrentItem(row)
                self.pane.open_item(row)
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Backspace and not event.modifiers():
            self.pane.up()
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions):
        pane = self.pane
        entries = pane.selected()
        if pane.busy or not entries or (pane.mode == "Remote" and pane.site is None):
            return
        paths = {entry.name: pane.join(entry.name) for entry in entries}
        self.drag_source = SimpleNamespace(mode=pane.mode, site=pane.site,
            password=pane.password, busy=False, join=paths.__getitem__)
        self.drag_entries = entries
        mime = QMimeData()
        mime.setData("application/x-fluxftp-transfer", b"1")
        drag = QDrag(self)
        drag.setMimeData(mime)
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            self.drag_source = None

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.pane.window.update_selection_display(self)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.pane.window.update_selection_display(self)

    def accepts(self, event):
        if self.pane.busy or (self.pane.mode == "Remote" and self.pane.site is None):
            return False
        source = event.source()
        if isinstance(source, TransferTree):
            return source is not self and source.pane.window is self.pane.window and source.drag_source is not None
        return event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls())

    def dragEnterEvent(self, event):
        if self.accepts(event):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if not self.accepts(event):
            event.ignore()
            return
        source = event.source()
        if isinstance(source, TransferTree):
            self.pane.window.enqueue_items(source.drag_source, self.pane, source.drag_entries)
        else:
            self.pane.window.enqueue_local_paths(self.pane, [url.toLocalFile() for url in event.mimeData().urls()],
                start=self.pane.window.mouse_action('explorer_drop') == 'Begin Transfer')
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()


class Pane(QWidget):
    def __init__(self, window, label, mode="Remote"):
        super().__init__()
        self.window = window
        self.mode = mode
        self.site = None
        self.password = ""
        self.local_path = str(window.preferences.value("General/download_path", str(Path.home()), type=str))
        self.remote_site = None
        self.remote_password = ""
        self.remote_path = "/"
        self.path = self.remote_path if mode == "Remote" else self.local_path
        self.busy = False
        self.generation = 0
        self.browse_session = BrowseSession()
        layout = QVBoxLayout(self)
        self.number = "1" if label in ("LEFT BROWSER", "1") else "2"
        self.title_label = QLabel(f"({self.number}) {mode}")
        self.title_label.setStyleSheet("background:#d9e7f7; color:#183b63; font-weight:600; padding:5px 7px; border:1px solid #aebfd2;")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.title_label, 1)
        layout.addLayout(top)
        self.file_toolbar = QToolBar(f"Pane {self.number} tools", self)
        self.file_toolbar.setMovable(False)
        self.file_toolbar.setFloatable(False)
        self.file_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.sites_button = QToolButton(self)
        self.sites_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.sites_button.setToolTip("Connect to a saved site")
        self.sites_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sites_menu = QMenu(self.sites_button)
        self.sites_menu.aboutToShow.connect(self.populate_site_menu)
        self.sites_button.setMenu(self.sites_menu)
        self.file_toolbar.addWidget(self.sites_button)
        self.disconnect_button = QToolButton(self)
        self.disconnect_button.setText('Disconnect')
        self.disconnect_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton))
        self.disconnect_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.disconnect_button.setToolTip('Disconnect')
        self.disconnect_button.setAccessibleName('Disconnect')
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(lambda: self.window.disconnect_pane(self))
        self.file_toolbar.addWidget(self.disconnect_button)
        self.switch_servers_button = QToolButton(self)
        self.switch_servers_button.setText("⇄")
        self.switch_servers_button.setToolTip("Switch the remote servers between panes")
        self.switch_servers_button.clicked.connect(self.window.swap_remote_servers)
        self.file_toolbar.addWidget(self.switch_servers_button)
        self._toolbar_action_for_pane(QStyle.StandardPixmap.SP_ArrowUp, "Parent directory", self.up)
        self.bookmark_button = QToolButton(self)
        self.bookmark_button.setText("★")
        self.bookmark_button.setToolTip("Remote directory bookmarks")
        self.bookmark_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.bookmark_menu = QMenu(self.bookmark_button)
        self.bookmark_menu.aboutToShow.connect(self.populate_bookmark_menu)
        self.bookmark_button.setMenu(self.bookmark_menu)
        self.file_toolbar.addWidget(self.bookmark_button)
        self._toolbar_action_for_pane(QStyle.StandardPixmap.SP_MediaPlay, "Start or resume the transfer queue", self.window.play_queue)
        self._toolbar_action_for_pane(QStyle.StandardPixmap.SP_MediaStop, "Stop queued transfers", self.window.stop_queue)
        self._toolbar_action_for_pane(QStyle.StandardPixmap.SP_BrowserReload, "Refresh this site or folder (F5)", self.refresh)
        self._toolbar_action_for_pane(QStyle.StandardPixmap.SP_FileDialogDetailedView, "Compare folder contents (Ctrl+D)", self.window.compare_contents)
        transfer_icon = QStyle.StandardPixmap.SP_ArrowRight if self.number == "1" else QStyle.StandardPixmap.SP_ArrowLeft
        self._toolbar_action_for_pane(transfer_icon, "Transfer selected items to the other pane (Ctrl+T)", self.transfer_to_other)
        self.file_toolbar.addSeparator()
        self._toolbar_action_for_pane(QStyle.StandardPixmap.SP_FileDialogNewFolder, "Create a folder", self.mkdir)
        self._toolbar_action_for_pane(QStyle.StandardPixmap.SP_FileDialogDetailedView, "Rename selected item", self.rename)
        self._toolbar_action_for_pane(QStyle.StandardPixmap.SP_TrashIcon, "Delete selected items", self.delete)
        self.mode_button = QToolButton(self)
        self.mode_button.setText(mode)
        self.mode_button.setToolTip("Switch this pane between Remote and Local")
        self.mode_button.clicked.connect(self.toggle_mode)
        self.file_toolbar.addWidget(self.mode_button)
        layout.addWidget(self.file_toolbar)
        self.refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        self.refresh_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.refresh_shortcut.setEnabled(False)  # View's window-wide F5 action owns this shortcut.
        self.refresh_shortcut.activated.connect(self.refresh)
        self.transfer_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self.transfer_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.transfer_shortcut.activated.connect(self.transfer_to_other)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.address = AddressBar(self)
        side = 'Left' if self.number == '1' else 'Right'
        self.address.setAccessibleName(side + ' pane path')
        self.address.setToolTip("Current local or remote path")
        self.address.returnPressed.connect(lambda: self.navigate(self.address.text()))
        bar.addWidget(self.address, 1)
        self.filter = QLineEdit()
        self.filter.setAccessibleName(side + ' pane filename filter')
        self.filter.setPlaceholderText("Filter…")
        self.filter.setMaximumWidth(180)
        self.filter.textChanged.connect(self.filter_rows)
        bar.addWidget(self.filter)
        layout.addLayout(bar)
        self.tree = TransferTree(self)
        self.tree.setAccessibleName(side + ' pane files')
        self.tree.setAccessibleDescription('Select files with arrow keys. Enter opens a folder. Ctrl+T transfers selected items to the other pane.')
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels(["Name", "Size", "Date", "Attrib", "User", "Group"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sort_key = self.window.preferences.value('Display/sort', 'Name', type=str).replace('Modified', 'Date')
        if self.sort_key not in ('Name', 'Size', 'Date'): self.sort_key = 'Name'
        self.sort_descending = self.window.preferences.value('Sort/' + self.sort_key.lower() + '_descending', False, type=bool)
        self.tree.header().setSectionsClickable(True)
        self.tree.header().setSortIndicatorShown(True)
        self.tree.header().setSortIndicator({'Name': 0, 'Size': 1, 'Date': 2}[self.sort_key], Qt.SortOrder.DescendingOrder if self.sort_descending else Qt.SortOrder.AscendingOrder)
        self.tree.header().sectionClicked.connect(self.sort_column)
        self.tree.itemDoubleClicked.connect(self.open_item)
        self.tree.itemSelectionChanged.connect(self.window.ftp_commands.update)
        self.tree.itemSelectionChanged.connect(self.window.queue_commands.update)
        self.tree.itemSelectionChanged.connect(self.window.command_commands.update)
        self.tree.addAction(self.window.ftp_commands.actions['Attributes(CHMOD)'])
        layout.addWidget(self.tree)
        self.status = QLabel("Not connected" if mode == "Remote" else "Local files")
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        self.window.mark_commands.install(self)
        self.window.edit_commands.install(self)
        self.refresh()

    def _toolbar_action_for_pane(self, icon, tooltip, callback, shortcut=None):
        action = self.file_toolbar.addAction(self.style().standardIcon(icon), "", callback)
        action.setToolTip(tooltip)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        return action

    def populate_site_menu(self):
        self.window.populate_display_site_menu(self)

    def remote_title(self, site):
        if site is None:
            return f"({self.number}) Remote"
        show = site_options(site.site_options).get('Site/show_name')
        if show is True:
            return site.name
        if show is False:
            return f"({self.number}) Remote"
        return f"({self.number}) Remote · {site.name}"

    def _bookmark_records(self):
        data = self.window.preferences.value("Bookmarks/sites", "{}", type=str)
        try:
            bookmarks = json.loads(data)
        except (TypeError, ValueError):
            return []
        if not isinstance(bookmarks, dict) or not self.site:
            return []
        configured = site_options(self.site.site_options)
        records = configured.get('Site/bookmarks', bookmarks.get(self.site.name, []))
        return [record for record in records if isinstance(record, dict) and isinstance(record.get("path"), str)]

    def populate_bookmark_menu(self):
        self.bookmark_menu.clear()
        add = self.bookmark_menu.addAction("Bookmark this directory…")
        add.setEnabled(self.mode == "Remote" and self.site is not None)
        add.triggered.connect(self.add_bookmark)
        records = self._bookmark_records()
        if records:
            self.bookmark_menu.addSeparator()
            for record in records:
                label = record.get("name") or record["path"]
                action = self.bookmark_menu.addAction(f"{label}  —  {record['path']}")
                action.triggered.connect(lambda checked=False, item=record: self.open_bookmark(item))

    def open_bookmark(self, record):
        if record.get('local'):
            self.local_path = record['local']
        self.navigate(record['path'])

    def add_bookmark(self):
        if self.mode != "Remote" or self.site is None:
            return
        default_name = posixpath.basename(self.path.rstrip("/")) or self.path
        name, accepted = QInputDialog.getText(self, "Bookmark remote directory", "Bookmark name:", text=default_name)
        if not accepted or not name.strip():
            return
        options = site_options(self.site.site_options)
        records = [dict(name=item.get('name') or item['path'], path=item['path'], local=item.get('local', ''))
                   for item in self._bookmark_records() if item.get('path') != self.path]
        records.append({'name': name.strip(), 'path': self.path, 'local': ''})
        options['Site/bookmarks'] = records
        encoded = json.dumps(options, ensure_ascii=False)
        if self.site.name in self.window.sites:
            proposed = {key: dict(value) for key, value in self.window.sites.items()}
            proposed[self.site.name]['site_options'] = encoded
            if not self.window.save_sites(proposed):
                return
        self.site.site_options = encoded
        self.window.statusBar().showMessage(f"Bookmarked {self.path}", 4000)

    def toggle_mode(self, checked=False):
        self.change_mode("Local" if self.mode == "Remote" else "Remote")

    def transfer_to_other(self):
        self.window.enqueue(self.number == "1")

    def connect_site(self, checked=False, site_name=None, protocol=None):
        if self.busy:
            return
        dialog = SiteDialog(self, self.window.sites)
        if protocol:
            dialog.protocol.setCurrentText(protocol)
        if site_name:
            dialog.saved.setCurrentText(site_name)
        if dialog.exec():
            site = dialog.site()
            password = '' if site.cloud_account else dialog.password.text()
            if not self.window.ensure_proxy_credentials(self, site.host, site):
                return
            if site.username.casefold() == "anonymous" and not password:
                password = self.window.preferences.value("General/anonymous_email", "anonymous@fluxftp.local", type=str)
            self.set_remote(site, password, site.root)
            self.window.populate_quick_connect(self.site, self.password, 0 if self.number == "1" else 1)
            proposed = dict(self.window.sites)
            profile = asdict(self.site)
            previous = proposed.get(self.site.name)
            if previous and previous.get("_credential_id"):
                profile["_credential_id"] = previous["_credential_id"]
            proposed[self.site.name] = profile
            if self.window.save_sites(proposed):
                try:
                    self.window.set_site_password(self.site.name, self.password)
                except RuntimeError as error:
                    QMessageBox.warning(self, "Password not remembered", str(error))
            self.refresh()

    def connect_saved_site(self, site_name):
        if self.busy or site_name not in self.window.sites:
            return
        data = self.window.sites[site_name]
        self.connect_profile(data, site_name)

    def connect_profile(self, data, site_name=None):
        if self.busy:
            return
        if data.get("_disabled"):
            self.window.log.appendPlainText(f"Site '{site_name}' is disabled.")
            return
        site = self.window.site_from_profile(data)
        warn_plain = self.window.preferences.value("Confirm/unencrypted", True, type=bool)
        if site.protocol == PROTOCOLS[4] and warn_plain and QMessageBox.question(
                self, "Unencrypted connection",
                "Plain FTP sends your password and files without encryption. Continue?") != QMessageBox.StandardButton.Yes:
            return
        if not self.window.ensure_proxy_credentials(self, site.host, site):
            return
        try:
            password = self.window.get_site_password(data)
        except RuntimeError as error:
            QMessageBox.warning(self, "Secure password storage unavailable", str(error))
            password = ""
        if not password and not site.cloud_account and not (site.managed_key and site.protocol in (PROTOCOLS[3], 'SCP · SSH')):
            password, accepted = QInputDialog.getText(
                self, "Connect to site", f"Password or key passphrase for {site.username}@{site.host}:",
                QLineEdit.EchoMode.Password)
            if not accepted:
                return
            try:
                if site_name is not None:
                    self.window.set_site_password(site_name, password)
            except RuntimeError as error:
                QMessageBox.warning(self, "Password not remembered", str(error))
        if site.username.casefold() == "anonymous" and not password:
            password = self.window.preferences.value("General/anonymous_email", "anonymous@fluxftp.local", type=str)
        self.set_remote(site, password, site.root)
        self.window.populate_quick_connect(site, password, 0 if self.number == "1" else 1)
        self.refresh()

    def set_remote(self, site=None, password="", path=None):
        was_connected = getattr(self, '_workspace_connected_site', None) is not None
        self._workspace_connected_site = None
        self.browse_session.close()
        if self.site and self.window._transfer_site_key(self.site) != self.window._transfer_site_key(site):
            self.window.clear_site_queue(self, self.site)
        self.site, self.password = site, password
        if site is not None:
            if site.local_path:
                self.local_path = site.local_path
            self.remote_site, self.remote_password = site, password
            self.remote_path = path or site.root
            self.path = self.remote_path
        self.mode = "Remote"
        self._set_mode_button("Remote")
        self.title_label.setText(self.remote_title(site))
        self.generation += 1
        self.window.apply_display_settings()

        if was_connected and site is None:
            self.window.update_connected_workspace_name(self)

    def _set_mode_button(self, mode):
        from .toolbar_icons import apply_mode_icon
        apply_mode_icon(self)

    def change_mode(self, mode):
        if self.busy:
            self.mode_button.setText(self.mode)
            return
        if mode == self.mode:
            return
        self.browse_session.close()
        if self.mode == "Local":
            self.local_path = self.path
        elif self.site is not None:
            self.remote_site, self.remote_password, self.remote_path = self.site, self.password, self.path
        self.mode = mode
        self._set_mode_button(mode)
        if mode == "Local":
            self.site, self.password = None, ""
            was_connected = getattr(self, '_workspace_connected_site', None) is not None
            self._workspace_connected_site = None
            if was_connected:
                self.window.update_connected_workspace_name(self)
            self.path = self.local_path
            self.title_label.setText(f"({self.number}) Local")
        else:
            self.site, self.password = self.remote_site, self.remote_password
            self.path = self.remote_path if self.site else "/"
            self.title_label.setText(self.remote_title(self.site))
        self.generation += 1
        self.window.view_commands.options.apply()
        self.refresh()

    def go_local(self):
        if not self.busy:
            self.change_mode("Local")

    def navigate(self, path):
        if self.busy and self.mode == 'Local' and getattr(self, '_local_listing_worker', None):
            self._local_listing_worker.cancel.set()
            self._local_listing_worker = None
            self.generation += 1
            self.busy = False
        if not self.busy:
            self.path = posixpath.normpath(path) if self.mode == "Remote" else str(Path(path).expanduser().absolute())
            if self.mode == "Local":
                self.local_path = self.path
            elif self.site:
                self.remote_path = self.path
            self._navigation_cache = True
            try:
                self.refresh()
            finally:
                self._navigation_cache = False

    def up(self):
        self.navigate(posixpath.dirname(self.path.rstrip("/")) or "/" if self.mode == "Remote" else str(Path(self.path).parent))

    def refresh(self):
        if self.busy:
            return
        self.address.setText(self.path)
        self.window.update_local_address(self)
        if hasattr(self.window, "compare_commands"):
            self.window.compare_commands.clear(self)
        if self.mode == "Local":
            path, generation = self.path, self.generation
            show_hidden = self.window.preferences.value("Display/local_show_hidden", False, type=bool)
            self.busy = True
            self.tree.setEnabled(False)
            self.status.setText('Loading folder…')
            from itertools import islice, chain
            from types import SimpleNamespace
            try:
                items = os.scandir(path)
                initial = list(islice(items, 2001))
            except OSError as error:
                self.busy = False
                self.tree.clear()
                self.tree.setEnabled(True)
                self.window.error(str(error))
                return
            def local_listing(worker):
                entries = []
                with items:
                    for p in chain(initial, items):
                        if worker.cancel.is_set(): raise Cancelled('Listing cancelled')
                        if not show_hidden and p.name.startswith('.'):
                            continue
                        try:
                            st = p.stat()
                            if not show_hidden and getattr(st, 'st_file_attributes', 0) & 2: continue
                            entries.append(Entry(p.name, p.is_dir(), st.st_size, time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime)), p.is_symlink()))
                        except OSError:
                            continue
                return entries, 'Local files'
            def local_done(result):
                if generation == self.generation:
                    self._local_listing_worker = None
                    self.show_entries(result)
            def local_failed(message):
                if generation == self.generation:
                    self._local_listing_worker = None
                    self.busy = False
                    self.tree.clear()
                    self.tree.setEnabled(True)
                    self.status.setText('Listing failed')
                    self.window.error(message)
            if len(initial) <= 2000:
                try: local_done(local_listing(SimpleNamespace(cancel=Event())))
                except Exception as error: local_failed(str(error))
            else:
                self._local_listing_worker = self.window.run(local_listing, local_done, local_failed)
            return
        if not self.site:
            self.tree.clear()
            self.tree.setEnabled(True)
            self.title_label.setText(f"({self.number}) Remote")
            self.status.setText("Not connected · use Connect… or choose a saved site")
            return
        site, password, path = self.site, self.password, self.path
        generation = self.generation
        cache = self.window.directory_cache
        cache_key = cache.key(site, password, path)
        cached = cache.get(cache_key) if getattr(self, '_navigation_cache', False) and site_options(site.site_options).get('Site/cache', True) else None
        if cached is not None:
            self.show_entries(cached)
            self.status.setText(self.status.text() + ' · cached (F5 to refresh)')
            return
        epoch = cache.epoch
        self.busy = True
        self.tree.setEnabled(False)
        self.status.setText("Loading folder…")
        connection_settings = tuple((key, repr(self.window.preferences.value(key)))
                                    for key in self.window.preferences.allKeys()
                                    if key.startswith(('Connection/', 'Transfer/')) or key == 'General/upnp_enabled')
        session_key = (cache_key[:2], connection_settings, self.window.proxy_password)
        settings = SiteSettings(self.window.preferences, site)
        persistent = settings.value('Connection/persistent_browsing', True, type=bool) and not (site.single_connection or settings.value('Transfer/single_connection', False, type=bool))
        if not persistent:
            self.browse_session.close()
        session_generation = self.browse_session.generation
        def task(worker):
            def factory():
                c = self.window.new_connection(site, password)
                c.trace.display_side = int(self.number)
                return c
            if not persistent:
                with factory() as c:
                    return c.list(path), c.security
            return self.browse_session.list(session_key, factory, path, expected_generation=session_generation)
        def failed(message):
            self.busy = False
            if generation != self.generation: return
            self.tree.clear()
            self.tree.setEnabled(True)
            self.status.setText("Connection failed")
            self.window.error(message)
        def listed(result):
            if generation != self.generation:
                self.busy = False
                return
            self.window.remember_recent_site(site)
            if getattr(self, '_workspace_connected_site', None) is not site:
                self._workspace_connected_site = site
                self.window.update_connected_workspace_name(self)
            cache.put(cache_key, result, epoch)
            self.show_entries(result)
        self.window.run(task, listed, failed)

    def show_entries(self, result):
        entries, security = result
        self.busy = False
        self.address.remember()
        self.title_label.setText(self.remote_title(self.site) if self.mode == "Remote" else f"({self.number}) {self.mode}")
        if hasattr(self.window, "compare_commands"):
            self.window.compare_commands.clear(self)
        show_hidden = self.window.preferences.value("Display/local_show_hidden" if self.mode == "Local" else "Display/show_hidden", False if self.mode == "Local" else True, type=bool)
        if self.mode == "Remote" and self.site and self.site.protocol not in (PROTOCOLS[3], 'SCP · SSH'):
            if self.window.preferences.value("Connection/list_mode", "Default (LIST)", type=str) in ("Show Hidden Files", "Custom"):
                show_hidden = True
        if not show_hidden:
            entries = [entry for entry in entries if not entry.name.startswith(".")]
        def finished():
            name = self.site.name + " · " if self.mode == "Remote" and self.site else ""
            self.status.setText(f"{name}{len(entries)} items · {security}")
            self.window.mark_commands.update()
            self.tree.setProperty('colorScope', self.mode.lower())
            self.window.update_selection_display(self.tree)
            self.window.view_commands.options.apply()
        from .listing_view import populate
        populate(self, entries, finished)

    def sort_column(self, column):
        key = {0: 'Name', 1: 'Size', 2: 'Date'}.get(column)
        if key is None: return
        self.sort_descending = (not self.sort_descending) if key == self.sort_key else self.window.preferences.value('Sort/' + key.lower() + '_descending', False, type=bool)
        self.sort_key = key
        self.resort_rows()

    def resort_rows(self):
        header = self.tree.header()
        header.setSortIndicatorShown(True)
        header.setSortIndicator({'Name': 0, 'Size': 1, 'Date': 2}[self.sort_key], Qt.SortOrder.DescendingOrder if self.sort_descending else Qt.SortOrder.AscendingOrder)
        selected_rows = self.tree.selectedItems()
        selected = {id(row.data(0, Qt.ItemDataRole.UserRole)) for row in selected_rows}
        current = self.tree.currentItem()
        scroll = self.tree.verticalScrollBar().value()
        with QSignalBlocker(self.tree):
            rows = [self.tree.takeTopLevelItem(0) for _ in range(self.tree.topLevelItemCount())]
            entries = [row.data(0, Qt.ItemDataRole.UserRole) for row in rows]
            mapping = {id(entry): row for entry, row in zip(entries, rows)}
            for entry in sorted_entries(entries, self.window.preferences, self.mode.lower(), self.sort_key, self.sort_descending):
                self.tree.addTopLevelItem(mapping[id(entry)])
            if current: self.tree.setCurrentItem(current)
            for row in rows: row.setSelected(id(row.data(0, Qt.ItemDataRole.UserRole)) in selected)
        self.tree.verticalScrollBar().setValue(scroll)
        self.filter_rows()

    def filter_rows(self):
        needle = self.filter.text().casefold()
        for i in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(i)
            row.setHidden(needle not in row.text(0).casefold() or (
                row.data(0, RESULT_ROLE) == "same" and
                self.window.preferences.value("Compare/hide", True, type=bool)))
        self.window.mark_commands.update()

    def selected(self):
        return [row.data(0, Qt.ItemDataRole.UserRole) for row in self.tree.selectedItems() if not row.isHidden()]

    def join(self, name):
        safe_name(name)
        return posixpath.join(self.path, name) if self.mode == "Remote" else str(Path(self.path) / name)

    def open_item(self, row):
        entry = row.data(0, Qt.ItemDataRole.UserRole)
        if self.busy or entry is None:
            return
        if entry.is_dir:
            self.navigate(self.join(entry.name))
        else:
            self.window.mouse_file(self, entry)

    def mutate(self, remote_fn, local_fn, success=None):
        if self.busy:
            return
        if self.mode == "Remote":
            if self.site is None:
                self.window.statusBar().showMessage("Connect this remote pane before changing files", 5000)
                return
            site, password = self.site, self.password
            self.window.directory_cache.clear(site)
            self.busy = True
            def task(worker):
                c = self.window.new_connection(site, password)
                c.trace.display_side = int(self.number)
                with c:
                    remote_fn(c)
            def done(_):
                self.busy = False
                self.window.directory_cache.clear(site)
                success() if success else self.refresh()
            def failed(error):
                self.busy = False
                self.window.directory_cache.clear(site)
                self.window.error(error)
                self.refresh()
            self.window.run(task, done, failed)
        else:
            try:
                local_fn()
                self.refresh()
            except Exception as e:
                self.window.error(str(e))

    def mkdir(self):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name")
        if ok and name:
            try:
                path = self.join(name)
                if self.window.preferences.value('Misc/deny_parent_name', False, type=bool) and name.casefold() == (posixpath.basename(self.path.rstrip('/')) if self.mode == 'Remote' else Path(self.path).name).casefold():
                    raise ValueError('New directory cannot have the same name as its parent')
                enter = self.mode == 'Remote' and self.window.preferences.value('Misc/enter_created', False, type=bool)
                self.mutate(lambda c: c.mkdir(path), lambda: Path(path).mkdir(), (lambda: self.navigate(path)) if enter else None)
            except ValueError as e:
                self.window.error(str(e))

    def rename(self):
        selected = self.selected()
        if len(selected) != 1:
            return
        name, ok = QInputDialog.getText(self, "Rename", "New name", text=selected[0].name)
        if ok and name:
            try:
                old, new = self.join(selected[0].name), self.join(name)
                self.mutate(lambda c: c.rename(old, new), lambda: Path(old).rename(new))
            except ValueError as e:
                self.window.error(str(e))

    def delete(self):
        selected = self.selected()
        if not selected:
            return
        message = f"Permanently delete {len(selected)} selected item(s)?"
        if self.mode == 'Remote':
            message += " Selected folders and all their contents will be permanently deleted."
            if any(e.is_link for e in selected):
                self.window.error('Symbolic links cannot be deleted through this action.')
                return
        else:
            message += " Only empty folders can be deleted."
        if self.mode == 'Local':
            if not self.window.confirm_action('delete_local', 'Delete local files', message):
                return
        elif self.window.preferences.value("Confirm/delete", True, type=bool) and QMessageBox.question(self, "Delete selected items", message) != QMessageBox.StandardButton.Yes:
            return
        paths = [(self.join(e.name), e.is_dir) for e in selected]
        from .core import remove_remote_items
        self.mutate(lambda c: remove_remote_items(c, paths),
                    lambda: [Path(p).rmdir() if d else Path(p).unlink() for p, d in paths])


