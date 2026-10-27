from __future__ import annotations

import json
import argparse
import os
import posixpath
import sys
import ssl
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from PySide6.QtCore import Qt, QThread, Signal, QStandardPaths, QTimer, QSettings, QMimeData, QSignalBlocker
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QShortcut, QDrag
from cryptography import x509
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QSpinBox, QCheckBox, QDialog, QFormLayout,
    QDialogButtonBox, QFileDialog, QSplitter, QTreeWidget, QTreeWidgetItem, QHeaderView,
    QAbstractItemView, QTabWidget, QPlainTextEdit, QMessageBox, QInputDialog, QToolBar, QToolButton, QMenu, QStyle, QStackedWidget, QGroupBox, QGridLayout, QScrollArea)

from .core import Site, Entry, Connection, PROTOCOLS, Cancelled, CertificateTrustRequired, normalize_host, safe_name, transfer_tree, verify_sfv, bypass_proxy
from .site_import import validate_site
from .site_manager import SitesDialog
from .preferences import PreferencesDialog
from . import diagnostics
from . import credentials
from .ident import IdentService
from .transfer_options import TransferOptions, RateLimiter
from .site_options import SiteSettings, transfer_settings, decode as site_options
from .queue_manager import QueueManager
from .priority_rules import DEFAULT_RULES, priority_rank
from .display_options import DisplayOptions, AddressBar
from .sort_options import sorted_entries
from .color_options import ColorOptions
from .layout_options import LayoutOptions, CaptionPanel, WorkspaceTabBar
from .mouse_actions import MouseActions
from .confirm_options import ConfirmOptions
from .file_menu import FileMenu
from .workspaces import Workspaces
from .mark_menu import MarkMenu
from .edit_menu import EditMenu
from .tools_menu import ToolsMenu
from .view_menu import ViewMenu
from .ftp_menu import FTPMenu
from .directory_cache import DirectoryCache
from .browse_session import BrowseSession
from .queue_tree import QueueTree, selected_rows
from .queue_tree import STATE_ROLE
from .transfer_reporting import finish as report_transfer, update_group, append as append_transfer_log
from .queue_menu import QueueMenu
from .compare_menu import CompareMenu, RESULT_ROLE
from .command_menu import CommandMenu
from . import __version__


STYLE = """
QWidget { background:#f2f3f5; color:#17202a; }
QMainWindow { background:#e8edf5; }
QMenuBar { background:#f7f7f7; border-bottom:1px solid #c7cdd6; padding:2px 4px; }
QMenuBar::item { padding:4px 9px; background:transparent; }
QMenuBar::item:selected { background:#dce9fb; }
QMenu { background:#fff; border:1px solid #aeb8c5; }
QMenu::item { padding:5px 26px 5px 24px; }
QMenu::item:selected { background:#dce9fb; }
QLabel#brand { font-size:19px; font-weight:600; color:#164e91; }
QLabel#muted { color:#526477; }
QPushButton { background:#f5f6f8; border:1px solid #aeb7c2; padding:4px 9px; border-radius:2px; }
QPushButton:hover { background:#e4eefb; border-color:#6e9ed3; }
QPushButton:pressed { background:#d4e4f7; }
QPushButton:disabled { color:#9aa3ad; background:#eceef1; }
QPushButton#primary { background:#dceafa; border:1px solid #78a6d5; color:#123e70; font-weight:600; }
QLineEdit,QSpinBox,QComboBox { background:#fff; border:1px solid #aeb7c2; border-radius:1px; padding:4px 6px; selection-background-color:#2c70bb; }
QTreeWidget,QPlainTextEdit { background:#fff; border:1px solid #abb5c1; alternate-background-color:#f5f8fc; }
QTreeWidget::item { padding:3px 5px; }
QTreeWidget::item:selected { background:#cfe2f8; color:#10263d; }
QHeaderView::section { background:#eef1f5; color:#243447; border:0; border-right:1px solid #c8cdd4; border-bottom:1px solid #b7bec8; padding:5px 7px; }
QTabWidget::pane { border:1px solid #aab4c1; background:#fff; }
QTabBar::tab { background:#e5e9ef; border:1px solid #bdc6d1; padding:5px 12px; margin-right:2px; }
QTabBar::tab:selected { background:#fff; color:#164e91; border-bottom-color:#fff; }
QSplitter::handle { background:#b9c9df; }
QToolTip { color:#17202a; background:#fffde8; border:1px solid #aeb7c2; }
QToolBar { background:#edf0f4; border:0; border-bottom:1px solid #c2c9d2; spacing:3px; padding:3px 6px; }
QToolBar QToolButton { background:transparent; border:1px solid transparent; border-radius:2px; padding:4px 7px; }
QToolBar QToolButton:hover { background:#deebfa; border-color:#9bb9dc; }
QStatusBar { background:#e9edf3; border-top:1px solid #c3cad3; }
"""


def settings_store():
    """Use Qt's per-user settings backend (registry on Windows, native settings elsewhere)."""
    return QSettings("FluxFTP", "FluxFTP")


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class Worker(QThread):
    result = Signal(object)
    failed = Signal(object)
    file_started = Signal(str, str)
    progress = Signal(int, int, object, object)  # byte counts may exceed 32 bits

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel = Event()
        self.last = 0.0
        self.started_at = time.monotonic()
        self.stream_speed = None
        self.stream_bytes = self.file_bytes = 0

    def begin_file(self):
        from .transfer_speed import RollingSpeed
        if self.stream_speed is None:
            self.stream_speed = RollingSpeed()
            self.stream_speed.sample(0)
        self.stream_bytes += self.file_bytes
        self.file_bytes = 0
        self.last = 0

    def report(self, done, total):
        now = time.monotonic()
        if now - self.last > .15 or done == total:
            self.last = now
            self.file_bytes = done
            rate = (self.stream_speed.sample(self.stream_bytes + done) / 1024 if self.stream_speed else
                    done / max(.1, now - self.started_at) / 1024)
            self.progress.emit(int(100 * done / total) if total else 100,
                               min(2147483647, int(rate)),
                               self.stream_bytes + done if self.stream_speed else done,
                               0 if self.stream_speed else total)

    def run(self):
        try:
            self.result.emit(self.function(self))
        except Exception as e:
            diagnostics.worker_failure(e)
            self.failed.emit(e)


