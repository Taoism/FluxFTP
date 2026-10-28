"""Reference-ordered Tools menu, using the existing command controllers."""
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QApplication, QStyle

from .queue_menu import QueueMenu
from .command_menu import CommandMenu
from .compare_menu import CompareMenu


class ToolsMenu(QObject):
    def __init__(self, window, menus):
        super().__init__(window)
        self.window = window
        self.menu = menus.addMenu('Tools')
        self.actions = {}
        self.choices = []
        window.queue_commands = QueueMenu(window, self.menu)
        from .transfer_history import TransferHistory
        window.transfer_history = TransferHistory(window)
        from .conflict_dialog import ConflictController
        window.conflicts = ConflictController(window)
        self.add('Transfer History…', window.transfer_history.show)
        from .bandwidth import BandwidthControl
        window.bandwidth_control = BandwidthControl(window)
        self.add('Unified Bandwidth Management…', window.bandwidth_control.show)
        self.menu.addSeparator()
        self.add('Site Manager…', window.manage_sites, 'F3', QStyle.StandardPixmap.SP_DirOpenIcon)
        from .certification import show_certification
        self.add('Certification Checker…', lambda: show_certification(window))
        from .connection_diagnostics import show_diagnostics
        self.add('Connection Diagnostics…', lambda: show_diagnostics(window))
        self.add('Add to site manager…')
        self.menu.addSeparator()
        window.command_commands = CommandMenu(window, self.menu)
        window.command_commands.designer_action.setIcon(window.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        window.compare_commands = CompareMenu(window, self.menu)
        window.compare_commands.menu.setIcon(window.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        from .sync_dialog import show_sync
        self.add('Synchronize Folders…', lambda: show_sync(window))
        from .deployment_ui import show_deployments
        self.add('Deployment Profiles…', lambda: show_deployments(window))
        self.add('View RAW Directory…')
        self.menu.addSeparator()
        from .automation import Automation
        window.automation = Automation(window)
        from .folder_monitor import FolderMonitor
        window.folder_monitor = FolderMonitor(window)
        from .watch_upload import WatchUploads
        window.watch_uploads = WatchUploads(window)
        self.add('Watch-and-upload Folders…', window.watch_uploads.show)
        self.add('Script Designer…', window.automation.show)
        self.add('Scheduled Automation…', window.automation.show)
        self.menu.addSeparator()
        language = self.menu.addMenu('Languages')
        english = language.addAction('English')
        english.setCheckable(True)
        english.setChecked(True)
        english.setEnabled(False)
        self.menu.addSeparator()
        self.choice('Layout', 'Display/pane_layout', ('Side by side', 'Stacked'), 'Side by side', self.layout)
        self.menu.addSeparator()
        self.add('Hotkey Manager…')
        from .tray_menu import manual_download
        self.add('Manual Download from Site…', lambda: manual_download(window), icon=QStyle.StandardPixmap.SP_ArrowDown)
        from .tftp_dialog import show_tftp
        self.add('TFTP Transfer…', lambda: show_tftp(window))
        self.menu.addSeparator()
        clipboard = self.menu.addMenu('Clipboard')
        clipboard.addAction(window.edit_commands.actions['Copy Selected Names'])
        self.copy_path = clipboard.addAction('Copy Current Path', self.copy_current_path)
        self.paste = clipboard.addAction('Paste Local Files', self.paste_files)
        clipboard.aboutToShow.connect(self.update)
        self.menu.addSeparator()
        self.choice('Transfer Mode', 'Transfer/mode', ('Binary', 'ASCII', 'Auto'), 'Binary')
        self.choice('On Transfer Completed', 'Transfer/completed', ('Idle', 'Disconnect', 'Close FluxFTP'), 'Idle')
        self.menu.addSeparator()
        self.add('Create SFV or MD5 file…')
        self.add('IP Address Translate…')
        self.menu.addSeparator()
        self.add('Restart UPnP service…')
        self.menu.addSeparator()
        self.add('Options…', window.open_preferences, 'F6', QStyle.StandardPixmap.SP_FileDialogDetailedView)
        self.menu.aboutToShow.connect(self.update)

    def add(self, label, callback=None, shortcut='', icon=None):
        action = self.menu.addAction(label)
        if callback:
            action.triggered.connect(callback)
        else:
            action.setEnabled(False)
            action.setToolTip('Not implemented yet.')
        if shortcut:
            action.setShortcut(shortcut)
        if icon is not None:
            action.setIcon(self.window.style().standardIcon(icon))
        self.actions[label] = action
        return action

    def choice(self, title, key, values, default, callback=None):
        menu = self.menu.addMenu(title)
        group = QActionGroup(self)
        for value in values:
            action = menu.addAction(value)
            action.setCheckable(True)
            group.addAction(action)
            def choose(checked=False, value=value):
                self.window.preferences.setValue(key, value)
                if callback:
                    callback(value)
            action.triggered.connect(choose)
            self.choices.append((action, key, value, default))
        menu.aboutToShow.connect(self.update)

    def layout(self, value):
        orientation = Qt.Orientation.Horizontal if value == 'Side by side' else Qt.Orientation.Vertical
        for index in range(self.window.tabs.count()):
            split = self.window.tabs.widget(index)
            if hasattr(split, '_workspace_saved_state'):
                del split._workspace_saved_state
            split.setOrientation(orientation)
            self.window.initialize_pane_positions(split)
        self.window.save_workspaces()

    def copy_current_path(self):
        pane = self.window.mark_commands.pane()
        if pane and (pane.mode == 'Local' or pane.site):
            QApplication.clipboard().setText(pane.path)

    def paste_files(self):
        pane = self.window.mark_commands.pane()
        if pane:
            self.window.paste_local_files(pane)

    def update(self):
        for action, key, value, default in self.choices:
            action.setChecked(self.window.preferences.value(key, default, type=str) == value)
        self.window.edit_commands.update()
        pane = self.window.mark_commands.pane()
        self.copy_path.setEnabled(bool(pane and (pane.mode == 'Local' or pane.site)))
        mime = QApplication.clipboard().mimeData()
        self.paste.setEnabled(bool(pane and pane.mode == 'Remote' and pane.site and not pane.busy
            and self.window.preferences.value('Mouse/allow_paste', True, type=bool)
            and mime and mime.hasUrls() and mime.urls() and all(url.isLocalFile() for url in mime.urls())))
