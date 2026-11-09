"""Build and wire the desktop window's services and widget tree."""
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QPlainTextEdit, QHeaderView, QAbstractItemView, QTabWidget, QSplitter,
    QToolBar, QStyle)

from . import credentials
from .connection_factory import ConnectionFactory
from .directory_cache import DirectoryCache
from .ident import IdentService
from .layout_options import CaptionPanel, WorkspaceTabBar
from .queue_tree import QueueTree
from .transfer_options import RateLimiter, TransferOptions


def initialize_window(self, smoke, style, settings_factory, standard_paths):
    self._smoke_test = smoke
    self.setWindowTitle("FluxFTP — File transfer workspace")
    self.resize(1600, 900)
    self.preferences = settings_factory()
    if smoke:
        from tempfile import TemporaryDirectory
        self._smoke_directory = TemporaryDirectory(prefix='fluxftp-smoke-')
        self.preferences = QSettings(str(Path(self._smoke_directory.name) / 'settings.ini'), QSettings.Format.IniFormat)
    self.directory_cache = DirectoryCache()
    self.ident = IdentService()
    self.app_instance = QApplication.instance()
    self.STYLE = style
    self.workers = set()
    from .queue_engine import QueueEngine
    from .queue_controller import QueueController
    self.queue_engine = QueueEngine()
    self.queue_controller = QueueController(self)
    self.pending = []
    self.active = {}
    self.queue_jobs = []
    self.job_views = {}
    self.queue_storage_enabled = False
    self.upload_limiter, self.download_limiter = RateLimiter(), RateLimiter()
    self.queue_batch_failed = False
    self.paused = False
    default_data_dir = Path(standard_paths.writableLocation(standard_paths.StandardLocation.AppConfigLocation))
    if smoke:
        default_data_dir = Path(self._smoke_directory.name) / 'data'
    data_dir = Path(self.preferences.value("General/data_path", str(default_data_dir), type=str))
    self.sites_file = data_dir / "sites.json"
    self.credential_vault = credentials.CredentialVault(default_data_dir / "credentials.enc")
    from .key_manager import KeyAccess
    self.key_access = KeyAccess(self, default_data_dir / "keys.enc")
    from .ssh_trust import SSHTrust
    self.ssh_trust = SSHTrust(self, default_data_dir / 'known_hosts')
    from .cloud import CloudService
    self.cloud_service = CloudService(default_data_dir / 'cloud', self.credential_vault)
    self.proxy_credential_id = str(self.preferences.value("Connection/proxy_profile_id", "proxy", type=str))
    if self.proxy_credential_id != "proxy":
        self.proxy_credential_id = f"proxy:{self.proxy_credential_id}"
    try:
        self.proxy_password = self.credential_vault.get(self.proxy_credential_id)
    except RuntimeError:
        self.proxy_password = ""
    self._proxy_password_entered = bool(self.proxy_password)
    self.connection_factory = ConnectionFactory(self)
    try:
        self.sites = json.loads(self.sites_file.read_text("utf-8"))
    except (OSError, ValueError):
        self.sites = {}
    if not isinstance(self.sites, dict):
        self.sites = {}
    if not self.preferences.value("Connection/masq_default_migrated", False, type=bool):
        for profile in self.sites.values():
            if isinstance(profile, dict):
                profile["use_masq"] = False
        if not self.sites or self.save_sites(self.sites):
            self.preferences.setValue("Connection/masq_default_migrated", True)
        self.preferences.setValue("Connection/smart_pasv", False)
    for profile in self.sites.values():
        if isinstance(profile, dict) and profile.get("_credential_id"):
            try:
                self.get_site_password(profile)
            except RuntimeError:
                break
    central = QWidget()
    self.setCentralWidget(central)
    layout = QVBoxLayout(central)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(3)
    self._build_menus()
    self.main_toolbar = QToolBar("File transfer tools", self)
    self.main_toolbar.setObjectName("mainToolbar")
    self.main_toolbar.setMovable(False)
    self.main_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.main_toolbar)
    self._toolbar_action("Sites", QStyle.StandardPixmap.SP_DirOpenIcon, self.manage_sites,
                         "Manage, import, and connect to saved sites (F3)")
    self._toolbar_action("Options", QStyle.StandardPixmap.SP_FileDialogDetailedView, self.open_preferences,
                         "Configure connection, transfer, display, and confirmation options")
    self.main_toolbar.addSeparator()
    self._toolbar_action("Refresh Both", QStyle.StandardPixmap.SP_BrowserReload, self.refresh_both_panes,
                         "Refresh both file listings")
    self._toolbar_action("Start Queue", QStyle.StandardPixmap.SP_MediaPlay, self.play_queue,
                         "Start or resume queued transfers")
    self._toolbar_action("Stop Queue", QStyle.StandardPixmap.SP_MediaStop, self.stop_queue,
                         "Stop queued transfers")
    self.main_toolbar.addSeparator()
    self._toolbar_action("Log Window", QStyle.StandardPixmap.SP_FileDialogDetailedView,
                         self.view_commands.toggle_log_window, "Toggle log window visibility")
    self._toolbar_action("Transfer Window", QStyle.StandardPixmap.SP_FileDialogDetailedView,
                         self.view_commands.toggle_transfer_window, "Toggle transfer window visibility")
    self._toolbar_action("FTP Terminal", QStyle.StandardPixmap.SP_ComputerIcon,
                         self.view_commands.open_ftp_terminal, "Open the FTP command terminal")
    self.main_toolbar.addSeparator()
    self._toolbar_action("New workspace", QStyle.StandardPixmap.SP_FileDialogNewFolder, self.add_workspace,
                         "Open another two-pane workspace")
    self._toolbar_action("Verify SFV", QStyle.StandardPixmap.SP_MessageBoxInformation, self.sfv,
                         "Verify files against an SFV manifest")
    self.tabs = QTabWidget()
    self.tabs.setTabBar(WorkspaceTabBar(self))
    self.tabs.setTabsClosable(True)
    self.tabs.tabCloseRequested.connect(self.close_tab)
    plus = QPushButton("+")
    plus.setToolTip('New workspace')
    plus.setAccessibleName('New workspace')
    plus.clicked.connect(self.add_workspace)
    self.tabs.setCornerWidget(plus)
    self._build_quick_connect(layout)
    self.log = QPlainTextEdit()
    self.log.setAccessibleName('System log')
    self.log.setTabChangesFocus(True)
    self.log.setReadOnly(True)
    self.log.setMaximumBlockCount(self.preferences.value("General/log_lines", 300, type=int))
    self._activity_logging = self.preferences.value("General/log_to_disk", False, type=bool)
    self.log.textChanged.connect(self._write_activity_log)
    self.ftp_log = QPlainTextEdit()
    self.ftp_log.setAccessibleName('FTP log')
    self.ftp_log.setTabChangesFocus(True)
    self.ftp_log.setReadOnly(True)
    self.ftp_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    self.ftp_log_content = QWidget()
    ftp_layout = QVBoxLayout(self.ftp_log_content)
    ftp_layout.setContentsMargins(0, 0, 0, 0)
    ftp_layout.setSpacing(2)
    command_row = QHBoxLayout()
    command_row.setContentsMargins(0, 0, 0, 0)
    command_row.setSpacing(3)
    self.ftp_command_input = QComboBox()
    self.ftp_command_input.setEditable(True)
    self.ftp_command_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    self.ftp_command_input.addItems(['HELP', 'FEAT', 'SYST', 'PWD', 'NOOP', 'SITE HELP', 'SITE WHO'])
    self.ftp_command_input.setCurrentIndex(-1)
    self.ftp_command_input.setCompleter(None)
    self.ftp_command_input.lineEdit().setPlaceholderText('FTP Command')
    self.ftp_command_input.setAccessibleName('FTP Command')
    self.ftp_command_input.lineEdit().setAccessibleName('FTP Command')
    self.ftp_command_input.lineEdit().returnPressed.connect(self.command_commands.inline_raw)
    self.ftp_command_send = QPushButton('Send')
    self.ftp_command_send.clicked.connect(self.command_commands.inline_raw)
    command_row.addWidget(self.ftp_command_input, 1)
    command_row.addWidget(self.ftp_command_send)
    ftp_layout.addLayout(command_row)
    ftp_layout.addWidget(self.ftp_log, 1)
    self.transfer_log = QPlainTextEdit()
    self.transfer_log.setAccessibleName('Transfer log')
    self.transfer_log.setTabChangesFocus(True)
    self.transfer_log.setReadOnly(True)
    self.transfer_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    self.tasks_log = QPlainTextEdit()
    self.tasks_log.setAccessibleName('Tasks log')
    self.tasks_log.setTabChangesFocus(True)
    self.tasks_log.setReadOnly(True)
    self.log_tabs = QTabWidget()
    self.log_tabs.setTabPosition(QTabWidget.TabPosition.South)
    self.log_panels = {}
    for key, widget, title in (('ftp', self.ftp_log_content, 'FTP Log'), ('transfer', self.transfer_log, 'Transfer Log'),
                               ('system', self.log, 'System Log'), ('tasks', self.tasks_log, 'Tasks')):
        panel = CaptionPanel(widget, title)
        self.log_panels[key] = panel
        self.log_tabs.addTab(panel, title)
    self.queue = QueueTree()
    self.queue.setAccessibleName('Transfer queue')
    self.queue.setAlternatingRowColors(True)
    self.queue.setHeaderLabels(["Name", "Progress", "Speed", "Transferred", "Size", "Source path", "Target path", "Remark"])
    self.queue.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    for col in (5, 6):
        self.queue.header().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
    self.queue.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    self.queue_commands.install()
    self.resume = QCheckBox("Resume partial files")
    self.resume.setToolTip("Continue partial local, FTP/FTPS and SFTP transfers when source content is unchanged. SCP and cloud do not support byte-offset resume; choose Overwrite for existing partial files.")
    self.resume.setChecked(self.preferences.value("Transfer/resume", True, type=bool))
    self.resume.toggled.connect(lambda value: self.preferences.setValue("Transfer/resume", value))
    self.add_workspace()
    self.work_area = QSplitter(Qt.Orientation.Vertical)
    self.workspace_panel = CaptionPanel(self.tabs, 'Workspaces', self.add_workspace)
    self.queue_panel = CaptionPanel(self.queue, 'Queue')
    self.work_area.addWidget(self.workspace_panel)
    self.work_area.addWidget(self.log_tabs)
    self.work_area.addWidget(self.queue_panel)
    self.work_area.setStretchFactor(0, 3)
    self.work_area.setStretchFactor(1, 3)
    self.work_area.setStretchFactor(2, 3)
    self.work_area.setSizes([350, 320, 300])
    layout.addWidget(self.work_area, 1)
    self.statusBar().showMessage("Choose Local or Remote in each pane. Connect remote panes to browse; Remote/Remote can use direct FXP.")
    self.statusBar().setVisible(self.preferences.value("Display/status_bar", True, type=bool))
    selection = self.preferences.value("Display/selection_color", "#254b57", type=str)
    self.apply_selection_color(selection)
    self.apply_ident_settings()
    self.initialize_display()
    self.initialize_layout()
    self.initialize_mouse()
    self.view_commands.initialize()
    self.update_file_menu()
    self.tabs.currentChanged.connect(lambda _: self.update_file_menu())
    self.tray_timer.timeout.connect(self.update_file_menu)
    self.ftp_commands.update()
    self.edit_commands.update()
    self.command_commands.update()
    self.tabs.currentChanged.connect(self.ftp_commands.update)
    self.tabs.currentChanged.connect(self.command_commands.update)
    self.tray_timer.timeout.connect(self.ftp_commands.update)
    self.tray_timer.timeout.connect(self.edit_commands.update)
    self.tabs.currentChanged.connect(self.edit_commands.update)
    self.tray_timer.timeout.connect(self.command_commands.update)
    self.initialize_panel_positions()
    self.initialize_header_rows()
    self.apply_fonts()
    from .docking import DockLayout
    self.docking = DockLayout(self)
    from .window_positions import WindowPositions
    self.window_positions = WindowPositions(self)
    self.initialize_workspaces()
    from .layout_lock import LayoutLock
    self.layout_lock = LayoutLock(self)
    from .queue_filter import QueueFilter
    self.queue_filter = QueueFilter(self)
    from .toolbar_visibility import ToolbarVisibility
    self.toolbar_visibility = ToolbarVisibility(self)
    from .themes import SystemAppearance
    self.system_appearance = SystemAppearance(self)
    from .dpi_scaling import DpiScaling
    self.dpi_scaling = DpiScaling(self)
    self.browse_timer = QTimer(self)
    self.browse_timer.setInterval(1000)
    self.browse_timer.timeout.connect(self.maintain_browse_sessions)
    self.browse_timer.start()


