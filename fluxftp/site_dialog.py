"""Saved-site editor dialog and its connection property pages."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QSpinBox, QCheckBox, QDialogButtonBox, QFileDialog,
    QTreeWidget, QTreeWidgetItem, QStackedWidget, QPlainTextEdit, QMessageBox, QInputDialog,
    QGroupBox, QScrollArea)
from .core import Site, PROTOCOLS
from .cloud import CLOUD_PROTOCOLS
from .provider_icons import provider_icon
from .site_import import validate_site
from .site_options import decode as site_options


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
            control.setVisible(self.protocol.currentText() in CLOUD_PROTOCOLS)
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
        if self.protocol.currentText() in CLOUD_PROTOCOLS:
            self.url_preview.setText('Cloud account — provider authorization is managed in Cloud Accounts.')
            return
        from urllib.parse import quote
        schemes = {PROTOCOLS[0]: "ftp", PROTOCOLS[1]: "ftps", PROTOCOLS[2]: "ftps", PROTOCOLS[3]: "sftp", PROTOCOLS[4]: "ftp", "SCP · SSH": "scp"}
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
        return 22 if PROTOCOLS[index] in (PROTOCOLS[3], 'SCP · SSH') else 443 if PROTOCOLS[index] in CLOUD_PROTOCOLS else 990 if index == 2 else 21

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
            is_cloud = PROTOCOLS[index] in CLOUD_PROTOCOLS
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
                    'SCP payloads require a remote scp command; browsing uses SFTP. Byte-offset resume is unavailable.' if PROTOCOLS[index] == 'SCP · SSH' else 'TLS 1.2+ with certificate verification · SSH host keys must be trusted · passwords are stored in the encrypted vault')
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
        if not password and site.username.casefold() != "anonymous" and not site.cloud_account and not (site.managed_key and site.protocol in (PROTOCOLS[3], 'SCP · SSH')):
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
                if record['kind'] != ('ssh' if site.protocol in (PROTOCOLS[3], 'SCP · SSH') else 'tls'):
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
        cloud_id = self.cloud_account.currentData() if self.protocol.currentText() in CLOUD_PROTOCOLS else ''
        if self.protocol.currentText() in CLOUD_PROTOCOLS:
            values['host'] = 'cloud-' + (cloud_id or 'unconfigured')
            values['username'] = ''
            values['name'] = values['name'] or (self.cloud_account.currentText() if cloud_id else '')
        values["name"] = values["name"] or values["host"]
        values["root"] = values["root"] or "/"
        return Site(**values, protocol=self.protocol.currentText(), port=self.port.value(), passive=self.passive.isChecked(),
                    managed_key='' if self.protocol.currentText() in CLOUD_PROTOCOLS else self.managed_key.currentData() or '',
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
                self.cloud_account.addItem(provider_icon(record['provider']),
                    record['name'] + ' (' + record['provider'] + ')', identity)
            self.cloud_account.setCurrentIndex(max(0, self.cloud_account.findData(selected)))
        except (ValueError, OSError):
            self.cloud_account.setToolTip('Open Cloud Accounts to review the account list.')

    def manage_cloud_accounts(self):
        owner = self.key_owner()
        if owner:
            owner.open_cloud_accounts(self)
            self.reload_cloud_accounts()