class SiteDialog(QDialog):
    """FTP Rush-style site properties with a category tree and settings pages."""
    def __init__(self, parent, sites, editing=False, profile_only=False):
        super().__init__(parent)
        self.editing = editing
        self.profile_only = profile_only
        self.setWindowTitle("Property" if editing or profile_only else "Connect to a site")
        self.resize(780, 690)
        self.setStyleSheet('QLineEdit, QSpinBox, QComboBox { padding: 2px 4px; } QPushButton { padding: 3px 9px; }')
        self.setMinimumSize(700, 520)
        self.tls_cert_sha256 = ""
        self.tls_pin_host = ""
        self.tls_pin_port = 0
        self.tls_pin_protocol = ""
        self.fields = {key: QLineEdit() for key in
                       ("name", "host", "username", "root", "local_path", "ca_file", "cert_file", "cert_key", "ssh_key", "known_hosts")}
        self.managed_key = QComboBox()
        self.managed_key.addItem('None — use existing file/agent settings', '')
        self.reload_managed_keys()
        self.cloud_account = QComboBox()
        self.cloud_account.addItem('Select a cloud account', '')
        self.reload_cloud_accounts()
        self.fields["username"].setText("anonymous")
        self.fields["root"].setText("/")
        self.description = QPlainTextEdit()
        self.description.setMaximumHeight(48)
        self.protocol = QComboBox()
        self.protocol.addItems(PROTOCOLS)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(21)
        self._port_customized = False
        self._updating_port = False
        self._last_protocol_index = self.protocol.currentIndex()
        self.port.valueChanged.connect(self._port_changed)
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.passive = QCheckBox("Passive Mode")
        self.force_utf8 = QCheckBox("Forced UTF-8")
        self.utf8_username = QCheckBox("UTF-8 Username")
        self.utf8_username.setChecked(True)
        self.use_masq = QCheckBox("Server use NAT/Non-routable/Masq IP")
        self.site_timezone = QComboBox()
        self.site_timezone.addItem("Unknown / Do not adjust", "")
        for offset in (-12, -11, -10, -9, -8, -7, -6, -5, -4, -3, -2, -1,
                       1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14):
            sign = "+" if offset > 0 else "−"
            label = f"UTC{sign}{abs(offset):02d}:00"
            self.site_timezone.addItem(label, label.replace("−", "-"))
        self.single_connection = QCheckBox("Single connection (disable parallel transfers)")
        self.max_transfers_total = QSpinBox()
        self.max_downloads = QSpinBox()
        self.max_uploads = QSpinBox()
        for control in (self.max_transfers_total, self.max_downloads, self.max_uploads):
            control.setRange(1, 64)
            control.setValue(6)
        preferences = getattr(parent, "window", None)
        default_passive = preferences.preferences.value("Connection/default_passive", True, type=bool) if preferences and hasattr(preferences, "preferences") else True
        self._default_passive = default_passive
        self.passive.setChecked(default_passive)
        self._default_masq = (preferences.preferences.value("Connection/smart_pasv", True, type=bool)
                              if preferences and hasattr(preferences, "preferences") else True)
        self.use_masq.setChecked(self._default_masq)

        outer = QVBoxLayout(self)
        main = QHBoxLayout()
        self.navigation = QTreeWidget()
        self.navigation.setObjectName("sitePropertyNavigation")
        self.navigation.setAccessibleName('Site properties categories')
        self.navigation.setHeaderHidden(True)
        self.navigation.setFixedWidth(158)
        self.navigation.setIndentation(17)
        self.pages = QStackedWidget()
        self._page_indices = {}
        from .site_properties import PropertyPages
        self.property_editor = PropertyPages(self)
        self._add_property_page("General", "general", self._general_page)
        for label, key, parent in (
            ("Bookmark", "bookmark", "General"), ("URL", "url", "General"),
            ("Display", "display", "General"), ("Connection", "connection", None),
            ("Account", "account", "Connection"), ("Ident", "ident", "Connection"),
            ("Proxy Server", "proxy", "Connection"), ("List Mode", "list_mode", "Connection"),
            ("SSL", "ssl", "Connection"), ("SFTP", "sftp", "Connection"),
            ("Transfer", "transfer", None), ("Skip List", "skip_list", "Transfer"),
            ("Priority", "priority", "Transfer"), ("File Exists", "file_exists", "Transfer"),
            ("Notification", "notification", None)):
            builder = self._ssl_page if label == 'SSL' else self._sftp_page if label == 'SFTP' else lambda title=label: self.property_editor.build(title)
            self._add_property_page(label, key, builder, parent=parent)
        self.navigation.expandAll()
        self.navigation.currentItemChanged.connect(self._show_property_page)
        self.navigation.setCurrentItem(self._nav_items["General"])
        main.addWidget(self.navigation)
        main.addWidget(self.pages, 1)
        outer.addLayout(main, 1)

        if not editing and not profile_only:
            self.saved = QComboBox()
            self.saved.setAccessibleName('Saved connection')
            self.saved.addItem("New connection")
            self.saved.addItems(list(sites))
            outer.insertWidget(0, self.saved)
            self.saved.currentTextChanged.connect(lambda name: self.load(sites.get(name)))
        else:
            self.saved = None

        footer = QHBoxLayout()
        footer.addStretch()
        self.test_button = QPushButton("Test…")
        self.test_button.clicked.connect(self.test_current)
        footer.addWidget(self.test_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("OK" if editing or profile_only else "Save & connect")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(footer)

        from .accessibility import label_forms, name_control
        label_forms(self)
        for control, name in ((self.protocol, 'Site protocol'), (self.port, 'Site port'),
                (self.password, 'Site password'), (self.managed_key, 'Managed key'),
                (self.cloud_account, 'Cloud account'), (self.site_timezone, 'Site time zone'),
                (self.description, 'Site description'), (self.max_transfers_total, 'Maximum total transfers'),
                (self.max_downloads, 'Maximum downloads'), (self.max_uploads, 'Maximum uploads')):
            name_control(control, name)
        for key, name in (('name', 'Site name'), ('host', 'Site host'), ('username', 'Site username')):
            name_control(self.fields[key], name)
        self.description.setTabChangesFocus(True)
        order = [self.protocol, self.fields['host'], self.port, self.fields['name'],
                 self.fields['username'], self.password, self.anonymous_login, self.passive,
                 self.force_utf8, self.use_masq, self.utf8_username, self.site_timezone,
                 self.managed_key, self.max_transfers_total, self.max_downloads, self.max_uploads,
                 self.fields['root'], self.fields['local_path'], self.description]
        for previous, following in zip(order, order[1:]):
            QWidget.setTabOrder(previous, following)
        from .accessibility import fit_dialog
        fit_dialog(self)

    def _add_property_page(self, label, key, builder, parent=None):
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        self._nav_items = getattr(self, "_nav_items", {})
        self._nav_items[label] = item
        if parent:
            self._nav_items[parent].addChild(item)
        else:
            self.navigation.addTopLevelItem(item)
        page = builder() if callable(builder) else builder
        self._page_indices[key] = self.pages.addWidget(page)

    def _show_property_page(self, item, _previous):
        if item is None:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key in self._page_indices:
            self.pages.setCurrentIndex(self._page_indices[key])

    @staticmethod
    def _property_page(title):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 2, 4, 4)
        heading = QLabel(title)
        heading.setStyleSheet("font-size:16px; font-weight:600; border-bottom:1px solid #bfc8d4; padding-bottom:4px;")
        layout.addWidget(heading)
        return page, layout

    def _anonymous_toggled(self, checked):
        username = self.fields["username"]
        if checked:
            username.setText("anonymous")
        elif username.text().casefold() == "anonymous":
            username.clear()

    def _general_page(self):
        page, page_layout = self._property_page("Site")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 5, 10, 5)
        scroll.setWidget(content)
        page_layout.addWidget(scroll)
        site_box = QGroupBox("Site")
        grid = QGridLayout(site_box)
        grid.setVerticalSpacing(3)
        grid.addWidget(QLabel("Protocol"), 0, 0)
        grid.addWidget(self.protocol, 0, 1, 1, 3)
        grid.addWidget(QLabel("Host"), 1, 0)
        grid.addWidget(self.fields["host"], 1, 1)
        grid.addWidget(QLabel("Port"), 1, 2)
        grid.addWidget(self.port, 1, 3)
        grid.addWidget(QLabel("Site name"), 2, 0)
        grid.addWidget(self.fields["name"], 2, 1, 1, 3)
        grid.addWidget(QLabel("Username"), 3, 0)
        grid.addWidget(self.fields["username"], 3, 1)
        grid.addWidget(QLabel("Password"), 3, 2)
        grid.addWidget(self.password, 3, 3)
        self.anonymous_login = QCheckBox("Anonymous Login")
        self.anonymous_login.setChecked(True)
        self.anonymous_login.toggled.connect(self._anonymous_toggled)
        self.fields["username"].textChanged.connect(lambda value: self.anonymous_login.setChecked(value.casefold() == "anonymous"))
        grid.addWidget(self.anonymous_login, 4, 0, 1, 2)
        grid.addWidget(self.passive, 4, 2, 1, 2)
        grid.addWidget(self.force_utf8, 5, 0, 1, 2)
        grid.addWidget(self.use_masq, 6, 0, 1, 3)
        grid.addWidget(self.utf8_username, 7, 0, 1, 2)
        grid.addWidget(QLabel("Time Zone"), 8, 0)
        grid.addWidget(self.site_timezone, 8, 1, 1, 3)
        grid.addWidget(QLabel('Managed key'), 9, 0)
        grid.addWidget(self.managed_key, 9, 1, 1, 2)
        key_manager_button = QPushButton('Key Manager…')
        key_manager_button.clicked.connect(self.manage_keys)
        grid.addWidget(key_manager_button, 9, 3)
        self.managed_key.setToolTip('SFTP: SSH identity. FTPS: client certificate. Overrides private-key file fields when selected.')
        self.cloud_label = QLabel('Cloud account')
        grid.addWidget(self.cloud_label, 10, 0)
        grid.addWidget(self.cloud_account, 10, 1, 1, 2)
        self.cloud_button = QPushButton('Cloud Accounts…')
        self.cloud_button.clicked.connect(self.manage_cloud_accounts)
        grid.addWidget(self.cloud_button, 10, 3)
        for control in (self.cloud_label, self.cloud_account, self.cloud_button):
            control.setVisible(self.protocol.currentIndex() >= 5)
        self.cloud_hidden_controls = []
        for row in (1, 3, 4, 5, 6, 7, 8, 9):
            for column in range(4):
                item = grid.itemAtPosition(row, column)
                if item and item.widget() and item.widget() not in self.cloud_hidden_controls:
                    self.cloud_hidden_controls.append(item.widget())
        layout.addWidget(site_box)

        transfer_box = QGroupBox("Maximum simultaneous transfers per tab")
        transfer_box.setToolTip('Each file transfer uses its own login and worker. Folder contents are queued as separate files.')
        transfer_grid = QGridLayout(transfer_box)
        transfer_grid.addWidget(self.single_connection, 0, 0, 1, 4)
        for column, (label, control) in enumerate((("Total", self.max_transfers_total),
                                                    ("Download", self.max_downloads),
                                                    ("Upload", self.max_uploads))):
            transfer_grid.addWidget(QLabel(label), 1, column * 2)
            transfer_grid.addWidget(control, 1, column * 2 + 1)
        layout.addWidget(transfer_box)

        folder_box = QGroupBox("Folder")
        folder_form = QFormLayout(folder_box)
        folder_form.addRow("Remote Path", self.fields["root"])
        local_path_row = QWidget()
        local_path_layout = QHBoxLayout(local_path_row)
        local_path_layout.setContentsMargins(0, 0, 0, 0)
        local_path_layout.addWidget(self.fields["local_path"], 1)
        local_path_browse = QPushButton("📁")
        local_path_browse.setToolTip("Choose local folder")
        local_path_browse.clicked.connect(self.browse_folder)
        local_path_layout.addWidget(local_path_browse)
        folder_form.addRow("Local Path", local_path_row)
        layout.addWidget(folder_box)
        description_box = QGroupBox("Description")
        description_layout = QVBoxLayout(description_box)
        description_layout.addWidget(self.description)
        layout.addWidget(description_box)
        layout.addStretch()
        note = QLabel("TLS 1.2+ with certificate verification · SSH host keys must be trusted · passwords are stored in the encrypted vault")
        self.site_security_note = note
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _update_url_preview(self, *_):
        if self.protocol.currentIndex() >= 5:
            self.url_preview.setText('Cloud account — provider authorization is managed in Cloud Accounts.')
            return
        from urllib.parse import quote
        schemes = {PROTOCOLS[0]: "ftp", PROTOCOLS[1]: "ftps", PROTOCOLS[2]: "ftps", PROTOCOLS[3]: "sftp", PROTOCOLS[4]: "ftp"}
        user = quote(self.fields["username"].text().strip(), safe="")
        host = self.fields["host"].text().strip()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        self.url_preview.setText(f"{schemes.get(self.protocol.currentText(), 'ftp')}://{user + '@' if user else ''}{host}:{self.port.value()}{self.fields['root'].text()}")

    def _display_field_page(self, title, pairs):
        page, layout = self._property_page(title)
        form = QFormLayout()
        for label, field_key in pairs:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(self.fields[field_key], 1)
            browse = QPushButton("…")
            browse.clicked.connect(lambda checked=False, f=self.fields[field_key]: self.browse(f))
            line.addWidget(browse)
            form.addRow(label, row)
        layout.addLayout(form)
        layout.addStretch()
        return page

    def _ssl_page(self):
        page = self._display_field_page("SSL", [("Custom CA (PEM)", "ca_file"), ("TLS client certificate", "cert_file"), ("TLS client private key", "cert_key")])
        group = QGroupBox('Secure Data Channel')
        form = QFormLayout(group)
        for title in ('Send PBSZ 0 after SSL AUTH', 'Encrypt data when listing', 'Encrypt data when transferring'):
            control = QCheckBox(title)
            control.setChecked(True)
            control.setEnabled(False)
            control.setToolTip('Always enabled for FTPS in FluxFTP.')
            form.addRow(control)
        page.layout().insertWidget(1, group)
        button = QPushButton('Key Manager…')
        button.clicked.connect(self.manage_keys)
        page.layout().insertWidget(3, button)
        return page

    def _sftp_page(self):
        page = self._display_field_page("Public Key Authentication", [("SSH known_hosts file", "known_hosts")])
        keys = QTreeWidget()
        keys.setAccessibleName('Supported SSH key algorithms')
        keys.setRootIsDecorated(False)
        keys.setHeaderLabels(['File name'])
        def update_keys(value):
            keys.clear()
            if value:
                keys.addTopLevelItem(QTreeWidgetItem([value]))
        self.fields['ssh_key'].textChanged.connect(update_keys)
        page.layout().insertWidget(1, keys, 1)
        buttons = QHBoxLayout()
        for title, callback in [('Add…', lambda: self.browse(self.fields['ssh_key'])),
                                ('Delete', self.fields['ssh_key'].clear), ('Key Manager…', self.manage_keys)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        page.layout().insertLayout(2, buttons)
        note = QLabel('One explicit key file is supported. A managed key selected in General takes precedence; otherwise SSH agent/default keys are also available.')
        note.setWordWrap(True)
        page.layout().insertWidget(3, note)
        return page

    @staticmethod
    def _default_port(index):
        return 443 if index >= 5 else 22 if index == 3 else 990 if index == 2 else 21

    def _port_changed(self, _value):
        if not self._updating_port:
            self._port_customized = True
        if hasattr(self, "url_preview"):
            self._update_url_preview()

    def _protocol_changed(self, index):
        previous_default = self._default_port(self._last_protocol_index)
        if (not self.editing and not self._port_customized
                and self.port.value() == previous_default):
            self._updating_port = True
            self.port.setValue(self._default_port(index))
            self._updating_port = False
        self._last_protocol_index = index
        if hasattr(self, 'cloud_button'):
            is_cloud = index >= 5
            for control in (self.cloud_account, self.cloud_label, self.cloud_button):
                control.setVisible(is_cloud)
            for control in (self.fields['host'], self.fields['username'], self.password, self.port, self.passive, self.managed_key):
                control.setEnabled(not is_cloud)
            for control in getattr(self, 'cloud_hidden_controls', []):
                control.setVisible(not is_cloud)
            for name in ('Ident', 'Proxy Server', 'List Mode', 'SSL', 'SFTP'):
                if name in getattr(self, '_nav_items', {}):
                    self._nav_items[name].setDisabled(is_cloud)
            if hasattr(self, 'site_security_note'):
                self.site_security_note.setText('Choose an authorized cloud account. Remote Path selects the folder, bucket or container to open.' if is_cloud else
                    'TLS 1.2+ with certificate verification · SSH host keys must be trusted · passwords are stored in the encrypted vault')
        if hasattr(self, "url_preview"):
            self._update_url_preview()

    def browse(self, field):
        path, _ = QFileDialog.getOpenFileName(self, "Choose file")
        if path:
            field.setText(path)

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose local folder", self.fields["local_path"].text())
        if path:
            self.fields["local_path"].setText(path)

    def load(self, data):
        options = site_options((data or {}).get('site_options', '{}'))
        if data and 'Site/bookmarks' not in options and self.key_owner():
            try:
                legacy = json.loads(self.key_owner().preferences.value('Bookmarks/sites', '{}', type=str)).get(data.get('name'), [])
                options['Site/bookmarks'] = [dict(name=item.get('name') or item['path'], path=item['path'], local=item.get('local', ''))
                                             for item in legacy if isinstance(item, dict) and isinstance(item.get('path'), str)]
            except (ValueError, TypeError, AttributeError):
                pass
        self.property_editor.load(json.dumps(options))
        account_id = data.get('cloud_account', '') if data else ''
        account_index = self.cloud_account.findData(account_id)
        if account_index < 0:
            self.cloud_account.addItem('Missing cloud account', account_id)
            account_index = self.cloud_account.count() - 1
        self.cloud_account.setCurrentIndex(account_index)
        identity = data.get('managed_key', '') if data else ''
        index = self.managed_key.findData(identity)
        if index < 0:
            self.managed_key.addItem('Missing managed key', identity)
            index = self.managed_key.count() - 1
        self.managed_key.setCurrentIndex(index)
        if data:
            self.tls_cert_sha256 = data.get("tls_cert_sha256", "")
            self.tls_pin_host = data.get("tls_pin_host", "")
            self.tls_pin_port = data.get("tls_pin_port", 0)
            self.tls_pin_protocol = data.get("tls_pin_protocol", "")
            for key, field in self.fields.items():
                field.setText(data.get(key, ""))
            self.description.setPlainText(data.get("description", ""))
            self.force_utf8.setChecked(data.get("force_utf8", False))
            self.utf8_username.setChecked(data.get("utf8_username", True))
            use_masq = data.get("use_masq")
            if use_masq is None:
                parent = self.parent()
                owner = parent if hasattr(parent, "preferences") else getattr(parent, "window", None)
                use_masq = owner.preferences.value("Connection/smart_pasv", True, type=bool) if owner and hasattr(owner, "preferences") else True
            self.use_masq.setChecked(bool(use_masq))
            timezone_index = self.site_timezone.findData(data.get("timezone", ""))
            self.site_timezone.setCurrentIndex(max(0, timezone_index))
            self.single_connection.setChecked(data.get("single_connection", False))
            self.max_transfers_total.setValue(data.get("max_transfers_total", 6))
            self.max_downloads.setValue(data.get("max_downloads", 6))
            self.max_uploads.setValue(data.get("max_uploads", 6))
            self.protocol.setCurrentText(data["protocol"])
            self.port.setValue(data["port"])
            self.passive.setChecked(data.get("passive", True))
            try:
                parent = self.parent()
                owner = parent if hasattr(parent, "get_site_password") else getattr(parent, "window", None)
                self.password.setText(owner.get_site_password(data) if owner and hasattr(owner, "get_site_password") else "")
            except RuntimeError as error:
                QMessageBox.warning(self, "Secure password storage unavailable", str(error))
                self.password.clear()
        else:
            for key, field in self.fields.items():
                field.clear()
            self.fields["username"].setText("anonymous")
            self.fields["root"].setText("/")
            self.password.clear()
            self.description.clear()
            self.tls_cert_sha256 = self.tls_pin_host = self.tls_pin_protocol = ""
            self.tls_pin_port = 0
            self.protocol.setCurrentIndex(0)
            self._port_customized = False
            self.port.setValue(self._default_port(0))
            self.passive.setChecked(self._default_passive)
            self.force_utf8.setChecked(False)
            self.utf8_username.setChecked(True)
            self.use_masq.setChecked(self._default_masq)
            self.site_timezone.setCurrentIndex(0)
            self.single_connection.setChecked(False)
            self.max_transfers_total.setValue(6)
            self.max_downloads.setValue(6)
            self.max_uploads.setValue(6)
        self._update_url_preview()
        if self.editing or self.profile_only:
            site_name = data.get("name", "") if data else ""
            self.setWindowTitle(f"Property - {site_name}" if site_name else "Property")

    def test_current(self):
        parent = self.parent()
        owner = parent if hasattr(parent, "new_connection") else getattr(parent, "window", None)
        if not owner or not hasattr(owner, "new_connection"):
            return
        try:
            site = self.site()
            validate_site(site)
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, 'Invalid site', str(error))
            return
        if not owner.ensure_proxy_credentials(self, site.host, site):
            return
        password = self.password.text()
        if not password and site.username.casefold() != "anonymous" and not site.cloud_account and not (site.managed_key and site.protocol == PROTOCOLS[3]):
            password, accepted = QInputDialog.getText(self, "Test site", f"Password for {site.username}@{site.host}:", QLineEdit.EchoMode.Password)
            if not accepted:
                return
        def check(_worker):
            connection = owner.new_connection(site, password)
            try:
                connection.connect()
                entries = connection.list(site.root)
                return connection.security, len(entries)
            finally:
                connection.close()
        def done(result):
            QMessageBox.information(self, "Test successful", f"Connection and directory test succeeded.\nTransport: {result[0]}\n{result[1]} entries listed in {site.root}.")
        owner.run(check, done, lambda error: QMessageBox.warning(self, "Test failed", str(error)))

    def validate(self):
        try:
            site = self.site()
            validate_site(site)
            if site.cloud_account and self.key_owner():
                self.key_owner().cloud_service.fs(site)
            if site.managed_key and self.key_owner():
                record = self.key_owner().key_access.store.metadata().get(site.managed_key)
                if record is None:
                    raise ValueError('The selected managed key is missing.')
                if record['kind'] != ('ssh' if site.protocol == PROTOCOLS[3] else 'tls'):
                    raise ValueError('Choose an SSH key for SFTP or an X.509 certificate for FTPS.')
        except (ValueError, RuntimeError, OSError) as e:
            QMessageBox.warning(self, "Invalid site", str(e))
            return
        preferences = getattr(self.parent(), "window", None)
        should_warn = preferences.preferences.value("Confirm/unencrypted", True, type=bool) if preferences and hasattr(preferences, "preferences") else True
        if not self.editing and not self.profile_only and self.protocol.currentIndex() == 4 and should_warn and QMessageBox.question(self, "Unencrypted connection",
                "Plain FTP sends your password and files without encryption. Continue?") != QMessageBox.StandardButton.Yes:
            return
        self.accept()

    def site(self):
        values = {key: field.text().strip() for key, field in self.fields.items()}
        cloud_id = self.cloud_account.currentData() if self.protocol.currentIndex() >= 5 else ''
        if self.protocol.currentIndex() >= 5:
            values['host'] = 'cloud-' + (cloud_id or 'unconfigured')
            values['username'] = ''
            values['name'] = values['name'] or (self.cloud_account.currentText() if cloud_id else '')
        values["name"] = values["name"] or values["host"]
        values["root"] = values["root"] or "/"
        return Site(**values, protocol=self.protocol.currentText(), port=self.port.value(), passive=self.passive.isChecked(),
                    managed_key='' if self.protocol.currentIndex() >= 5 else self.managed_key.currentData() or '',
                    cloud_account=cloud_id or '', site_options=self.property_editor.dump(),
                    tls_cert_sha256=self.tls_cert_sha256, tls_pin_host=self.tls_pin_host,
                    tls_pin_port=self.tls_pin_port, tls_pin_protocol=self.tls_pin_protocol,
                    description=self.description.toPlainText().strip(), force_utf8=self.force_utf8.isChecked(),
                    utf8_username=self.utf8_username.isChecked(), use_masq=self.use_masq.isChecked(),
                    timezone=self.site_timezone.currentData() or "", single_connection=self.single_connection.isChecked(),
                    max_transfers_total=self.max_transfers_total.value(), max_downloads=self.max_downloads.value(),
                    max_uploads=self.max_uploads.value())

    def key_owner(self):
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, 'key_access'):
                return parent
            owner = getattr(parent, 'window', None)
            if hasattr(owner, 'key_access'):
                return owner
            parent = parent.parent()
        return None

    def reload_managed_keys(self):
        current = self.managed_key.currentData() or ''
        owner = self.key_owner()
        if owner is None:
            return
        try:
            records = owner.key_access.store.metadata()
            self.managed_key.clear()
            self.managed_key.addItem('None — use existing file/agent settings', '')
            for identity, record in records.items():
                self.managed_key.addItem(f"{record['name']} ({'SFTP' if record['kind'] == 'ssh' else 'FTPS'} / {record['algorithm']})", identity)
            self.managed_key.setCurrentIndex(max(0, self.managed_key.findData(current)))
        except (RuntimeError, ValueError, OSError):
            self.managed_key.setToolTip('Key vault unavailable. Open Key Manager for details.')

    def manage_keys(self):
        owner = self.key_owner()
        if owner:
            owner.open_key_manager(self)
            self.reload_managed_keys()

    def reload_cloud_accounts(self):
        owner = self.key_owner()
        if owner is None:
            return
        selected = self.cloud_account.currentData() or ''
        self.cloud_account.clear()
        self.cloud_account.addItem('Select a cloud account', '')
        try:
            for identity, record in owner.cloud_service.accounts().items():
                self.cloud_account.addItem(record['name'] + ' (' + record['provider'] + ')', identity)
            self.cloud_account.setCurrentIndex(max(0, self.cloud_account.findData(selected)))
        except (ValueError, OSError):
            self.cloud_account.setToolTip('Open Cloud Accounts to review the account list.')

    def manage_cloud_accounts(self):
        owner = self.key_owner()
        if owner:
            owner.open_cloud_accounts(self)
            self.reload_cloud_accounts()


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
        if event.button() == Qt.MouseButton.LeftButton and entry and entry.is_dir:
            # Folder activation must not depend on the delegate's checkbox or
            # previous-click state. Do not invoke Qt again after navigation
            # replaces the directory's rows.
            event.accept()
            if not self.pane.busy:
                self.setFocus()
                self.setCurrentItem(row)
                self.pane.open_item(row)
            return
        super().mouseDoubleClickEvent(event)

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
        if not password and not site.cloud_account and not (site.managed_key and site.protocol == PROTOCOLS[3]):
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
        if self.mode == "Remote" and self.site and self.site.protocol != PROTOCOLS[3]:
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
        selected = {id(row) for row in selected_rows}
        current = self.tree.currentItem()
        scroll = self.tree.verticalScrollBar().value()
        with QSignalBlocker(self.tree):
            rows = [self.tree.takeTopLevelItem(0) for _ in range(self.tree.topLevelItemCount())]
            entries = [row.data(0, Qt.ItemDataRole.UserRole) for row in rows]
            mapping = {id(entry): row for entry, row in zip(entries, rows)}
            for entry in sorted_entries(entries, self.window.preferences, self.mode.lower(), self.sort_key, self.sort_descending):
                self.tree.addTopLevelItem(mapping[id(entry)])
            if current: self.tree.setCurrentItem(current)
            for row in rows: row.setSelected(id(row) in selected)
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


