"""Display preferences and desktop presentation lifecycle."""
import json
import hashlib
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QStyle, QLineEdit, QAbstractButton
from .local_locations import local_folders, local_drives
from .queue_tree import rows as queue_rows
from .queue_tree import RATE_ROLE, PROGRESS_ROLE, STREAM_ROLE
from .transfer_reporting import update_group


class AddressBar(QLineEdit):
    def __init__(self, pane):
        super().__init__(pane.path)
        self.pane = pane
        self.path_menu = QMenu(self)
        self.dropdown_action = self.addAction(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown),
                                              QLineEdit.ActionPosition.TrailingPosition)
        self.dropdown_action.setToolTip('Drives, recent folders and bookmarks (Alt+Down)')
        self.dropdown_action.setText('Drives, recent folders and bookmarks')
        for button in self.findChildren(QAbstractButton):
            button.setAccessibleName('Drives, recent folders and bookmarks')
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.dropdown_action.triggered.connect(self.show_paths)

    def scope(self):
        if self.pane.mode == 'Local':
            return 'local'
        site = self.pane.site
        if site is None:
            return None
        identity = json.dumps([site.protocol, site.host.casefold(), site.port, site.username])
        return hashlib.sha256(identity.encode('utf-8')).hexdigest()

    def history(self):
        try:
            value = json.loads(self.pane.window.preferences.value('General/path_history', '{}', type=str))
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    def remember(self):
        key = self.scope()
        if key is None:
            return
        data = self.history()
        previous = data.pop(key, [])
        if not isinstance(previous, list):
            previous = []
        data[key] = [self.pane.path] + [path for path in previous
            if isinstance(path, str) and path != self.pane.path][:24]
        data = dict(list(data.items())[-50:])
        self.pane.window.preferences.setValue('General/path_history', json.dumps(data, ensure_ascii=False))

    def populate_paths(self):
        menu = self.path_menu
        menu.clear()
        key = self.scope()
        ready = key is not None and not self.pane.busy
        def add(label, path, icon=None):
            action = menu.addAction(label.replace('&', '&&'))
            if icon is not None:
                action.setIcon(self.style().standardIcon(icon))
            action.setEnabled(ready)
            action.setToolTip(path)
            action.setData(path)
            action.triggered.connect(lambda checked=False: self.choose_path(key, path))
        if self.pane.mode == 'Local':
            icons = {'desktop': QStyle.StandardPixmap.SP_DesktopIcon, 'folder': QStyle.StandardPixmap.SP_DirIcon,
                     'home': QStyle.StandardPixmap.SP_DirHomeIcon, 'disk': QStyle.StandardPixmap.SP_DriveHDIcon,
                     'removable': QStyle.StandardPixmap.SP_DriveFDIcon,
                     'network': QStyle.StandardPixmap.SP_DriveNetIcon, 'optical': QStyle.StandardPixmap.SP_DriveCDIcon}
            for label, path, kind in local_folders():
                add(label, path, icons[kind])
            menu.addSeparator()
            menu.addAction('This PC').setEnabled(False)
            try:
                drives = local_drives()
            except OSError:
                drives = []
                menu.addAction('Unable to enumerate drives').setEnabled(False)
            for label, path, kind in drives:
                add(label, path, icons[kind])
            menu.addSeparator()
        menu.addAction('Recent folders').setEnabled(False)
        paths = self.history().get(key, [])
        paths = [path for path in paths if isinstance(path, str)] if isinstance(paths, list) else []
        if paths:
            for path in paths[:25]:
                add(path, path)
        else:
            menu.addAction('No recent folders').setEnabled(False)
        if self.pane.mode == 'Remote':
            menu.addSeparator()
            menu.addAction('Bookmarks').setEnabled(False)
            records = self.pane._bookmark_records()
            for record in records:
                path = record['path']
                add(f"{record.get('name') or path} — {path}", path)
            if not records:
                menu.addAction('No bookmarks for this site').setEnabled(False)
            menu.addSeparator()
            action = menu.addAction('Bookmark this directory…')
            action.setEnabled(ready)
            action.triggered.connect(self.pane.add_bookmark)

    def choose_path(self, scope, path):
        if not self.pane.busy and self.scope() == scope:
            self.pane.navigate(path)

    def show_paths(self):
        self.populate_paths()
        self.path_menu.setMinimumWidth(self.width())
        self.path_menu.popup(self.mapToGlobal(QPoint(0, self.height())))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Down and event.modifiers() == Qt.KeyboardModifier.AltModifier:
            self.show_paths()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        if self.pane.mode == 'Local': self.setText(self.pane.path)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.pane.window.update_local_address(self.pane)


