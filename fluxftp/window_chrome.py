"""Main-window menus, toolbar actions, and quick-connect presentation."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QStyle, QWidget)

from . import __version__
from .cloud import CLOUD_PROTOCOLS
from .core import PROTOCOLS
from .edit_menu import EditMenu
from .ftp_menu import FTPMenu
from .mark_menu import MarkMenu
from .site_dialog import SiteDialog
from .tools_menu import ToolsMenu
from .view_menu import ViewMenu


class WindowChrome:
    """Build the top-level menu, toolbar actions, and quick-connect row."""
    def _toolbar_action(self, text, icon, callback, tooltip, shortcut=None):
        action = QAction(self.style().standardIcon(icon), text, self)
        action.setToolTip(tooltip)
        from .toolbar_icons import DESCRIPTIONS
        action._toolbar_description = next((value for prefix, value in DESCRIPTIONS.items()
                                            if tooltip.startswith(prefix)), None)
        if shortcut:
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(lambda checked=False: callback())
        self.main_toolbar.addAction(action)
        return action

    def _build_menus(self):
        menus = self.menuBar()

        def add(menu, text, callback, shortcut=None):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(shortcut)
                action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(lambda checked=False: callback())
            menu.addAction(action)
            return action

        file_menu = menus.addMenu("File")
        file_menu.setStyleSheet('QMenu::item:disabled { color: #9ba2aa; }')
        self.file_menu = file_menu
        new_menu = file_menu.addMenu('New')
        add(new_menu, 'New Window', self.add_workspace, 'Ctrl+N')
        self.clone_window_action = add(new_menu, 'New Window Of Current', self.new_window_of_current, 'Ctrl+Shift+N')
        self.close_window_action = add(file_menu, 'Close Current Window', lambda: self.close_tab(self.tabs.currentIndex()), 'Ctrl+W')
        self.close_window_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        file_menu.addSeparator()
        tray_action = add(file_menu, 'Minimize to system tray', self.file_minimize_to_tray, 'F12')
        tray_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        file_menu.addSeparator()
        add(file_menu, 'Backup configuration now…', self.backup_configuration)
        add(file_menu, 'Restore configuration from backup file…', self.restore_configuration_backup)
        file_menu.addSeparator()
        add(file_menu, 'Exit…', self.close, 'Alt+X')
        file_menu.aboutToShow.connect(self.update_file_menu)
        quick_action = QAction('Quick Connect', self)
        quick_action.setShortcut('Ctrl+K')
        quick_action.triggered.connect(self.quick_connect)
        self.addAction(quick_action)

        self.edit_commands = EditMenu(self, menus)
        self.edit_menu = self.edit_commands.menu

        self.mark_commands = MarkMenu(self, menus)
        self.mark_menu = self.mark_commands.menu

        self.view_commands = ViewMenu(self, menus)
        self.view_menu = self.view_commands.menu

        self.ftp_commands = FTPMenu(self, menus)
        self.ftp_menu = self.ftp_commands.menu

        self.tools_commands = ToolsMenu(self, menus)
        self.tools_menu = self.tools_commands.menu
        # Share the main-window F6 action so Options is reachable from Edit too.
        self.edit_menu.addAction(self.tools_commands.actions['Options…'])

        help_menu = menus.addMenu("Help")
        self.help_menu = help_menu
        help_menu.setStyleSheet('QMenu::item:disabled { color: #9ba2aa; }')
        from .help_viewer import show_help
        help_index = add(help_menu, 'Help Index', lambda: show_help(self), 'F1')
        help_index.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogHelpButton))
        from .setup_wizard import show_wizard
        self.wizard_action = add(help_menu, 'FluxFTP Wizard…', lambda: show_wizard(self))
        about = self.about_action = add(help_menu, "About FluxFTP…", lambda: QMessageBox.about(
            self, "About FluxFTP", f"FluxFTP {__version__}\n\nA multi-platform (Windows, Ubuntu, Debian) FTP, FTPS, SFTP and cloud-storage client with synchronization, verification reports and deployment rollback."))
        about.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))

    def _build_quick_connect(self, layout):
        row = QWidget()
        self.quick_connect_row = row
        row.setObjectName("quickConnect")
        line = QHBoxLayout(row)
        line.setContentsMargins(5, 3, 5, 3)
        line.setSpacing(5)

        def labeled(label, widget, width=None):
            caption = QLabel(label)
            caption.setBuddy(widget)
            from .accessibility import name_control
            name_control(widget, 'Quick connect ' + {'User': 'username', 'Pass': 'password'}.get(label, label.lower()))
            line.addWidget(caption)
            if width:
                widget.setFixedWidth(width)
            line.addWidget(widget)

        self.quick_target = QComboBox()
        self.quick_target.addItems(["Left pane", "Right pane"])
        labeled("Target", self.quick_target, 90)
        self.quick_protocol = QComboBox()
        self.quick_protocol.addItems(PROTOCOLS)
        labeled("Protocol", self.quick_protocol, 165)
        self.quick_host = QLineEdit()
        self.quick_host.setPlaceholderText("Server host or IP")
        labeled("Host", self.quick_host, 180)
        self.quick_port = QSpinBox()
        self.quick_port.setRange(1, 65535)
        self.quick_port.setValue(21)
        self._quick_last_protocol_index = self.quick_protocol.currentIndex()
        self._quick_port_customized = False
        self._quick_updating_port = False
        self.quick_port.valueChanged.connect(self._quick_port_changed)
        labeled("Port", self.quick_port, 78)
        self.quick_username = QLineEdit()
        self.quick_username.setPlaceholderText("Username")
        labeled("User", self.quick_username, 125)
        self.quick_password = QLineEdit()
        self.quick_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.quick_password.setPlaceholderText("Password")
        labeled("Pass", self.quick_password, 120)
        self.quick_path = QLineEdit()
        self.quick_path.setPlaceholderText("Remote path")
        labeled("Path", self.quick_path, 170)
        connect = QPushButton("Connect")
        connect.setObjectName("primary")
        connect.clicked.connect(self.quick_connect)
        line.addWidget(connect)
        line.addStretch(1)
        self.quick_protocol.currentIndexChanged.connect(self._quick_protocol_changed)
        layout.addWidget(row)

    def _quick_port_changed(self, _value):
        if not self._quick_updating_port:
            self._quick_port_customized = True

    def _quick_protocol_changed(self, index):
        for control in (self.quick_host, self.quick_port, self.quick_username, self.quick_password):
            control.setEnabled(PROTOCOLS[index] not in CLOUD_PROTOCOLS)
        previous_default = SiteDialog._default_port(self._quick_last_protocol_index)
        if not self._quick_port_customized and self.quick_port.value() == previous_default:
            self._quick_updating_port = True
            self.quick_port.setValue(SiteDialog._default_port(index))
            self._quick_updating_port = False
        self._quick_last_protocol_index = index