class Window(DisplayOptions, ColorOptions, LayoutOptions, MouseActions, ConfirmOptions, FileMenu, Workspaces, QMainWindow, QueueManager):
    ftp_message = Signal(str, int)
    def __init__(self, *, smoke=False):
        super().__init__()
        self._smoke_test = smoke
        self.setWindowTitle("FluxFTP — File transfer workspace")
        self.resize(1600, 900)
        self.preferences = settings_store()
        if smoke:
            from tempfile import TemporaryDirectory
            self._smoke_directory = TemporaryDirectory(prefix='fluxftp-smoke-')
            self.preferences = QSettings(str(Path(self._smoke_directory.name) / 'settings.ini'), QSettings.Format.IniFormat)
        self.directory_cache = DirectoryCache()
        self.ident = IdentService()
        self.app_instance = QApplication.instance()
        self.STYLE = STYLE
        self.workers = set()
        self.pending = []
        self.active = {}
        self.queue_jobs = []
        self.queue_storage_enabled = False
        self.upload_limiter, self.download_limiter = RateLimiter(), RateLimiter()
        self.queue_batch_failed = False
        self.paused = False
        default_data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
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
        try:
            self.sites = json.loads(self.sites_file.read_text("utf-8"))
        except (OSError, ValueError):
            self.sites = {}
        if not isinstance(self.sites, dict):
            self.sites = {}
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
        self.resume.setToolTip("Resume assumes source content has not changed. Downloads use .fluxpart files.")
        self.resume.setChecked(self.preferences.value("Transfer/resume", True, type=bool))
        self.resume.toggled.connect(lambda value: self.preferences.setValue("Transfer/resume", value))
        self.add_workspace()
        self.work_area = QSplitter(Qt.Orientation.Vertical)
        self.workspace_panel = CaptionPanel(self.tabs, 'Window List')
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
        self.browse_timer = QTimer(self)
        self.browse_timer.setInterval(1000)
        self.browse_timer.timeout.connect(self.maintain_browse_sessions)
        self.browse_timer.start()

    def maintain_browse_sessions(self):
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            for side in range(split.count()):
                pane = split.widget(side)
                if pane.mode != 'Remote' or not pane.site:
                    continue
                settings = SiteSettings(self.preferences, pane.site)
                persistent = settings.value('Connection/persistent_browsing', True, type=bool)
                if not persistent or pane.site.single_connection or settings.value('Transfer/single_connection', False, type=bool):
                    if not pane.busy:
                        pane.browse_session.close()
                    continue
                if pane.busy or getattr(pane, '_keepalive_pending', False):
                    continue
                interval = max(5, min(3600, settings.value('Connection/ftp_keepalive_interval', 60, type=int)))
                if settings.value('Connection/ftp_keepalive', True, type=bool) and pane.browse_session.due(interval):
                    pane._keepalive_pending = True
                    worker = self.run(lambda worker, session=pane.browse_session, seconds=interval: session.keepalive(seconds),
                                      lambda _: None, failed=lambda _: None, raw_failure=True, autostart=False)
                    worker.finished.connect(lambda pane=pane: setattr(pane, '_keepalive_pending', False))
                    worker.start()

    def apply_ident_settings(self):
        try:
            self.ident.configure(
                enabled=self.preferences.value("Ident/enabled", False, type=bool),
                only_connecting=self.preferences.value("Ident/only_connecting", True, type=bool),
                name=self.preferences.value("Ident/name", "FluxFTP", type=str),
                system=self.preferences.value("Ident/system", "OTHER", type=str),
                port=self.preferences.value("Ident/port", 113, type=int),
            )
        except (OSError, ValueError) as error:
            for index in range(self.tabs.count()):
                for side in range(2):
                    self.tabs.widget(index).widget(side).browse_session.close()
            self.ident.close()
            self.error(str(error))

    def current_panes(self):
        split = self.tabs.currentWidget()
        return [split.widget(i) for i in range(split.count())] if split else []

    def refresh_skip_colors(self):
        if not hasattr(self, "tabs"):
            return
        options = TransferOptions.from_settings(self.preferences)
        from .highlight_rules import highlight_rules, highlight_color
        try:
            rules = () if self.preferences.value('Display/highlights_disabled', False, type=bool) else highlight_rules(self.preferences.value('Display/highlight_rules', '[]', type=str))
        except ValueError:
            rules = ()
        for tab in range(self.tabs.count()):
            split = self.tabs.widget(tab)
            for index in range(split.count()):
                pane = split.widget(index)
                for row_index in range(pane.tree.topLevelItemCount()):
                    row = pane.tree.topLevelItem(row_index)
                    entry = row.data(0, Qt.ItemDataRole.UserRole)
                    reason = options.skip_reason(entry.name, entry.is_dir, entry.size)
                    color = highlight_color(entry.name, rules)
                    for column in range(row.columnCount()):
                        foreground = options.skip_color if reason else color
                        row.setForeground(column, QBrush(QColor(foreground)) if foreground else QBrush())
                    row.setToolTip(0, f"Skip List: {reason}" if reason else "")

    def new_connection(self, site, password, direction=None):
        from .site_connections import transfer_account, custom_proxy
        if direction:
            site, password = transfer_account(self, site, password, direction)
        preferences = SiteSettings(self.preferences, site)
        transfer_options = TransferOptions.from_settings(preferences)
        from .cloud import CLOUD_PROTOCOLS
        if site.protocol in CLOUD_PROTOCOLS:
            from .cloud_connection import CloudConnection
            rates = tuple(preferences.value('Transfer/' + route + '_kib', 10, type=int) * 1024
                if preferences.value('Transfer/limit_' + route, False, type=bool) else 0
                for route in ('upload', 'download'))
            connection = CloudConnection(site, self.cloud_service, transfer_options, rates)
            trace = connection.trace
            trace.display_side = getattr(self, '_log_site_sides', {}).get(self._transfer_site_key(site), 1)
            trace.listener = lambda message, level: self.ftp_message.emit(('ERROR: ' if level >= 40 else '') + message, trace.display_side)
            return connection
        self.upload_limiter.configure(preferences.value("Transfer/upload_kib", 10, type=int) * 1024
                                      if preferences.value("Transfer/limit_upload", False, type=bool) else 0)
        self.download_limiter.configure(preferences.value("Transfer/download_kib", 10, type=int) * 1024
                                        if preferences.value("Transfer/limit_download", False, type=bool) else 0)
        bind_sockets = preferences.value("Connection/bind_sockets",
            preferences.value("Connection/bind_local_ip", False, type=bool), type=bool)
        local_bind_ip = ""
        if bind_sockets and preferences.value("Connection/bind_local_ip", False, type=bool):
            local_bind_ip = preferences.value("Connection/local_ip", "", type=str).strip()
            if not local_bind_ip:
                raise ValueError("Enter a local IP address, or uncheck 'Bind Local IP address to:' for automatic binding.")
        proxy_enabled = preferences.value("Connection/proxy_enabled", False, type=bool)
        proxy = custom_proxy(self, site)
        if proxy is None and proxy_enabled and not self.proxy_bypassed(site.host):
            proxy_host = preferences.value("Connection/proxy_host", "", type=str).strip()
            if not proxy_host:
                raise ValueError("Proxy is enabled but no proxy host is configured.")
            proxy = {
                "type": preferences.value("Connection/proxy_type", "SOCKS5", type=str),
                "host": proxy_host,
                "port": preferences.value("Connection/proxy_port", 1080, type=int),
                "username": preferences.value("Connection/proxy_username", "", type=str)
                    if preferences.value("Connection/proxy_auth", False, type=bool) else "",
                "password": self.proxy_password,
            }
        connection = Connection(
            site, password,
            identity_loader=self.key_access.resolve,
            ssh_trust=self.ssh_trust,
            connect_timeout=preferences.value("Connection/connect_timeout", 30, type=int),
            command_timeout=preferences.value("Connection/command_timeout", 200, type=int),
            list_mode=preferences.value("Connection/list_mode", "Default (LIST)", type=str),
            list_command=preferences.value("Connection/list_command", "LIST -al", type=str),
            max_retries=preferences.value("Connection/max_retries", 2, type=int),
            retry_delay=preferences.value("Connection/retry_delay", 3, type=int),
            send_feat=preferences.value("Connection/send_feat", True, type=bool),
            smart_pasv=(preferences.value("Connection/smart_pasv", True, type=bool)
                        if site.use_masq is None else site.use_masq),
            proxy=proxy,
            tcp_keepalive=preferences.value("Connection/tcp_keepalive", True, type=bool),
            keepalive_interval=preferences.value("Connection/keepalive_interval", 30, type=int),
            passive_fallback=preferences.value("Connection/passive_fallback", True, type=bool),
            local_bind_ip=local_bind_ip,
            bind_sockets=bind_sockets,
            upnp_enabled=preferences.value("General/upnp_enabled", False, type=bool),
            proxy_force_passive=preferences.value("Connection/proxy_passive", True, type=bool),
            noop_during_transfer=preferences.value("Connection/transfer_noop", False, type=bool),
            transfer_options=transfer_options,
            upload_limiter=self.upload_limiter,
            download_limiter=self.download_limiter,
            local_port_range=self._configured_local_port_range(),
            use_mlsd=preferences.value("Connection/use_mlsd", True, type=bool),
            filename_encoding=preferences.value('Connection/filename_encoding', 'Current site settings', type=str),
            encoding_fallback=preferences.value('Connection/encoding_fallback', 'cp1252', type=str),
            sftp_read_aheads=preferences.value('Connection/sftp_read_aheads', 50, type=int),
            mlsd_essential=preferences.value('Connection/mlsd_essential', False, type=bool),
            ident=self.ident,
        )
        side = getattr(self, '_log_site_sides', {}).get(self._transfer_site_key(site), 1)
        trace = connection.trace
        trace.display_side = side
        trace.listener = lambda message, level: self.ftp_message.emit(('ERROR: ' if level >= 40 else '') + message, trace.display_side)
        connection.bandwidth_manager = getattr(self, 'bandwidth_manager', None)
        return connection

    def _configured_local_port_range(self):
        if not self.preferences.value("Connection/limit_local_ports", False, type=bool):
            return None
        value = self.preferences.value("Connection/local_port_range", "", type=str)
        try:
            first_text, last_text = value.split("-", 1)
            first, last = int(first_text.strip()), int(last_text.strip())
        except (ValueError, AttributeError):
            raise ValueError("Enter the active local port range as two numbers, such as 1400-1500.")
        if not 1 <= first <= last <= 65535:
            raise ValueError("Active local port range must be within 1-65535 and in ascending order.")
        return first, last

    def change_data_folder(self, folder):
        destination = Path(folder).expanduser().resolve()
        current = self.sites_file.parent.resolve()
        if destination == current:
            return True
        try:
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / "sites.json"
            existing = {}
            if target.exists():
                existing = json.loads(target.read_text("utf-8"))
                if not isinstance(existing, dict):
                    raise ValueError("The selected data folder contains an invalid sites.json")
            migrated = {**existing, **self.sites}
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(migrated, indent=2), "utf-8")
            temporary.replace(target)
            self.sites_file = target
            self.sites = migrated
            self.preferences.setValue("General/data_path", str(destination))
            self.preferences.sync()
            self.log.appendPlainText(f"FluxFTP data folder changed to {destination}")
            return True
        except (OSError, ValueError) as error:
            self.error(f"Could not change data folder: {error}")
            return False

    def proxy_bypassed(self, host):
        return bypass_proxy(host,
            self.preferences.value("Connection/proxy_bypass_local", False, type=bool),
            self.preferences.value("Connection/proxy_exceptions", "", type=str))

    def ensure_proxy_credentials(self, parent, host="", site=None):
        if site is not None and site_options(site.site_options).get('Connection/proxy_profile'):
            from .site_connections import custom_proxy
            try:
                custom_proxy(self, site)
                return True
            except ValueError as error:
                self.error(str(error))
                return False
        if site is not None and site_options(site.site_options).get('Connection/proxy_enabled') is False:
            return True
        if host.startswith('cloud-') and len(host) == 38 and all(c in '0123456789abcdef' for c in host[6:]):
            return True
        if host and self.proxy_bypassed(host):
            return True
        if not (self.preferences.value("Connection/proxy_enabled", False, type=bool)
                and self.preferences.value("Connection/proxy_auth", False, type=bool)):
            return True
        if self._proxy_password_entered:
            return True
        password, accepted = QInputDialog.getText(
            parent, "Proxy authentication", "Proxy password (kept in memory until FluxFTP closes):",
            QLineEdit.EchoMode.Password)
        if not accepted:
            return False
        self.proxy_password = password
        self._proxy_password_entered = True
        try:
            self.credential_vault.set(getattr(self, "proxy_credential_id", "proxy"), password)
        except RuntimeError as error:
            QMessageBox.warning(parent, "Proxy password not saved", str(error))
        return True

    def set_activity_logging(self, enabled):
        self._activity_logging = bool(enabled)
        self.preferences.setValue("General/log_to_disk", self._activity_logging)
        if enabled:
            self.log.appendPlainText("Activity log to disk enabled")

    def _write_activity_log(self):
        current = self.log.toPlainText()
        previous = getattr(self, "_activity_log_snapshot", "")
        self._activity_log_snapshot = current
        if not getattr(self, "_activity_logging", False):
            return
        previous_lines = previous.splitlines()
        current_lines = current.splitlines()
        overlap = min(len(previous_lines), len(current_lines))
        while overlap and previous_lines[-overlap:] != current_lines[:overlap]:
            overlap -= 1
        added = current_lines[overlap:]
        if not added:
            return
        try:
            path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs" / "activity.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            from .diagnostics import redact
            with path.open("a", encoding="utf-8") as stream:
                stream.write(redact("\n".join(added)) + "\n")
        except OSError:
            pass

    def apply_selection_color(self, color):
        from .themes import stylesheet, palette_for, apply_application
        apply_application(self.app_instance, self.preferences, self.STYLE)
        if palette_for(self.preferences):
            return
        base = stylesheet(self.preferences, self.STYLE)
        self.app_instance.setStyleSheet(base + f"\nQTreeWidget::item:selected {{ background:{color}; color:white; }}")

    def open_cloud_accounts(self, parent=None):
        from .cloud_accounts import CloudAccounts
        CloudAccounts(self, parent).exec()

    def open_key_manager(self, parent=None):
        from .key_manager import KeyManager
        KeyManager(self, parent).exec()

    def open_preferences(self):
        PreferencesDialog(self).exec()

    def _toolbar_action(self, text, icon, callback, tooltip, shortcut=None):
        action = QAction(self.style().standardIcon(icon), text, self)
        action.setToolTip(tooltip)
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

        help_menu = menus.addMenu("Help")
        self.help_menu = help_menu
        help_menu.setStyleSheet('QMenu::item:disabled { color: #9ba2aa; }')
        from .help_viewer import show_help
        help_index = add(help_menu, 'Help Index', lambda: show_help(self), 'F1')
        help_index.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogHelpButton))
        from .setup_wizard import show_wizard
        self.wizard_action = add(help_menu, 'FluxFTP Wizard…', lambda: show_wizard(self))
        about = self.about_action = add(help_menu, "About FluxFTP…", lambda: QMessageBox.about(
            self, "About FluxFTP", f"FluxFTP {__version__}\n\nFTP, FTPS, SFTP, and FXP file transfers.\nDevelopment build — FTP Rush parity is still in progress."))
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
        self.quick_username = QLineEdit("anonymous")
        self.quick_username.setPlaceholderText("Username")
        labeled("User", self.quick_username, 125)
        self.quick_password = QLineEdit()
        self.quick_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.quick_password.setPlaceholderText("Password")
        labeled("Pass", self.quick_password, 120)
        self.quick_path = QLineEdit("/")
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
            control.setEnabled(index < 5)
        previous_default = SiteDialog._default_port(self._quick_last_protocol_index)
        if not self._quick_port_customized and self.quick_port.value() == previous_default:
            self._quick_updating_port = True
            self.quick_port.setValue(SiteDialog._default_port(index))
            self._quick_updating_port = False
        self._quick_last_protocol_index = index

    def _select_all_in_pane(self, index):
        pane = self._pane(index)
        if pane:
            pane.tree.selectAll()

    def _clear_pane_selections(self):
        for pane in self.current_panes():
            pane.tree.clearSelection()

    def _pane(self, index):
        panes = self.current_panes()
        return panes[index] if 0 <= index < len(panes) else None

    def populate_quick_connect(self, site, password="", target=0):
        self.quick_protocol.setCurrentText(site.protocol)
        self.quick_port.setValue(site.port)
        self.quick_host.setText(site.name if site.cloud_account else site.host)
        self.quick_username.setText(site.username)
        self.quick_password.setText(password)
        self.quick_path.setText(site.root)
        self.quick_target.setCurrentIndex(target)

    def quick_connect(self):
        if self.quick_protocol.currentIndex() >= 5:
            pane = self._pane(self.quick_target.currentIndex())
            if pane and not pane.busy:
                pane.connect_site(protocol=self.quick_protocol.currentText())
            return
        host = self.quick_host.text().strip()
        if not host:
            self.quick_host.setFocus()
            self.statusBar().showMessage("Enter a server host or IP address", 5000)
            return
        target = self.quick_target.currentIndex()
        pane = self._pane(target)
        if pane is None or pane.busy:
            self.statusBar().showMessage("The selected pane is busy", 5000)
            return
        try:
            site = Site(name=host, host=host, port=self.quick_port.value(),
                        protocol=self.quick_protocol.currentText(), username=self.quick_username.text().strip(),
                        root=self.quick_path.text().strip() or "/",
                        passive=self.preferences.value("Connection/default_passive", True, type=bool))
            validate_site(site)
        except ValueError as error:
            self.error(str(error))
            return
        if site.protocol == PROTOCOLS[4] and self.preferences.value("Confirm/unencrypted", True, type=bool):
            if QMessageBox.question(self, "Unencrypted connection",
                    "Plain FTP sends your password and files without encryption. Continue?") != QMessageBox.StandardButton.Yes:
                return
        if not self.ensure_proxy_credentials(pane, site.host, site):
            return
        if self.preferences.value('Misc/quick_new_tab', False, type=bool):
            self.add_workspace()
            pane = self._pane(target)
        pane.set_remote(site, self.quick_password.text(), site.root)
        self.log.appendPlainText(f"Quick connecting {site.protocol} to {site.host}:{site.port} in {self.quick_target.currentText()}")
        pane.refresh()

    def refresh_focused_pane(self):
        focus = QApplication.focusWidget()
        for pane in self.current_panes():
            if focus is pane or (focus is not None and pane.isAncestorOf(focus)):
                pane.refresh()
                return
        pane = self.mark_commands.pane()
        if pane:
            pane.refresh()

    def swap_remote_servers(self):
        panes = self.current_panes()
        if len(panes) != 2 or any(pane.mode != "Remote" or pane.site is None for pane in panes):
            self.statusBar().showMessage("Connect both panes in Remote mode before switching servers", 5000)
            return
        left, right = panes
        left_state = (left.site, left.password, left.path)
        right_state = (right.site, right.password, right.path)
        left.set_remote(*right_state)
        right.set_remote(*left_state)
        left.refresh()
        right.refresh()
        self.statusBar().showMessage("Remote servers switched between panes", 4000)

    def compare_contents(self):
        self.compare_commands.compare()

    def refresh_pane(self, pane_index):
        split = self.tabs.currentWidget()
        if split and pane_index < split.count():
            split.widget(pane_index).refresh()

    def add_workspace(self):
        if self.tabs.count() >= 100:
            self.statusBar().showMessage('Close a workspace before opening another (100-workspace limit).', 5000)
            return
        modes = [pane.mode for pane in self.current_panes()] if hasattr(self, 'tabs') else []
        if len(modes) != 2 or self.preferences.value('Misc/new_tab_remote', False, type=bool):
            modes = ['Remote', 'Remote']
        orientation = Qt.Orientation.Horizontal if self.preferences.value("Display/pane_layout", "Side by side", type=str) == "Side by side" else Qt.Orientation.Vertical
        splitter = QSplitter(orientation)
        splitter.addWidget(Pane(self, "1", modes[0]))
        splitter.addWidget(Pane(self, "2", modes[1]))
        if hasattr(self, 'mouse_filter'):
            for index in range(splitter.count()):
                self.install_pane_mouse(splitter.widget(index))
        self.tabs.setCurrentIndex(self.tabs.addTab(splitter, self.workspace_name()))
        if getattr(self, '_panel_positions_ready', False):
            self.initialize_pane_positions(splitter)
        self.apply_display_settings()
        if hasattr(self, "tray"): self.apply_fonts()
        if hasattr(self, 'layout_lock'): self.layout_lock.apply()
        self.save_workspaces()

    def close_tab(self, index):
        pane = self.tabs.widget(index)
        if pane is None or self.tabs.count() <= 1:
            return
        jobs = [job for job in self.queue_jobs if job['workspace'] is pane and job.get('state') in ('queued', 'active', 'failed')]
        if self.workspace_busy(pane):
            self.statusBar().showMessage('Wait for active work and close queue editors before closing this workspace.')
            return
        if jobs and not self.confirm_action('close_queue', 'Close workspace with queue files',
                f'Close this workspace and discard its {len(jobs)} unfinished queue item(s)? Transferred files will be kept.'):
            return
        if any(pane.widget(i).site or pane.widget(i).remote_site for i in range(2)) and not self.confirm_action(
                'close_connected', 'Close connected workspace', 'Close this workspace and disconnect its sites?'):
            return
        if any(pane.widget(i).busy for i in range(2)) or any(record['workspace'] is pane for record in self.active.values()):
            self.statusBar().showMessage('New work started; workspace close cancelled.')
            return
        self.discard_queue_jobs(jobs)
        for i in range(2):
            pane.widget(i).browse_session.close()
        self.tabs.removeTab(index)
        pane.deleteLater()
        self.save_queue_state()
        self.apply_layout_settings()
        self.refresh_workspace_names()
        self.save_panel_layout()
        self.save_workspaces()
        return True

    def manage_sites(self):
        dialog = SitesDialog(self)
        if dialog.exec() and dialog.connection:
            index, name = dialog.connection
            self.tabs.currentWidget().widget(index).connect_saved_site(name)

    def save_sites(self, proposed=None):
        proposed = self.sites if proposed is None else proposed
        try:
            old_ids = {data.get("_credential_id") for data in self.sites.values() if data.get("_credential_id")}
            proposed = {name: dict(data) for name, data in proposed.items()}
            for data in proposed.values():
                data.setdefault("_credential_id", uuid.uuid4().hex)
            self.sites_file.parent.mkdir(parents=True, exist_ok=True)
            if self.preferences.value("General/backup_profiles", True, type=bool) and self.sites_file.exists():
                from datetime import date
                import shutil
                backup_dir = self.sites_file.parent / "backup"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_file = backup_dir / f"sites-{date.today():%Y%m%d}.json"
                if not backup_file.exists():
                    shutil.copy2(self.sites_file, backup_file)
                    backups = sorted(backup_dir.glob("sites-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                    for stale in backups[4:]:
                        stale.unlink()
            temp = self.sites_file.with_suffix(".tmp")
            temp.write_text(json.dumps(proposed, indent=2), "utf-8")
            temp.replace(self.sites_file)
            self.sites = proposed
            new_ids = {data.get("_credential_id") for data in proposed.values() if data.get("_credential_id")}
            for credential_id in old_ids - new_ids:
                try:
                    self.credential_vault.set(credential_id, "")
                    credentials.delete_legacy_password(credential_id)
                except RuntimeError:
                    pass
            return True
        except OSError as e:
            self.error(f"Could not save sites: {e}")
            return False

    @staticmethod
    def site_from_profile(data):
        from dataclasses import fields
        allowed = {field.name for field in fields(Site)}
        return Site(**{key: value for key, value in data.items() if key in allowed})

    def get_site_password(self, profile):
        credential_id = profile.get("_credential_id", "")
        if not credential_id:
            return ""
        password = self.credential_vault.get(credential_id)
        if password:
            return password
        return self.credential_vault.migrate_legacy_keyring_secret(credential_id)

    def set_proxy_password(self, password):
        self.proxy_password = password
        self._proxy_password_entered = True
        self.credential_vault.set(getattr(self, "proxy_credential_id", "proxy"), password)

    def set_site_password(self, site_name, password):
        profile = self.sites.get(site_name)
        if not profile:
            return
        credential_id = profile.get("_credential_id")
        if not credential_id:
            profile["_credential_id"] = credential_id = uuid.uuid4().hex
            if not self.save_sites(self.sites):
                raise RuntimeError("Could not save the site profile's secure credential reference.")
        self.credential_vault.set(credential_id, password)
        credentials.delete_legacy_password(credential_id)

    def error(self, message):
        self.log.appendPlainText(message)
        QMessageBox.warning(self, "Operation failed", message)

    def sfv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Verify SFV manifest", "", "SFV files (*.sfv)")
        if path:
            def done(results):
                text = "\n".join(f"{state}: {name}" for name, state in results) or "No entries in manifest"
                self.log.appendPlainText(text)
                QMessageBox.information(self, "SFV results", text[:4000])
            self.run(lambda worker: verify_sfv(path), done)

    def run(self, function, result, failed=None, raw_failure=False, autostart=True):
        failure_handler = failed or self.error
        worker = Worker(function, self)
        self.workers.add(worker)
        worker.result.connect(result)
        worker.failed.connect(failure_handler if raw_failure else lambda error: self._handle_worker_failure(error, function, result, failure_handler))
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.finished.connect(worker.deleteLater)
        if autostart:
            worker.start()
        return worker

    def _handle_worker_failure(self, error, function, result, failure_handler):
        if not isinstance(error, CertificateTrustRequired):
            failure_handler(f"Cancelled: {error}" if isinstance(error, Cancelled) else str(error))
            return
        if not self._trust_server_certificate(error):
            failure_handler("Connection canceled because the server certificate was not trusted.")
            return
        self.run(function, result, failure_handler)

    def _trust_server_certificate(self, error):
        site = error.site
        cert = error.certificate
        if cert is None:
            QMessageBox.warning(self, "Certificate unavailable", "FluxFTP could not read the certificate presented by the server.")
            return False
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            names = ", ".join(san.get_values_for_type(x509.DNSName) +
                               [str(value) for value in san.get_values_for_type(x509.IPAddress)]) or "none"
        except Exception:
            names = "none"
        fingerprint = ":".join(error.fingerprint[i:i + 2] for i in range(0, len(error.fingerprint), 2)).upper()
        not_before = cert.not_valid_before_utc.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        not_after = cert.not_valid_after_utc.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        details = (
            f"Site: {site.name}\nServer: {normalize_host(site.host)}:{site.port}\nProtocol: {site.protocol}\n"
            f"Verification failure: {error.verify_message}\nSubject: {cert.subject.rfc4514_string()}\n"
            f"Issuer: {cert.issuer.rfc4514_string()}\nSubject alternative names: {names}\n"
            f"Valid from: {not_before}\nValid until: {not_after}\nSHA-256 fingerprint: {fingerprint}"
        )
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Icon.Warning)
        prompt.setWindowTitle("Untrusted server certificate")
        prompt.setText("The server presented a certificate that FluxFTP does not currently trust.")
        prompt.setInformativeText(
            "Review the certificate details. Trusting it saves this exact certificate's SHA-256 fingerprint "
            "for this site and requires that same certificate on future connections. Verify the fingerprint "
            "with the server administrator through a separate trusted channel before accepting."
            + ("\n\nPreviously trusted fingerprint: " + error.previous_fingerprint
               if error.previous_fingerprint else "")
        )
        prompt.setDetailedText(details)
        trust = prompt.addButton("Trust certificate and connect", QMessageBox.ButtonRole.AcceptRole)
        prompt.addButton("Reject", QMessageBox.ButtonRole.RejectRole)
        now = datetime.now(timezone.utc)
        certificate_is_current = cert.not_valid_before_utc <= now <= cert.not_valid_after_utc
        if not certificate_is_current:
            trust.setEnabled(False)
            prompt.setInformativeText(
                prompt.informativeText() + "\n\nThis certificate is outside its validity period and cannot be trusted."
            )
        prompt.exec()
        if prompt.clickedButton() is not trust:
            return False
        site.tls_cert_sha256 = error.fingerprint
        site.tls_pin_host = normalize_host(site.host)
        site.tls_pin_port = site.port
        site.tls_pin_protocol = site.protocol
        proposed = dict(self.sites)
        profile = asdict(site)
        previous = proposed.get(site.name, {})
        if previous.get("_credential_id"):
            profile["_credential_id"] = previous["_credential_id"]
        proposed[site.name] = profile
        return self.save_sites(proposed)

    def enqueue(self, right):
        split = self.tabs.currentWidget()
        source, dest = (split.widget(0), split.widget(1)) if right else (split.widget(1), split.widget(0))
        self.enqueue_items(source, dest, source.selected())

    def enqueue_local_paths(self, dest, paths, start=None):
        entries, sources = [], {}
        try:
            for path in dict.fromkeys(paths):
                item = Path(path)
                if not item.is_absolute() or item.is_symlink() or not (item.is_file() or item.is_dir()):
                    raise ValueError("Drop existing local files or folders; symbolic links are not supported.")
                safe_name(item.name)
                if item.name in sources:
                    raise ValueError("Dropped items must have distinct names in the destination folder.")
                sources[item.name] = str(item)
                entries.append(Entry(item.name, item.is_dir(), item.stat().st_size))
        except (OSError, ValueError) as error:
            self.error(str(error))
            return
        source = SimpleNamespace(mode="Local", site=None, password="", busy=False, join=sources.__getitem__)
        self.enqueue_items(source, dest, entries, start=start)

    def enqueue_items(self, source, dest, entries, start=None):
        split = dest.parentWidget()
        if source.busy or dest.busy or not entries:
            return
        if dest.mode == 'Remote' and dest.site:
            self.directory_cache.clear(dest.site)
        source_remote, dest_remote = source.mode == "Remote", dest.mode == "Remote"
        route = "FXP" if source_remote and dest_remote else "Upload" if dest_remote else "Download" if source_remote else "Copy"
        try:
            options = transfer_settings(self.preferences, source.site if source_remote else None, dest.site if dest_remote else None)
            names = [options.filename(entry.name, route) for entry in entries]
            compared = [name.casefold() for name in names] if not dest_remote and os.name == "nt" else names
            if len(compared) != len(set(compared)):
                raise ValueError("Filename conversion would overwrite another selected file.")
        except ValueError as error:
            self.error(str(error))
            return
        if source_remote and source.site is None:
            self.error("Connect the source remote pane before transferring files.")
            return
        if dest_remote and dest.site is None:
            self.error("Connect the destination remote pane before transferring files.")
            return
        if any(e.is_link for e in entries):
            self.error("Symbolic links are not transferred in this build.")
            return
        if self.preferences.value("Confirm/queue", True, type=bool) and QMessageBox.question(self, "Queue transfers", f"Transfer {len(entries)} file(s)? Existing destination files may be overwritten. Resume assumes unchanged source files.") != QMessageBox.StandardButton.Yes:
            return
        if not self.active and not self.pending:
            self.queue_batch_failed = False
            for previous_job in self.queue_jobs:
                previous_job['batch_active'] = False
            if start is not False:
                self.reset_failed_jobs(automatic=True)
        from .queue_buffer import DiskBuffer, BUFFER_LIMIT
        large_selection = len(entries) + len(self.pending) > BUFFER_LIMIT
        selection_buffer = DiskBuffer(rank=lambda item: options.priority(item['name'])) if large_selection else None
        buffer_parent = None
        for entry, target_name in zip(entries, names):
            try:
                src, dst = source.join(entry.name), dest.join(target_name)
            except ValueError as e:
                self.error(str(e))
                continue
            src_site = source.site if source_remote else None
            dst_site = dest.site if dest_remote else None
            if src_site and dst_site and (src_site.host.casefold(), src_site.port, src_site.username) == (dst_site.host.casefold(), dst_site.port, dst_site.username) and (src == dst or (entry.is_dir and dst.startswith(src.rstrip("/") + "/"))):
                self.error("Source and destination overlap on the same server.")
                continue
            if not source_remote and not dest_remote:
                source_path, destination_path = Path(src).resolve(), Path(dst).resolve()
                if source_path == destination_path or (entry.is_dir and destination_path.is_relative_to(source_path)):
                    self.error("The local destination cannot be the source itself or a folder inside the source.")
                    continue
            src_password, dst_password = source.password, dest.password
            resume = self.resume.isChecked()
            route = "FXP" if source_remote and dest_remote else "Upload" if dest_remote else "Download" if source_remote else "Copy"
            if route == 'FXP' and (src_site.cloud_account or dst_site.cloud_account or src_site.protocol == PROTOCOLS[3] or dst_site.protocol == PROTOCOLS[3]):
                route = 'Relay'
            row = QTreeWidgetItem([entry.name, "0%", "—", "—", self.file_size(entry.size), src, dst, f"{route} · Queued"])
            row.setData(4, Qt.ItemDataRole.UserRole, entry.size)
            if selection_buffer is None or entry.is_dir:
                self.queue.addTopLevelItem(row)
            job = {
                "row": row, "route": route, "workspace": split,
                "source_site": src_site, "destination_site": dst_site,
                "source_password": src_password, "destination_password": dst_password,
                "source_side": int(getattr(source, "number", 1)), "destination_side": int(getattr(dest, "number", 2)),
                "src": src, "dst": dst, "directory": entry.is_dir, "resume": resume,
                "name": entry.name, "options": options, "state": "queued", "retries": 0, "held": start is False,
            }
            if selection_buffer is not None and not entry.is_dir:
                selection_buffer.append(dict(src=src, dst=dst, name=entry.name, size=entry.size,
                    directory=False, resume=resume, state='queued', retries=0, held=start is False,
                    source_site=asdict(src_site) if src_site else None,
                    destination_site=asdict(dst_site) if dst_site else None,
                    workspace=self.tabs.indexOf(split), source_side=job['source_side'], destination_side=job['destination_side']))
                if buffer_parent is None:
                    buffer_parent = job
                    buffer_parent.update(name='Selected files', src=source.path, dst=dest.path)
                    row.setText(0, 'Selected files')
                    row.setText(5, source.path)
                    row.setText(6, dest.path)
                    self.queue.addTopLevelItem(row)
                    self.queue_jobs.append(buffer_parent)
                continue
            job["task"] = self.make_queue_task(job)
            self.queue_jobs.append(job)
            self.pending.append(job)
            if start is False:
                row.setText(7, f'{route} · Held — press Play')
        if buffer_parent is not None:
            self.install_queue_buffer(buffer_parent, selection_buffer)
        elif selection_buffer is not None:
            selection_buffer.close()
        self.save_queue_state()
        if start is True:
            self.paused = False
        self.show_transfer_queue()
        self.start_next()

    @staticmethod
    def _transfer_site_key(site):
        if site is None:
            return None
        return (site.host.casefold(), site.port, site.username.casefold(), site.protocol)

    def _transfer_record(self, job):
        source = job["source_site"]
        destination = job["destination_site"]
        sites = {key: site for site in (source, destination)
                 if site is not None for key in (self._transfer_site_key(site),)}
        keys = set(sites)
        route = job["route"]
        download_keys = {self._transfer_site_key(source)} if route in ("Download", "FXP", "Relay") and source else set()
        upload_keys = {self._transfer_site_key(destination)} if route in ("Upload", "FXP", "Relay") and destination else set()
        return {
            "row": job["row"], "task": job["task"], "route": route,
            "workspace": job["workspace"], "site_keys": keys,
            "download_keys": download_keys, "upload_keys": upload_keys,
            "single_keys": {self._transfer_site_key(site) for site in sites.values() if site.single_connection},
            "site_total_limits": {key: site.max_transfers_total for key, site in sites.items()},
            "download_limits": {key: site.max_downloads for key, site in sites.items()},
            "upload_limits": {key: site.max_uploads for key, site in sites.items()},
            "max_total": min((site.max_transfers_total for site in sites.values()), default=6),
        }

    def _can_start_transfer(self, job):
        if job.get('buffered'):
            return False
        if job.get('scheduled_at') is not None and job['scheduled_at'] > time.time():
            return False
        if job.get('held') or job.get('editing'):
            return False
        if job.get('dst'):
            remote = job.get('destination_site')
            target = posixpath.normpath(job['dst']) if remote else os.path.normcase(os.path.abspath(job['dst']))
            separator = '/' if remote else os.sep
            for record in self.active.values():
                other = record.get('job', {})
                if not other.get('dst') or self._transfer_site_key(remote) != self._transfer_site_key(other.get('destination_site')):
                    continue
                other_target = posixpath.normpath(other['dst']) if remote else os.path.normcase(os.path.abspath(other['dst']))
                if (target == other_target or
                    (job.get('directory') and other_target.startswith(target.rstrip(separator) + separator)) or
                    (other.get('directory') and target.startswith(other_target.rstrip(separator) + separator))):
                    return False
        if self.preferences.value("Transfer/single_connection", False, type=bool) and self.active:
            return False
        candidate = self._transfer_record(job)
        active = [record for record in self.active.values() if record["workspace"] is candidate["workspace"]]
        if len(active) >= self.preferences.value("Transfer/per_tab", 10, type=int):
            return False
        total_limit = min([candidate["max_total"], *(record["max_total"] for record in active)])
        if len(active) >= total_limit:
            return False
        for record in active:
            if (candidate["single_keys"] | record["single_keys"]) & (candidate["site_keys"] & record["site_keys"]):
                return False
        for key, limit in candidate["site_total_limits"].items():
            if sum(key in record["site_keys"] for record in active) >= limit:
                return False
        for key, limit in candidate["download_limits"].items():
            if sum(key in record["download_keys"] for record in active) >= limit:
                return False
        for key, limit in candidate["upload_limits"].items():
            if sum(key in record["upload_keys"] for record in active) >= limit:
                return False
        return True

    def start_next(self):
        if getattr(self, '_queue_operation_depth', 0) or getattr(self, '_queue_commit', False): return
        self.refill_queue_buffers()
        if self.pending:
            self.directory_cache.clear()
        if self.paused:
            return
        while self.pending:
            if any(record.get('job', {}).get('deployment') for record in self.active.values()): return
            rules = self.preferences.value('Transfer/priority_rules', DEFAULT_RULES, type=str)
            job_index = min((index for index, job in enumerate(self.pending) if self._can_start_transfer(job) and not (job.get('deployment') and self.active)),
                            key=lambda index: self.queue_commands.schedule_key(self.pending[index], getattr(self.pending[index].get('options'), 'priority_rules', rules)),
                            default=None)
            if job_index is None:
                return
            job = self.pending.pop(job_index)
            if job.get('needs_credentials'):
                self.pending.insert(job_index, job)
                self.paused = True
                self.statusBar().showMessage('Restored queue needs credentials. Press Play to continue.')
                return
            if job not in self.queue_jobs:
                self.queue_jobs.append(job)
            job['state'] = 'active'
            job.pop('transfer_outcome', None)
            job.pop('actual_dst', None)
            job['transfer_started'] = time.monotonic()
            job['row'].setData(0, STATE_ROLE, 'active')
            job['batch_active'] = True
            record = self._transfer_record(job)
            record['job'] = job
            row, task, route = record["row"], record["task"], record["route"]
            row.setText(7, f"{route} · " + ('Opening folder…' if job.get('streaming_folder') else 'Scanning folder…' if job.get('directory') else 'Transferring'))
            if job.get('streaming_folder'):
                from .queue_tree import PROGRESS_ROLE, STREAM_ROLE
                row.setData(0, STREAM_ROLE, True)
                row.setData(3, Qt.ItemDataRole.UserRole, [0, 0])
                row.setText(3, '0 B')
                row.setText(4, '—')
                row.setToolTip(3, 'Cumulative bytes uploaded across files in this folder during this attempt.')
                row.setToolTip(4, 'Folder size is not pre-scanned.')
                row.setData(1, PROGRESS_ROLE, 0)
                row.setToolTip(1, 'Current file progress; the full folder size is not calculated before uploading.')
            row.setToolTip(7, '')
            if self.preferences.value('Queue/show_paths', False, type=bool):
                self.log.appendPlainText(f"{route}: {row.text(5)} -> {row.text(6)}")
            def done(result, target=row, transfer_route=route, current=job):
                if isinstance(result, dict) and result.get('paused'):
                    from .queue_tree import PROGRESS_ROLE
                    current['state'] = 'queued'
                    target.setText(7, f'{transfer_route} · Paused before next file')
                    target.setData(1, PROGRESS_ROLE, None)
                    target.setText(1, 'Paused')
                    target.setText(2, '0 B/s')
                    return
                if isinstance(result, dict) and result.get('planned') is not None and current.get('suppress_retry'):
                    close = getattr(result['planned'], 'close', None)
                    if close: close()
                    current['state'] = 'cancelled'
                    target.setText(7, f'{transfer_route} · Cancelled')
                    return
                current['state'] = 'complete'
                if isinstance(result, dict):
                    if result.get('planned') is not None:
                        if result.get('transferred') is False:
                            close = getattr(result['planned'], 'close', None)
                            if close: close()
                        else:
                            self.expand_queue_files(current, result['planned'])
                    for message in result.get('messages', []): self.log.appendPlainText(message)
                    if result.get('messages'): target.setToolTip(7, '\n'.join(result['messages']))
                    if result.get('crc_errors'):
                        current['transfer_outcome'] = 'failed'
                        self.register_crc_failures(current, result['crc_errors'])
                        if not current.get('crc_job'):
                            target.setText(7, f'{transfer_route} · Finished with CRC failures')
                        return
                for old, new in result.get("renamed", []) if isinstance(result, dict) else []:
                    self.log.appendPlainText(f"Auto Rename: {old} -> {new}")
                    if target.text(6) == old:
                        current['actual_dst'] = new
                        target.setText(6, new)
                skipped_items = result.get("skipped", []) if isinstance(result, dict) else []
                skipped_directories = result.get('skipped_directories', set()) if isinstance(result, dict) else set()
                skipped_root = isinstance(result, dict) and result.get("transferred") is False
                color = QBrush(QColor(self.preferences.value("Transfer/skip_color", "#ff0000", type=str)))
                if skipped_root:
                    current['transfer_outcome'] = 'skipped'
                    target.setText(7, f"{transfer_route} · Skipped")
                    target.setText(1, "—")
                    target.setToolTip(7, skipped_items[0][1] if skipped_items else "Skip List")
                    for column in range(target.columnCount()):
                        target.setForeground(column, color)
                for path, reason in skipped_items:
                    if current.get('directory') and not skipped_root:
                        self.transfer_history.skipped(current, path, reason, path in skipped_directories)
                    self.log.appendPlainText(f"Skipped: {path} ({reason})")
                    if current.get('directory'):
                        append_transfer_log(self, f"[{current.get('source_side', 1)}] Skip [{path}] {reason}")
                    if not skipped_root and path not in skipped_directories:
                        skipped_row = QTreeWidgetItem([Path(path).name, "—", "—", "—", "—", path, "", f"{transfer_route} · Skipped"])
                        skipped_row.setToolTip(7, reason)
                        for column in range(skipped_row.columnCount()):
                            skipped_row.setForeground(column, color)
                        self.queue.addTopLevelItem(skipped_row)
                if skipped_root:
                    return
                if isinstance(result, dict) and result.get('planned') is not None:
                    return
                target.setText(1, "100%")
                if current.get('streaming_folder'):
                    from .queue_tree import PROGRESS_ROLE
                    target.setData(1, PROGRESS_ROLE, 100)
                current['transfer_outcome'] = 'complete'
                total = target.data(4, Qt.ItemDataRole.UserRole) or 0
                if not current.get('streaming_folder'):
                    target.setData(3, Qt.ItemDataRole.UserRole, [total, total])
                else:
                    target.setText(2, '0 B/s')
                target.setText(7, f"{transfer_route} · Complete")
                self.log.appendPlainText(f"Complete: {target.text(0)} ({transfer_route})")
            def failed(message, target=row, transfer_route=route, current=job):
                self.queue_batch_failed = True
                current['state'] = 'cancelled' if 'Cancelled:' in message else 'failed'
                current['transfer_outcome'] = current['state']
                target.setText(7, f"{transfer_route} · " + ("Cancelled" if "Cancelled:" in message else "Failed"))
                target.setToolTip(7, message)
                self.log.appendPlainText(f"{target.text(0)}: {message}")
            transfer_id = uuid.uuid4().hex
            def queue_error(error, current=job, current_record=record, fail=failed, job_id=transfer_id):
                from .folder_sync import PreviewChanged
                from .transfer_reliability import ReliabilityError
                if isinstance(error, (PreviewChanged, ReliabilityError, Cancelled)): current['suppress_retry'] = True
                current_record['handling_failure'] = True
                try:
                    if isinstance(error, CertificateTrustRequired):
                        if self._trust_server_certificate(error) and not current.get('suppress_retry'):
                            current['state'] = 'queued'
                        else:
                            fail('Cancelled: Server certificate was not trusted.')
                    else:
                        fail(f'Cancelled: {error}' if isinstance(error, Cancelled) else str(error))
                finally:
                    current_record['handling_failure'] = False
                    if current_record.get('finish_deferred'):
                        QTimer.singleShot(0, lambda: self.transfer_finished(job_id))
            worker = self.run(task, done, queue_error, raw_failure=True, autostart=False)
            def current_file(src, dst, target=row, transfer_route=route):
                from .queue_tree import PROGRESS_ROLE
                target.setText(7, f'{transfer_route} · {Path(src).name}')
                target.setToolTip(7, f'{src}\n→ {dst}\nProgress is for the current file; folder total is not pre-scanned.')
                target.setText(1, '0%')
                target.setData(1, PROGRESS_ROLE, 0)
            worker.file_started.connect(current_file)
            record["worker"] = worker
            self.active[transfer_id] = record
            worker.progress.connect(lambda percent, rate, done, total, target=row:
                self.transfer_display_progress(target, percent, rate, done, total))
            worker.finished.connect(lambda job_id=transfer_id: self.transfer_finished(job_id))
            self.schedule_queue_save()
            worker.start()

    def transfer_finished(self, transfer_id):
        self.directory_cache.clear()
        if self.active.get(transfer_id, {}).get('handling_failure'):
            self.active[transfer_id]['finish_deferred'] = True
            return
        record = self.active.pop(transfer_id, None)
        if record is None:
            return
        job = record.get('job') if record else None
        if job:
            self.transfer_history.record(job, elapsed=time.monotonic() - record['worker'].started_at)
        if job and job.get('state') == 'queued' and job not in self.pending:
            self.pending.append(job)
        if job and job.get('state') == 'failed' and self.preferences.value('Queue/cycle_retry', True, type=bool):
            self.retry_job(job)
        if not self.active and not self.pending and self.preferences.value('Queue/retry_after', True, type=bool):
            for failed_job in self.queue_jobs:
                if failed_job.get('state') == 'failed':
                    self.retry_job(failed_job)
        self.queue_batch_failed = any(j.get('batch_active', True) and j.get('state') in ('failed', 'cancelled') for j in self.queue_jobs)
        if job:
            report_transfer(self, job, record['worker'])
            self.clear_skipped_folder(job)
            self.retire_buffered_success(job)
            self.clear_finished_download(job)
        self.refill_queue_buffers()
        self.schedule_queue_save()
        self.start_next()
        if not self.active and not self.pending:
            self.conflicts.reset()
            if not self.queue_batch_failed and self.display_value("flash_completed", True):
                QApplication.alert(self, 0)
            if self.preferences.value("Misc/beep", False, type=bool):
                QApplication.beep()
            action = self.preferences.value("Transfer/completed", "Idle", type=str)
            if not self.queue_batch_failed and action in ("Disconnect", "Close FluxFTP"):
                QTimer.singleShot(100, self._apply_completion_action)
                return
            for tab_index in range(self.tabs.count()):
                split = self.tabs.widget(tab_index)
                for pane_index in range(split.count()):
                    pane = split.widget(pane_index)
                    if not pane.busy:
                        pane.refresh()

    def _apply_completion_action(self):
        action = self.preferences.value("Transfer/completed", "Idle", type=str)
        if self.active or self.pending or self.queue_batch_failed or action == "Idle":
            return
        if self.workers:
            QTimer.singleShot(100, self._apply_completion_action)
        elif action == "Close FluxFTP":
            self.close()
        elif action == "Disconnect":
            for tab_index in range(self.tabs.count()):
                split = self.tabs.widget(tab_index)
                for pane_index in range(split.count()):
                    pane = split.widget(pane_index)
                    if pane.mode == "Remote":
                        pane.browse_session.close()
                        pane.site = pane.remote_site = None
                        pane.password = pane.remote_password = ""
                        pane.tree.clear()
                        pane.title_label.setText(f"({pane.number}) Remote")
                        pane.status.setText("Disconnected")

    def play_queue(self):
        if not self.prepare_restored_jobs():
            return
        self.reset_failed_jobs()
        for job in self.pending:
            job['held'] = False
            self.release_queue_buffer(job)
        self.paused = False
        self.statusBar().showMessage("Transfer queue running · parallel transfers enabled")
        self.start_next()
        self.save_queue_state()

    def stop_queue(self):
        for record in self.active.values():
            if 'job' in record: record['job']['suppress_retry'] = True
            record['worker'].cancel.set()
        self.transfer_history.cancelled(self.pending)
        if self.pending or self.active:
            self.queue_batch_failed = True
        for job in self.pending:
            job['state'] = 'cancelled'
            job['row'].setData(0, STATE_ROLE, 'cancelled')
            update_group(self, job['row'])
            job['suppress_retry'] = True
            job["row"].setText(7, f"{job['route']} · Cancelled")
        self.pending.clear()
        for record in list(self.active.values()):
            if 'job' in record:
                record['job']['suppress_retry'] = True
            record["worker"].cancel.set()
            record["row"].setText(7, f"{record['route']} · Cancelling…")
        if self.active:
            self.statusBar().showMessage("Stopping active transfers and clearing the queue")
        else:
            self.statusBar().showMessage("Transfer queue stopped")
        for job in self.queue_jobs:
            if job.get('state') == 'failed': job['suppress_retry'] = True
        self.save_queue_state()

    def toggle_pause(self):
        if self.paused and not self.prepare_restored_jobs():
            return
        self.paused = not self.paused
        self.statusBar().showMessage("Queue paused; current transfer will finish" if self.paused else "Queue running")
        self.start_next()
        self.save_queue_state()

    def cancel_selected(self):
        rows = selected_rows(self.queue)
        for record in self.active.values():
            if record['row'] in rows:
                if 'job' in record: record['job']['suppress_retry'] = True
                record['worker'].cancel.set()
        self.transfer_history.cancelled(job for job in self.pending if job['row'] in rows)
        if rows:
            self.queue_batch_failed = True
        for job in self.pending:
            if job["row"] in rows:
                job['state'] = 'cancelled'
                job['row'].setData(0, STATE_ROLE, 'cancelled')
                update_group(self, job['row'])
                job['suppress_retry'] = True
                job["row"].setText(7, f"{job['route']} · Cancelled")
        self.pending = [job for job in self.pending if job["row"] not in rows]
        for record in self.active.values():
            if record["row"] not in rows:
                continue
            if 'job' in record:
                record['job']['suppress_retry'] = True
            record["worker"].cancel.set()
            record["row"].setText(7, f"{record['route']} · Cancelling…")
        for job in self.queue_jobs:
            if job['row'] in rows and job.get('state') == 'failed': job['state'] = 'cancelled'
        self.save_queue_state()

    def closeEvent(self, event):
        if getattr(self, '_queue_operation_depth', 0) or getattr(self, '_queue_commit', False):
            event.ignore()
            return
        saved = self.save_queue_state()
        if self.workers or (self.pending and not ((self.paused or all(j.get('held') for j in self.pending)) and saved)):
            QMessageBox.information(self, "Work in progress", "Cancel queued transfers and wait for active operations to finish before closing.")
            event.ignore()
        else:
            if not (getattr(self, '_smoke_test', False) or getattr(self, '_configuration_restored', False)) and not self.confirm_action('exit', 'Exit FluxFTP', 'Exit FluxFTP?'):
                event.ignore()
                return
            if self.workers or self.active:
                event.ignore()
                return
            for dialog in list(getattr(self, 'mouse_windows', [])):
                if not dialog.close():
                    event.ignore()
                    return
            self.save_panel_layout()
            self.save_workspaces()
            self.docking.shutdown()
            for index in range(self.tabs.count()):
                for side in range(2):
                    self.tabs.widget(index).widget(side).browse_session.close()
            self.ident.close()
            self.cloud_service.close()
            self.automation.shutdown()
            self.folder_monitor.shutdown()
            self.watch_uploads.shutdown()
            self.bandwidth_control.timer.stop()
            self.browse_timer.stop()
            self.system_appearance.close()
            self.tray_timer.stop()
            self.download_display_timer.stop()
            self.tray.hide()
            self.window_positions.close()
            event.accept()


def main():
    parser = argparse.ArgumentParser(description="FluxFTP desktop file transfer client")
    parser.add_argument("--debug", action="store_true", help="Write connection diagnostics to a text log")
    parser.add_argument("--log-file", type=Path, help="Debug log destination (requires --debug)")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument('--automation-once', action='store_true', help='Run due saved automation without opening the desktop')
    args, qt_args = parser.parse_known_args()
    args.smoke_test = args.smoke_test or args.runtime_smoke_test
    if args.log_file and not args.debug:
        parser.error("--log-file requires --debug")
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("FluxFTP")
    app.setOrganizationName("FluxFTP")
    if args.automation_once:
        from .background_scheduler import run_once
        from .openssl_runtime import ready
        ready()
        try:
            result = run_once(app, smoke=args.smoke_test)
        except Exception as error:
            diagnostics.worker_failure(error)
            result = 1
        sys.exit(result)
    debug_path = None
    if args.debug:
        from datetime import datetime
        default_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs"
        path = args.log_file or default_dir / f"fluxftp-debug-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log"
        try:
            debug_path = diagnostics.configure_debug(path)
        except OSError as error:
            QMessageBox.critical(None, "Cannot create debug log", str(error))
            sys.exit(1)
        previous_hook = sys.excepthook
        def report_unhandled(exc_type, value, tb):
            diagnostics.worker_failure(value)
            previous_hook(exc_type, value, tb)
        sys.excepthook = report_unhandled
    from .themes import apply_application
    if not args.smoke_test:
        apply_application(app, settings_store(), STYLE)
    window = Window(smoke=args.smoke_test)
    window.ftp_log.appendPlainText(f"FTP/FTPS SSL library: {ssl.OPENSSL_VERSION}")
    window._smoke_test = args.smoke_test
    if debug_path:
        window.log.appendPlainText(f"Debug logging enabled: {debug_path}")
        copy_log = QPushButton("Copy debug log path")
        copy_log.setToolTip(str(debug_path))
        copy_log.clicked.connect(lambda: app.clipboard().setText(str(debug_path)))
        window.statusBar().addPermanentWidget(copy_log)
        diagnostics.logger.info("Desktop ready; reproduce the connection problem to capture diagnostics.")
    if window.preferences.value('Layout/windows/main', '', type=str):
        window.show()
    else:
        window.showMaximized()
    from .openssl_runtime import ready
    ready()
    if not args.smoke_test and sys.platform == 'win32' and getattr(sys, 'frozen', False):
        from .openssl_update_ui import RuntimeUpdater
        window.runtime_updater = RuntimeUpdater(window)
    if not args.smoke_test:
        def recover_queue():
            window.initialize_queue_storage(defer=True)
            window.show_saved_queue()
        QTimer.singleShot(0, recover_queue)
    if args.smoke_test:
        def finish():
            if args.screenshot:
                window.grab().save("preview.png")
            window.close()
        QTimer.singleShot(500, finish)
    exit_code = app.exec()
    if debug_path:
        diagnostics.logger.info("Desktop exited: code=%s", exit_code)
        sys.excepthook = previous_hook
        diagnostics.close_debug()
    sys.exit(exit_code)