class DisplayOptions:
    def display_value(self, name, default):
        return self.preferences.value('Display/' + name, default, type=type(default))

    def file_size(self, size):
        from .app import human
        mode = self.display_value('size_mode', 'Dynamic')
        if mode == 'Bytes': return f'{size:,} B'
        if mode == 'KBytes': return f'{size / 1024:.1f} KB'
        return human(size)

    def initialize_display(self):
        self.initialize_colors()
        self.tray = QSystemTrayIcon(self)
        from .tray_menu import tray_icons, install_tray_menu
        from .themes import os_dark
        self.tray_normal, self.tray_transfer = tray_icons(os_dark(system=True))
        self.tray.setIcon(self.tray_normal)
        self.tray.setToolTip('FluxFTP')
        install_tray_menu(self)
        self.tray.activated.connect(lambda reason: self.restore_from_tray() if reason in (
            QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray_timer = QTimer(self)
        self.tray_timer.setInterval(600)
        self.tray_timer.timeout.connect(self.update_tray)
        self.tray_timer.start()
        self.download_display_timer = QTimer(self)
        self.download_display_timer.timeout.connect(self.update_downloading_sizes)
        self._tray_phase = False
        self.apply_fonts()
        self.apply_display_settings()

    def restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.update_tray()

    def minimize_to_tray(self):
        if self.isMinimized() and self.display_value('minimize_tray', False) and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            self.hide()

    def changeEvent(self, event):
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, 'tray'):
            QTimer.singleShot(0, self.minimize_to_tray)

    def update_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            # Never leave the application inaccessible when the shell loses its tray.
            if not self.isVisible() and self.isMinimized(): self.showMinimized()
            return
        visible = self.display_value('always_tray', True) or bool(self.active) or (not self.isVisible() and self.isMinimized())
        self.tray.setVisible(visible)
        flashing = bool(self.active) and self.display_value('flash_tray', True)
        self._tray_phase = not self._tray_phase if flashing else False
        self.tray.setIcon(self.tray_transfer if self._tray_phase else self.tray_normal)
        self.tray.setToolTip(f'FluxFTP — {len(self.active)} active transfer(s)' if self.active else 'FluxFTP')

    def apply_fonts(self):
        from .font_options import display_font
        from PySide6.QtWidgets import QWidget
        fonts = {scope: display_font(self.preferences, scope) for scope in ('application', 'files', 'logs')}
        from PySide6.QtGui import QFont, QFontDatabase
        ascii_font = QFont(fonts['logs'])
        if not QFontDatabase.isFixedPitch(ascii_font.family()):
            ascii_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            ascii_font.setPointSizeF(max(1, fonts['logs'].pointSizeF()))
        ascii_font.setStyleHint(QFont.StyleHint.Monospace)
        ascii_font.setFixedPitch(True)
        fonts['ftp_ascii'] = ascii_font
        if self.app_instance.font() != fonts['application']: self.app_instance.setFont(fonts['application'])
        roots = {self.queue: 'files', self.log: 'logs', self.ftp_log: 'ftp_ascii',
                 self.transfer_log: 'ftp_ascii', self.tasks_log: 'logs'}
        from .font_zoom import zoomed_font
        keys = {self.queue: 'queue', self.log: 'log', self.ftp_log: 'ftp_log',
                self.transfer_log: 'transfer_log', self.tasks_log: 'tasks_log'}
        if hasattr(self, 'window_list'):
            roots[self.window_list] = 'application'
            keys[self.window_list] = 'workspace'
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            for side, pane in enumerate((split.widget(0), split.widget(1))):
                roots[pane.tree] = 'files'
                keys[pane.tree] = 'left' if side == 0 else 'right'
        zoom_fonts = {root: zoomed_font(self, root, keys[root], fonts[scope]) for root, scope in roots.items()}
        # Qt stylesheets can inhibit inherited fonts. Apply to each widget explicitly,
        # including headers and viewport children, while preserving each font scope.
        for widget in [self, *self.findChildren(QWidget)]:
            scope = widget.property('fontScope') or 'application'
            ancestor = widget
            zoom_font = None
            while ancestor is not None:
                if ancestor in roots:
                    scope = roots[ancestor]
                    zoom_font = zoom_fonts[ancestor]
                    break
                ancestor = ancestor.parentWidget()
            font = zoom_font if zoom_font is not None else fonts.get(scope, fonts['application'])
            if widget.font() != font: widget.setFont(font)

    def apply_display_settings(self, *_):
        self.apply_colors()
        if hasattr(self, 'download_display_timer'):
            self.download_display_timer.setInterval(max(1, self.display_value('download_seconds', 15)) * 1000)
            if self.display_value('update_download_size', False): self.download_display_timer.start()
            else: self.download_display_timer.stop()
            self.update_tray()
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            for pane in (split.widget(0), split.widget(1)):
                from .themes import header_style
                pane.title_label.setStyleSheet(header_style(self.preferences,
                    getattr(self.app_instance, '_fluxftp_original_palette', self.app_instance.palette())))
                self.update_selection_display(pane.tree)
                self.update_local_address(pane)
                for n in range(pane.tree.topLevelItemCount()):
                    row = pane.tree.topLevelItem(n)
                    entry = row.data(0, Qt.ItemDataRole.UserRole)
                    if entry and not entry.is_dir: row.setText(1, self.file_size(entry.size))
            labels = [p.site.name if p.mode == 'Remote' and p.site else p.mode for p in (split.widget(0), split.widget(1))]
            separator = ' - ' if self.display_value('hyphen_separator', False) else ' ↔ '
            self.tabs.setTabToolTip(index, separator.join(labels))
        for row in queue_rows(self.queue):
            size = row.data(4, Qt.ItemDataRole.UserRole)
            streaming = row.data(0, STREAM_ROLE)
            if size is not None: row.setText(4, '—' if streaming else self.file_size(size))
            progress = row.data(3, Qt.ItemDataRole.UserRole)
            if progress: row.setText(3, self.file_size(progress[0]) if streaming else f'{self.file_size(progress[0])} / {self.file_size(progress[1])}')
        self.apply_layout_settings()
        if hasattr(self, 'view_commands'):
            self.view_commands.options.apply()

    def update_selection_display(self, tree):
        hidden = self.display_value('hide_unfocused_selection', False) and not tree.hasFocus()
        tree.setStyleSheet(self.color_tree_style(tree, hidden))

    def transfer_display_progress(self, row, percent, rate, done, total):
        from .app import human
        row.setText(1, f'{percent}%')
        if row.data(1, PROGRESS_ROLE) is not None:
            row.setData(1, PROGRESS_ROLE, percent)
        row.setText(2, human(rate * 1024) + '/s')
        row.setData(3, Qt.ItemDataRole.UserRole, [done, total])
        row.setData(4, Qt.ItemDataRole.UserRole, total)
        streaming = row.data(0, STREAM_ROLE)
        row.setText(3, self.file_size(done) if streaming else f'{self.file_size(done)} / {self.file_size(total)}')
        row.setText(4, '—' if streaming else self.file_size(total))
        row.setData(0, RATE_ROLE, rate)
        update_group(self, row)

    def update_local_address(self, pane):
        if pane.mode != 'Local': return
        pane.address.setToolTip(pane.path)
        text = pane.path if self.display_value('local_full_path', False) or pane.address.hasFocus() else (Path(pane.path).name or pane.path)
        pane.address.setText(text)

    def update_downloading_sizes(self):
        paths = set()
        for record in self.active.values():
            job = record.get('job', {})
            if record.get('route') == 'Download' and job.get('dst'):
                target = Path(job['dst'])
                paths.add(target if job.get('directory') else target.parent)
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            for pane in (split.widget(0), split.widget(1)):
                if pane.mode == 'Local' and not pane.busy and any(Path(pane.path) == p or p in Path(pane.path).parents for p in paths):
                    selected = {e.name for e in pane.selected()}
                    scroll = pane.tree.verticalScrollBar().value()
                    pane.refresh()
                    for n in range(pane.tree.topLevelItemCount()):
                        row = pane.tree.topLevelItem(n)
                        row.setSelected(row.data(0, Qt.ItemDataRole.UserRole).name in selected)
                    pane.tree.verticalScrollBar().setValue(scroll)

    def recent_sites(self):
        try:
            values = json.loads(self.preferences.value('Display/recent_connected_sites', '[]', type=str))
            return values[:20] if isinstance(values, list) else []
        except ValueError: return []

    def remember_recent_site(self, site):
        # Site has no password fields; only connection profiles are persisted.
        record = asdict(site)
        self._session_reconnect_site = record
        previous = [value for value in self.recent_sites() if isinstance(value, dict) and value != record]
        self.preferences.setValue('Display/recent_connected_sites', json.dumps([record] + previous[:19]))

    def choose_recent_site(self, pane, profile):
        from .core import Site
        from .site_import import validate_site
        try:
            site = Site(**profile)
            validate_site(site)
        except (TypeError, ValueError): return
        if pane.busy:
            return
        identity = ('host', 'port', 'protocol', 'username', 'cloud_account')
        for name, saved in self.sites.items():
            current = self.site_from_profile(saved)
            if all(getattr(current, key) == getattr(site, key) for key in identity):
                pane.connect_saved_site(name)
                return
        pane.connect_profile(asdict(site))

    def populate_display_site_menu(self, pane):
        menu = pane.sites_menu
        menu.clear()
        if not self.display_value('hide_quick_connect', False):
            menu.addAction('Quick Connect', lambda: (self.quick_target.setCurrentIndex(int(pane.number) - 1), self.quick_host.setFocus()))
        if self.display_value('show_quick_dialog', True): menu.addAction('Quick Connect dialog…', pane.connect_site)
        profiles = []
        from .core import Site
        from .site_import import validate_site
        for profile in self.recent_sites():
            try:
                validate_site(Site(**profile))
            except (TypeError, ValueError):
                continue
            profiles.append(profile)
        if self.display_value('show_recent', True) and profiles:
            if menu.actions(): menu.addSeparator()
            session_site = getattr(self, '_session_reconnect_site', None)
            if session_site is not None:
                reconnect = menu.addAction('Reconnect to ' + str(session_site.get('name') or session_site['host']),
                    lambda: self.choose_recent_site(pane, session_site))
                reconnect.setEnabled(not pane.busy)
            recent = menu.addMenu('Recent sites')
            for profile in profiles:
                if not isinstance(profile, dict): continue
                action = recent.addAction(str(profile.get('name') or profile.get('host') or 'Site'))
                action.triggered.connect(lambda checked=False, profile=profile: self.choose_recent_site(pane, profile))
            recent.setEnabled(not pane.busy)
        names = sorted(self.sites, key=str.casefold)
        count = max(1, self.display_value('sites_lines', 25))
        split = self.display_value('split_sites', False)
        folders = set(self.preferences.value('Sites/folders', [], type=list))
        folders.update(profile['_folder'] for profile in self.sites.values() if profile.get('_folder'))
        if menu.actions() and (names or folders): menu.addSeparator()
        def add_sites(parent, site_names):
            for offset, name in enumerate(site_names):
                if split and offset % count == 0:
                    group = parent.addMenu(f'Sites {offset + 1}–{min(offset + count, len(site_names))}')
                target = group if split else parent
                action = target.addAction(name)
                action.setEnabled(not self.sites[name].get('_disabled', False))
                action.triggered.connect(lambda checked=False, name=name: pane.connect_saved_site(name))
        for folder in sorted(folders, key=str.casefold):
            child = menu.addMenu(folder)
            members = [name for name in names if self.sites[name].get('_folder') == folder]
            add_sites(child, members)
            child.setEnabled(bool(members))
        add_sites(menu, [name for name in names if not self.sites[name].get('_folder')])
