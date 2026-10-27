from __future__ import annotations



from pathlib import Path
import json
import uuid



from PySide6.QtCore import Qt, QSettings

from PySide6.QtGui import QColor, QFont

from PySide6.QtWidgets import (

    QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout, QPlainTextEdit,

    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,

    QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QMessageBox, QInputDialog, QRadioButton, QButtonGroup, QGroupBox, QScrollArea,
    QListWidget, QListWidgetItem, QDialogButtonBox, QAbstractItemView, QStyle, QGridLayout,

)
from .ascii_rules import read_rules, validate_rule
from .font_picker import FontPicker





class PreferencesDialog(QDialog):

    """Reference-inspired preferences tree for the controls FluxFTP supports."""



    TREE = [

        ("General", []),
        ("SSL", []),

        ("Sites", []),

        ("Connection", ["Key Manager", "Cloud Accounts", "Ident", "Keep Alive", "Proxy Server", "List Mode", "Data connection"]),

        ("Transfer", ["ASCII List", "Skip List", "File Exists", "Queue", "Priority", "Compress/Cache"]),

        ("Misc.", []),

        ("Display", ["Sort", "Highlights", "Font", "Color", "Layout", "Themes", "Status bar"]),

        ("Mouse Action", []),

        ("File Associations", []),

        ("Event Notification", []),

        ("Confirm", []),

    ]



    def __init__(self, window):

        super().__init__(window)

        self.window = window

        self._proxy_password_dirty = False

        self.settings: QSettings = window.preferences
        from .themes import migrate_theme
        migrate_theme(self.settings)

        self.setWindowTitle("Options")

        self.resize(860, 600)

        outer = QVBoxLayout(self)

        body = QHBoxLayout()

        outer.addLayout(body, 1)

        self.navigation = QTreeWidget()
        self.navigation.setAccessibleName('Options categories')

        self.navigation.setHeaderHidden(True)

        self.navigation.setFixedWidth(190)

        self.pages = QStackedWidget()

        body.addWidget(self.navigation)

        body.addWidget(self.pages, 1)

        self.page_by_name = {}

        for title, children in self.TREE:

            parent = QTreeWidgetItem([title])

            self.navigation.addTopLevelItem(parent)

            for child in children:

                QTreeWidgetItem(parent, [child])

            if children:

                parent.setExpanded(True)

            for name in [title, *children]:

                self.page_by_name[name] = self._make_page(name)

        self.navigation.currentItemChanged.connect(self._select_page)

        self.navigation.setCurrentItem(self.navigation.topLevelItem(0))

        footer = QHBoxLayout()

        footer.addStretch()

        close = QPushButton("Close")
        self.close_button = close

        close.clicked.connect(self.accept)

        footer.addWidget(close)
        self.ascii_buttons = QWidget()
        ascii_footer = QHBoxLayout(self.ascii_buttons)
        ascii_footer.setContentsMargins(0, 0, 0, 0)
        for label, callback in (("Apply", self._apply_ascii_rules), ("OK", self.accept), ("Cancel", self.reject)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            ascii_footer.addWidget(button)
        self.ascii_buttons.setVisible(False)
        footer.addWidget(self.ascii_buttons)

        outer.addLayout(footer)
        self._refresh_theme_controls()
        from .accessibility import fit_dialog
        fit_dialog(self)



    def _make_page(self, title):

        page = QWidget()

        layout = QVBoxLayout(page)

        heading = QLabel(title)

        heading.setStyleSheet("font-size:18px; font-weight:600;")

        layout.addWidget(heading)

        form = QFormLayout()

        layout.addLayout(form)

        description = QLabel()

        description.setObjectName("muted")

        description.setWordWrap(True)

        layout.addWidget(description)

        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        self.pages.addWidget(scroll)

        self._populate(title, form, description)
        from .accessibility import label_forms
        label_forms(page)

        return self.pages.count() - 1



    def _value(self, key, default, kind=None):

        if kind:

            return self.settings.value(key, default, type=kind)

        return self.settings.value(key, default)



    def _check(self, form, key, label, default=False, changed=None):

        control = QCheckBox(label)

        control.setChecked(self._value(key, default, bool))

        control.toggled.connect(lambda value: (self.settings.setValue(key, value), changed(value) if changed else None))

        form.addRow(control)

        return control



    def _spin(self, form, key, label, default, low, high, suffix=""):

        control = QSpinBox()

        control.setRange(low, high)

        control.setValue(self._value(key, default, int))

        control.setSuffix(suffix)

        control.valueChanged.connect(lambda value: self.settings.setValue(key, value))

        form.addRow(label, control)

        return control



    def _combo(self, form, key, label, items, default, changed=None):

        control = QComboBox()

        control.addItems(items)

        control.setCurrentText(self._value(key, default, str))

        control.currentTextChanged.connect(lambda value: (self.settings.setValue(key, value), changed(value) if changed else None))

        form.addRow(label, control)

        return control



    def _line(self, form, key, label, default="", changed=None):

        control = QLineEdit(str(self._value(key, default)))

        control.editingFinished.connect(lambda: (self.settings.setValue(key, control.text()), changed(control.text()) if changed else None))

        form.addRow(label, control)

        return control



    def _unavailable(self, description, feature):

        description.setText(f"{feature} is not implemented in FluxFTP yet. This control is shown for orientation and does not change connection behavior.")



    def _import_sites(self):

        path, _ = QFileDialog.getOpenFileName(
            self, "Import sites", "",
            "Site exports (*.xml *.ini *.txt *.ftp *.csv *.json);;All files (*)")
        if not path:
            return
        try:
            from .site_import import read_import, merge_sites
            from .site_manager import ImportDialog
            result = read_import(path)
            preview = ImportDialog(self, result)
            if preview.exec():
                selected = preview.selected_sites()
                if selected and self.window.save_sites(merge_sites(self.window.sites, selected)):
                    QMessageBox.information(self, "Sites imported", f"Imported {len(selected)} site(s) from {result.source}.")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Import failed", str(error))


    def _export_sites(self):

        if not self.window.sites:
            QMessageBox.information(self, "Export sites", "There are no saved sites to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export sites (without passwords)", "fluxftp-sites.json", "FluxFTP sites (*.json)")
        if not path:
            return
        try:
            from .site_import import export_sites
            export_sites(path, self.window.sites)
            QMessageBox.information(self, "Sites exported", f"Exported {len(self.window.sites)} site(s) without passwords.")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Export failed", str(error))


    def _populate(self, title, form, description):

        if title == "Sites":

            import_button = QPushButton("Import Sites…")
            import_button.clicked.connect(self._import_sites)
            export_button = QPushButton("Export Sites…")
            export_button.clicked.connect(self._export_sites)
            form.addRow(import_button)
            form.addRow(export_button)
            description.setText("Import saved site profiles from supported formats or export them as credential-free FluxFTP JSON. Passwords are never included.")

        elif title == "General":

            path = QLineEdit(str(self._value("General/download_path", str(Path.home()))))

            path.editingFinished.connect(lambda: self.settings.setValue("General/download_path", path.text()))

            browse_row = QWidget()

            row = QHBoxLayout(browse_row)

            row.setContentsMargins(0, 0, 0, 0)

            row.addWidget(path)

            browse = QPushButton("Browse…")

            def choose_download():

                selected = QFileDialog.getExistingDirectory(self, "Choose default download folder", path.text())

                if selected:

                    path.setText(selected)

                    self.settings.setValue("General/download_path", selected)

            browse.clicked.connect(choose_download)

            row.addWidget(browse)

            form.addRow("Default download path", browse_row)



            data_path = QLineEdit(str(self.window.sites_file.parent))

            data_path.setReadOnly(True)

            data_row = QWidget()

            data_layout = QHBoxLayout(data_row)

            data_layout.setContentsMargins(0, 0, 0, 0)

            data_layout.addWidget(data_path)

            data_browse = QPushButton("Change…")

            def choose_data():

                selected = QFileDialog.getExistingDirectory(self, "Choose FluxFTP data folder", data_path.text())

                if selected and self.window.change_data_folder(selected):

                    data_path.setText(selected)

            data_browse.clicked.connect(choose_data)

            data_layout.addWidget(data_browse)

            form.addRow("Data folder", data_row)



            self._line(form, "General/anonymous_email", "Email address for anonymous FTP", "anonymous@fluxftp.local")

            log_lines = self._spin(form, "General/log_lines", "Keep log buffer at last", 300, 100, 10000, " lines")

            log_lines.valueChanged.connect(self.window.log.setMaximumBlockCount)

            self.window.log.setMaximumBlockCount(log_lines.value())

            self._check(form, "General/backup_profiles", "Back up configuration files daily", True)

            self._check(form, "General/log_to_disk", "Write activity log to disk", False, self.window.set_activity_logging)

            self._check(form, "Connection/send_feat", "Send FEAT command after login", True)

            self.general_upnp = self._check(form, "General/upnp_enabled", "Enable UPnP", False)
            self.general_upnp.setToolTip("Request temporary router port mappings for active IPv4 FTP/FTPS data connections. Requires a compatible UPnP router.")
            description.setText("Data folder changes migrate saved sites; daily backups retain the latest four copies; activity logs respect the configured line limit. UPnP maps active IPv4 FTP/FTPS data ports until the connection closes (one-hour lease). It does not apply to passive mode, proxies, SFTP or Ident. OpenSSL updates use Python’s official Windows builds. General application updates, single-instance launch and splash screen are not implemented yet.")

        elif title == "Ident":
            apply = lambda *_: self.window.apply_ident_settings()
            self.ident_enabled = self._check(form, "Ident/enabled", "Enable Ident", False, apply)
            self.ident_connecting = self._check(form, "Ident/only_connecting", "Enable only when connecting", True, apply)
            self.ident_name = self._line(form, "Ident/name", "Ident name", "FluxFTP", apply)
            self.ident_system = self._line(form, "Ident/system", "System", "OTHER", apply)
            self.ident_port = self._spin(form, "Ident/port", "Port", 113, 1, 65535)
            self.ident_port.valueChanged.connect(apply)
            description.setText("Answers RFC 1413 queries for direct IPv4 FluxFTP connections. When enabled only while connecting, the listener stops after login completes or fails. Otherwise it listens while FluxFTP is open. Default TCP port: 113. Proxy connections are not supported.")

        elif title == "List Mode":
            selected = self._value("Connection/list_mode", "Default (LIST)", str)
            if selected not in ("Default (LIST)", "Show Hidden Files", "Custom"):
                if selected == "LIST only":
                    self.settings.setValue("Connection/use_mlsd", False)
                selected = "Default (LIST)"
                self.settings.setValue("Connection/list_mode", selected)
            self.list_mode_group = QButtonGroup(self)
            self.list_mode_buttons = {}
            for label in ("Default (LIST)", "Show Hidden Files", "Custom"):
                button = QRadioButton(label)
                self.list_mode_group.addButton(button)
                self.list_mode_buttons[label] = button
                button.setChecked(label == selected)
                if label == "Custom":
                    row = QWidget()
                    row_layout = QHBoxLayout(row)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.addWidget(button)
                    self.custom_list_command = QComboBox()
                    self.custom_list_command.addItems(["LIST -al", "LIST -a"])
                    self.custom_list_command.setCurrentText(self._value("Connection/list_command", "LIST -al", str))
                    self.custom_list_command.setEnabled(selected == "Custom")
                    self.custom_list_command.currentTextChanged.connect(lambda value: self.settings.setValue("Connection/list_command", value))
                    row_layout.addWidget(self.custom_list_command)
                    row_layout.addStretch()
                    form.addRow(row)
                else:
                    form.addRow(button)
                button.toggled.connect(lambda checked, value=label: self._select_list_mode(value) if checked else None)
            self.list_port_fallback = self._check(form, "Connection/passive_fallback", "Switch to PORT mode automatically when PASV failed", True)
            self.list_use_mlsd = self._check(form, "Connection/use_mlsd", "Use MLSD if available", True)
            from .listing_options import ENCODINGS, MODES
            self._combo(form, 'Connection/filename_encoding', 'FTP filename encoding', MODES, 'Current site settings')
            self._combo(form, 'Connection/encoding_fallback', 'Automatic fallback', ENCODINGS, 'cp1252')
            self._spin(form, 'Connection/sftp_read_aheads', 'SFTP read-ahead requests', 50, 1, 128)
            self._spin(form, 'Connection/list_batch_size', 'Rows per UI batch', 300, 100, 2000)
            self._check(form, 'Connection/list_server_order', 'Initially keep server order (skip sorting)', False)
            self._check(form, 'Connection/mlsd_essential', 'MLSD essential facts only (omit owner / permissions)', False)
            form.addRow(QLabel('Automatic encoding: UTF-8 when advertised; otherwise the selected fallback.\nForced UTF-8 takes priority. SFTP/cloud names remain Unicode.\nLarge folders load in batches. Click a column to sort server-order listings.'))
            description.setText("Default sends LIST; Show Hidden Files sends LIST -a. Custom uses the selected command. When enabled, MLSD is tried first, with the selected LIST command used if MLSD is unavailable. These settings apply to FTP and FTPS.")

        elif title == "Cloud Accounts":
            description.setText('Add cloud storage accounts, then select the provider and account in site properties. Supports Amazon S3, Backblaze B2, Box, Dropbox, Google Cloud Storage, Google Drive, Azure Blob Storage, OneDrive, OneDrive for Business, SharePoint Online, OpenStack Swift and WebDAV.')
            button = QPushButton('Cloud Accounts…')
            button.clicked.connect(lambda: self.window.open_cloud_accounts(self))
            form.addRow(button)

        elif title == "Key Manager":
            description.setText('Generate or import SFTP keys and FTPS client certificates. Choose a managed key in site properties to use it.')
            button = QPushButton('Key Manager…')
            button.clicked.connect(lambda: self.window.open_key_manager(self))
            form.addRow(button)

        elif title == "SSL":
            from .openssl_update_ui import add_controls
            add_controls(form, self.window)
            description.setText('Choose the OpenSSL major version for the next launch. The active version above reports the library actually loaded by FluxFTP.')
        elif title == "Connection":
            button = QPushButton('Key Manager…')
            button.clicked.connect(lambda: self.window.open_key_manager(self))
            form.addRow(button)

            self._check(form, "Connection/send_feat", "Send FEAT command", True)

            self._spin(form, "Connection/connect_timeout", "Connect timeout", 30, 5, 300, " seconds")

            self._spin(form, "Connection/command_timeout", "Timeout waiting for FTP reply", 200, 5, 600, " seconds")

            self._spin(form, "Connection/max_retries", "Maximum connect retries", 2, 0, 30)

            self._spin(form, "Connection/retry_delay", "Connect retry delay", 3, 0, 300, " seconds")

            self._check(form, "Connection/default_passive", "Use passive mode for new sites", True)

            description.setText("Retries apply only to connection setup. Configure NAT handling, socket binding and active local ports under Data connection.")

        elif title == "Data connection":
            self.data_smart_nat = self._check(form, "Connection/smart_pasv", "Smart handle FTP server behind NAT", True)
            self.data_bind_sockets = self._check(form, "Connection/bind_sockets", "Bind sockets to an IP Address",
                                                 self._value("Connection/bind_local_ip", False, bool))
            self.data_bind_local, self.data_local_ip = self._checked_field(
                form, "Connection/bind_local_ip", "Bind Local IP address to:",
                "Connection/local_ip", "", "192.168.1.10", False)
            def update_binding_controls(*_):
                enabled = self.data_bind_sockets.isChecked()
                self.data_bind_local.setEnabled(enabled)
                self.data_local_ip.setEnabled(enabled and self.data_bind_local.isChecked())
            self.data_bind_sockets.toggled.connect(update_binding_controls)
            self.data_bind_local.toggled.connect(update_binding_controls)
            update_binding_controls()
            self.data_limit_ports, self.data_port_range = self._checked_field(
                form, "Connection/limit_local_ports", "Limit Local Port Range",
                "Connection/local_port_range", "", "1400-1500", False)
            description.setText("Binding uses the control connection's local address unless a specific local IP is selected. The port range applies to incoming active FTP/FTPS data connections. Enter an inclusive range such as 1400-1500 or 50000-50100; FluxFTP selects an available port within it. Leave the limit unchecked to let the operating system choose a port.")

        elif title == "Keep Alive":

            self._check(form, "Connection/tcp_keepalive", "Enable TCP keep-alive on active connections", True)

            self._spin(form, "Connection/keepalive_interval", "Probe interval", 30, 5, 300, " seconds")

            self._check(form, "Connection/persistent_browsing", "Reuse browsing connections", True)
            self._check(form, "Connection/ftp_keepalive", "Send NOOP on idle FTP/FTPS browsing connections", True)
            self._spin(form, "Connection/ftp_keepalive_interval", "Idle NOOP interval", 60, 5, 3600, " seconds")
            description.setText("Browsing connections are reused per pane. Idle NOOP probes run separately from transfers and never overlap folder listings. Disconnected sessions reconnect on the next folder request. Single-connection mode disables browsing reuse. TCP keep-alive also applies to transfer sockets. SFTP browsing is reused, but FTP NOOP does not apply to SFTP or cloud accounts.")

        elif title == "Proxy Server":

            enabled = self._check(form, "Connection/proxy_enabled", "Use proxy for FTP, FTPS, and SFTP", False)
            self._active_proxy_profile_id = ""
            self._proxy_profiles = self._read_proxy_profiles()
            profile_row = QWidget()
            profile_layout = QHBoxLayout(profile_row)
            profile_layout.setContentsMargins(0, 0, 0, 0)
            self.proxy_profile_combo = QComboBox()
            for profile in self._proxy_profiles:
                self.proxy_profile_combo.addItem(profile["name"], profile["id"])
            profile_layout.addWidget(self.proxy_profile_combo, 1)
            add_profile = QPushButton("Add")
            save_profile = QPushButton("Save")
            delete_profile = QPushButton("Delete")
            profile_layout.addWidget(add_profile)
            profile_layout.addWidget(save_profile)
            profile_layout.addWidget(delete_profile)
            form.addRow("Proxy profile", profile_row)

            proxy_type = self._combo(form, "Connection/proxy_type", "Proxy type", ["SOCKS5", "SOCKS4a", "HTTP CONNECT"], "SOCKS5")
            proxy_host = self._line(form, "Connection/proxy_host", "Host", "")
            proxy_port = self._spin(form, "Connection/proxy_port", "Port", 1080, 1, 65535)
            auth = self._check(form, "Connection/proxy_auth", "Use proxy authentication", False)
            username = self._line(form, "Connection/proxy_username", "Username", "")
            password = QLineEdit(getattr(self.window, "proxy_password", ""))
            password.setEchoMode(QLineEdit.EchoMode.Password)
            password.textChanged.connect(self._set_proxy_password)
            password.editingFinished.connect(self._save_proxy_password)
            self.proxy_password_field = password
            form.addRow("Password", password)
            self._proxy_profile_fields = {
                "enabled": enabled, "type": proxy_type, "host": proxy_host,
                "port": proxy_port, "auth": auth, "username": username,
                "password": password,
            }

            for control in (proxy_type, proxy_host, proxy_port, auth, username, password):
                control.setEnabled(enabled.isChecked())
            enabled.toggled.connect(lambda value: [control.setEnabled(value) for control in (proxy_type, proxy_host, proxy_port, auth, username, password)])
            auth.toggled.connect(lambda value: (username.setEnabled(value and enabled.isChecked()), password.setEnabled(value and enabled.isChecked())))
            username.setEnabled(auth.isChecked() and enabled.isChecked())
            password.setEnabled(auth.isChecked() and enabled.isChecked())

            selected_id = str(self.settings.value("Connection/proxy_profile_id", self._proxy_profiles[0]["id"], type=str))
            selected_index = self.proxy_profile_combo.findData(selected_id)
            if selected_index < 0:
                selected_index = 0
            self.proxy_profile_combo.setCurrentIndex(selected_index)
            self._load_proxy_profile(self.proxy_profile_combo.currentData())
            self.proxy_profile_combo.currentIndexChanged.connect(self._switch_proxy_profile)
            add_profile.clicked.connect(self._add_proxy_profile)
            save_profile.clicked.connect(self._save_proxy_profile)
            delete_profile.clicked.connect(self._delete_proxy_profile)

            self.proxy_noop = self._check(form, "Connection/transfer_noop", "Send NOOP command during transfer", False)
            self.proxy_passive = self._check(form, "Connection/proxy_passive", "Use passive mode when using proxy", True)
            self.proxy_bypass_local = self._check(form, "Connection/proxy_bypass_local", "Bypass proxy server for local addresses", False)
            self.proxy_exceptions = self._line(form, "Connection/proxy_exceptions", "Exceptions")
            self.proxy_exceptions.setPlaceholderText("Do not use proxy server for addresses beginning with")
            self.proxy_exceptions.setToolTip("Address prefixes separated by semicolons, for example: 192.168.; ftp.internal.")
            description.setText("Exceptions use case-insensitive address prefixes separated by semicolons. Local bypass covers single-label hostnames and private, loopback or link-local IP literals, without DNS lookup. These options apply across proxy profiles. NOOP is sent every 30 seconds while upload/download data is flowing. Proxy passwords are stored in the encrypted credentials file.")

        elif title == "Transfer":
            def section(label):
                group = QGroupBox(label)
                group_form = QFormLayout(group)
                group_form.setContentsMargins(8, 5, 8, 5)
                group_form.setVerticalSpacing(3)
                form.addRow(group)
                return group_form
            speed = section("Speed Limit")
            self.transfer_download_limit, self.transfer_download_speed = self._checked_spin(speed,
                "Transfer/limit_download", "Limit Max Download Speed to", "Transfer/download_kib", 10, 1, 1048576, " KB/s")
            self.transfer_upload_limit, self.transfer_upload_speed = self._checked_spin(speed,
                "Transfer/limit_upload", "Limit Max Upload Speed to", "Transfer/upload_kib", 10, 1, 1048576, " KB/s")
            self._check(speed, "Transfer/bypass_local", "Bypass Speed Limit for Local Addresses", True)
            convert = section("Filename Convert")
            for key, label in (("upload_case", "When Uploading"), ("download_case", "When Downloading"), ("fxp_case", "When FXPing")):
                self._combo(convert, "Transfer/" + key, label, ["Unchanged", "Lowercase", "Uppercase"], "Unchanged")
            transfer = section("Uploading and Downloading")
            self._check(transfer, "Transfer/force_active", "Force Active Mode for Uploading and Downloading", False)
            self._checked_spin(transfer, "Transfer/stall_resume", "Stop and Resume if No Data Received",
                               "Transfer/stall_seconds", 120, 1, 65536, " seconds", True)
            self._spin(transfer, "Transfer/rollback_kib", "When Resume Download, Rollback", 20, 0, 1048576, " KB")
            self._spin(transfer, "Transfer/free_mib", "Keep Free HDD Space at least", 0, 0, 1048576, " MB")
            self._check(transfer, "Transfer/single_connection", "Single connection mode", False)
            self._spin(transfer, "Transfer/per_tab", "Maximum simultaneous transfers per Tab", 10, 1, 10)
            buffers = section("TCP/IP Buffer Size")
            def columns(parent_form):
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                forms = []
                for _ in range(2):
                    cell = QWidget()
                    cell_form = QFormLayout(cell)
                    cell_form.setContentsMargins(0, 0, 0, 0)
                    row_layout.addWidget(cell)
                    forms.append(cell_form)
                parent_form.addRow(row)
                return forms
            send, receive = columns(buffers)
            self._spin(send, "Transfer/send_kib", "Send", 2048, 1, 65536, " KB")
            self._spin(receive, "Transfer/receive_kib", "Receive", 2048, 1, 65536, " KB")
            reliability = QGroupBox("Transfer Reliability")
            reliability_rows = QFormLayout(reliability)
            self._check(reliability_rows, "Transfer/atomic_upload", "Atomic remote upload (temporary file, then rename)", False)
            self._combo(reliability_rows, "Transfer/hash_verification", "Verify file hashes", ["Off", "When available", "Required"], "Off")
            form.addRow(reliability)
            mode, completion = columns(form)
            self._combo(mode, "Transfer/mode", "Transfer Mode", ["Binary", "ASCII", "Auto"], "Binary")
            self._combo(completion, "Transfer/completed", "On Transfer Completed", ["Idle", "Disconnect", "Close FluxFTP"], "Idle")
            description.setText("")

        elif title == "File Exists":
            self.exists_actions = {}
            choices = ["Ask", "Auto Resume", "Auto Skip", "Auto Rename", "Auto Overwrite"]
            self._check(form, "Transfer/conflict_prompt", "Ask before replacing each existing file", False)
            for heading, route in (("Uploading", "upload"), ("Downloading", "download"), ("FXP (FTP to FTP)", "fxp")):
                group = QGroupBox(heading)
                rows = QFormLayout(group)
                for relation, default in (("smaller", "Auto Resume"), ("same", "Auto Skip"), ("larger", "Auto Rename")):
                    self.exists_actions[(route, relation)] = self._combo(rows, f"Transfer/{route}_{relation}",
                        f"Target file is {relation}", choices, default)
                form.addRow(group)
            group = QGroupBox("Error Handle")
            rows = QFormLayout(group)
            self.overwrite_resume_failed = self._check(rows, "Transfer/overwrite_resume_failed",
                "Auto Overwrite file if Resume failed", True)
            form.addRow(group)
            description.setText("Rules compare file sizes. Auto Rename preserves the existing file and uses a numbered destination name. Changes save immediately.")

        elif title == "Queue":
            group = QGroupBox("Options")
            rows = QFormLayout(group)
            self.queue_checks = {}
            for key, label, default in (
                ("reload", "Reload Un-finished Queue When FluxFTP Started", True),
                ("auto_save", "Auto save Queue files", True),
                ("keep_failed", "Keep failed transfer in Queue files", True),
                ("cycle_retry", "Cycle Retry Broken Transfers", True),
                ("retry_after", "Retry All Failed Transfers after Queue finished", True),
                ("clear_switch", "Clear Queue when Manual Switch Server", False),
                ("reset_failed", "Reset failed queues when queue started automatically", False),
                ("clear_logout", "Clear container queue by manual logout of a transferring site", False),
                ("show_paths", "Show source/destination at transfer status log", False)):
                self.queue_checks[key] = self._check(rows, "Queue/" + key, label, default)
            form.addRow(group)
            group = QGroupBox("Repeat Times")
            rows = QFormLayout(group)
            self.queue_retries = {}
            for route, count in (("Upload", 2), ("Download", 5), ("FXP", 0)):
                self.queue_retries[route] = self._spin(rows, "Queue/retry_" + route.lower(),
                    "Retry Failed " + route + " Transfers", count, 0, 999, " times")
            form.addRow(group)
            description.setText("Counts are additional attempts; 0 disables retries. Saved queues reload paused without passwords. Play resumes them. Changes save immediately.")

        elif title == "ASCII List":
            self.ascii_no_resume = QCheckBox("Do not use Resume Transfer for ASCII files")
            self.ascii_no_resume.setChecked(self._value("Transfer/ascii_no_resume", True, bool))
            form.addRow(self.ascii_no_resume)
            note = QLabel("NOTE:\nThe mask supports wildcards and regular expressions. If an item has an icon next to it, it is a regular expression.")
            note.setWordWrap(True)
            form.addRow(note)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            self.ascii_list = QListWidget()
            self.ascii_list.setAccessibleName('ASCII filename rules')
            self.ascii_list.setMinimumHeight(330)
            self.ascii_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            row_layout.addWidget(self.ascii_list, 1)
            buttons = QVBoxLayout()
            self.ascii_add = QPushButton("Add")
            self.ascii_edit = QPushButton("Edit")
            self.ascii_delete = QPushButton("Delete")
            for button in (self.ascii_add, self.ascii_edit, self.ascii_delete):
                buttons.addWidget(button)
            buttons.addStretch()
            row_layout.addLayout(buttons)
            form.addRow(row)
            self._ascii_load_rules(read_rules(self._value("Transfer/ascii_rules", "", str), self._value("Transfer/ascii_extensions", "", str)))
            self.ascii_add.clicked.connect(lambda: self._ascii_edit_rule(False))
            self.ascii_edit.clicked.connect(lambda: self._ascii_edit_rule(True))
            self.ascii_delete.clicked.connect(self._ascii_delete_rules)
            self.ascii_list.itemDoubleClicked.connect(lambda _: self._ascii_edit_rule(True))
            self.ascii_list.itemSelectionChanged.connect(self._ascii_selection_changed)
            self._ascii_selection_changed()
            description.setText("Rules match filenames without their folder path, ignoring case. Auto transfer mode uses this list. Apply or OK saves these settings; Cancel discards unapplied ASCII List edits.")

        elif title == "Skip List":
            from .transfer_options import TransferOptions
            options = QGroupBox("Options")
            grid = QFormLayout(options)
            top = QWidget()
            top_layout = QHBoxLayout(top)
            left, right = QFormLayout(), QFormLayout()
            top_layout.addLayout(left)
            top_layout.addLayout(right)
            self.skip_enabled = self._check(left, "Transfer/skip_enabled", "Enable Skip List", True, self._refresh_skip_colors)
            self.skip_zero = self._check(left, "Transfer/skip_zero", "Skip Zero Byte File", True, self._refresh_skip_colors)
            self.skip_empty = self._check(right, "Transfer/skip_empty", "Skip Empty Directory", True, self._refresh_skip_colors)
            self.skip_folders = self._check(right, "Transfer/skip_folders", "Apply SkipList to Folder", False, self._refresh_skip_colors)
            grid.addRow(top)
            color_row = QWidget()
            color_layout = QHBoxLayout(color_row)
            color_layout.setContentsMargins(0, 0, 0, 0)
            self.skip_color = QComboBox()
            for name, color in (("Red", "#ff0000"), ("Blue", "#0000ff"), ("Gray", "#808080"), ("Orange", "#ff8000")):
                self.skip_color.addItem(name, color)
            current = self._value("Transfer/skip_color", "#ff0000", str)
            if self.skip_color.findData(current) < 0:
                self.skip_color.addItem(current, current)
            self.skip_color.setCurrentIndex(self.skip_color.findData(current))
            self.skip_color.setStyleSheet(f"background:{current};")
            self.skip_color.currentIndexChanged.connect(self._save_skip_color)
            choose_color = QPushButton("…")
            choose_color.setMaximumWidth(32)
            choose_color.clicked.connect(self._choose_skip_color)
            color_layout.addWidget(self.skip_color)
            color_layout.addWidget(choose_color)
            color_layout.addStretch()
            grid.addRow("Color of Skipped items", color_row)
            form.addRow(options)
            mode = QGroupBox("Skip Mode")
            mode_layout = QHBoxLayout(mode)
            self.skip_mode_group = QButtonGroup(self)
            for label in ("Auto Transfer", "Manual Transfer", "Both"):
                button = QRadioButton(label)
                self.skip_mode_group.addButton(button)
                button.setChecked(self._value("Transfer/skip_mode", "Both", str) == label)
                button.toggled.connect(lambda checked, value=label: self._set_skip_mode(value) if checked else None)
                mode_layout.addWidget(button)
            form.addRow(mode)
            group = QGroupBox("Skip List")
            group_layout = QVBoxLayout(group)
            note = QLabel("NOTE:\nThe mask supports wildcards and regular expressions. If an item has an icon next to it, it is a regular expression.")
            note.setWordWrap(True)
            group_layout.addWidget(note)
            row = QHBoxLayout()
            self.skip_list = QListWidget()
            self.skip_list.setMinimumHeight(200)
            self.skip_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            row.addWidget(self.skip_list, 1)
            buttons = QVBoxLayout()
            self.skip_edit = QPushButton("Edit")
            self.skip_delete = QPushButton("Delete")
            for label, callback, button in (("Add", lambda: self._edit_skip_rule(False), None),
                    ("Edit", lambda: self._edit_skip_rule(True), self.skip_edit),
                    ("Delete", self._delete_skip_rules, self.skip_delete),
                    ("Import", self._import_skip_dialog, None), ("Export", self._export_skip_dialog, None)):
                button = button or QPushButton(label)
                button.clicked.connect(callback)
                buttons.addWidget(button)
            buttons.addStretch()
            row.addLayout(buttons)
            group_layout.addLayout(row)
            form.addRow(group)
            self._load_skip_rules(read_rules(self._value("Transfer/skip_rules", TransferOptions().skip_rules, str)))
            self.skip_list.itemDoubleClicked.connect(lambda _: self._edit_skip_rule(True))
            self.skip_list.itemSelectionChanged.connect(self._skip_selection_changed)
            self._skip_selection_changed()
            description.setText("Manual means explicitly selected items; Auto means items discovered inside transferred folders. Both covers both. Changes save immediately.")

        elif title == "Priority":
            from .priority_rules import DEFAULT_RULES, priority_rules
            note = QLabel("NOTE:\nThe mask supports wildcards and regular expressions. If an item has an icon next to it, it is a regular expression.")
            note.setWordWrap(True)
            form.addRow(note)
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            self.priority_list = QListWidget()
            self.priority_list.setAccessibleName('Transfer priority rules')
            self.priority_list.setMinimumHeight(350)
            row.addWidget(self.priority_list, 1)
            buttons = QVBoxLayout()
            self.priority_edit = QPushButton("Edit")
            self.priority_delete = QPushButton("Delete")
            self.priority_up = QPushButton("Move Up")
            self.priority_down = QPushButton("Move Down")
            for label, callback, button in (
                ("Add", lambda: self._edit_priority_rule(False), QPushButton("Add")),
                ("Edit", lambda: self._edit_priority_rule(True), self.priority_edit),
                ("Delete", self._delete_priority_rule, self.priority_delete),
                ("Move Up", lambda: self._move_priority_rule(-1), self.priority_up),
                ("Move Down", lambda: self._move_priority_rule(1), self.priority_down)):
                button.clicked.connect(callback)
                buttons.addWidget(button)
            buttons.addStretch()
            row.addLayout(buttons)
            form.addRow(container)
            self._load_priority_rules(priority_rules(self._value("Transfer/priority_rules", DEFAULT_RULES, str)))
            self.priority_list.itemSelectionChanged.connect(self._priority_selection_changed)
            self.priority_list.itemDoubleClicked.connect(lambda _: self._edit_priority_rule(True))
            self._priority_selection_changed()
            description.setText("Top rules run first; the first matching rule wins. Unmatched items follow in their original order. Active transfers continue. Changes save immediately.")

        elif title == "Compress/Cache":
            from .transfer_options import TransferOptions
            group = QGroupBox('Compression (MODE Z)')
            rows = QFormLayout(group)
            self.compress_checks = {}
            for key, label in (('listing', 'When Listing'), ('upload', 'When Uploading'), ('download', 'When Downloading')):
                self.compress_checks[key] = self._check(rows, 'Transfer/compress_' + key, label, True)
            form.addRow(group)
            group = QGroupBox('Disk cache for download')
            rows = QFormLayout(group)
            self.download_buffer = self._spin(rows, 'Transfer/download_buffer_kib', 'Disk buffer for every download connection', 0, 0, 65536, ' KByte(s)')
            form.addRow(group)
            group = QGroupBox('Exceptions')
            rows = QFormLayout(group)
            self.compression_bypass = self._check(rows, 'Transfer/compression_bypass_local', 'Bypass compression/cache for internal network address', True)
            rows.addRow(QLabel('Do not use compression/cache for addresses beginning with:'))
            self.compression_addresses = QPlainTextEdit(self._value('Transfer/compression_addresses', '', str))
            self.compression_addresses.setFixedHeight(65)
            self.compression_addresses.textChanged.connect(lambda: self.settings.setValue('Transfer/compression_addresses', self.compression_addresses.toPlainText()))
            rows.addRow(self.compression_addresses)
            rows.addRow(QLabel('Use semicolons(;) to separate entries'))
            rows.addRow(QLabel('Do not use compression for file extension matching with:'))
            self.compression_masks = QPlainTextEdit(self._value('Transfer/compression_masks', TransferOptions().compression_masks, str))
            self.compression_masks.setFixedHeight(65)
            self.compression_masks.textChanged.connect(lambda: self.settings.setValue('Transfer/compression_masks', self.compression_masks.toPlainText()))
            rows.addRow(self.compression_masks)
            rows.addRow(QLabel('Use semicolons(;) to separate entries'))
            form.addRow(group)
            description.setText('MODE Z requires server support. A disk buffer of 0 disables extra write buffering. Changes save immediately.')

        elif title == "Misc.":
            self.misc_checks = {}
            def checks(heading, values):
                group = QGroupBox(heading)
                rows = QFormLayout(group)
                for key, label, default in values:
                    self.misc_checks[key] = self._check(rows, 'Misc/' + key, label, default)
                form.addRow(group)
                return rows
            rows = checks('CRC Check', (
                ('crc_auto', 'Automatically check SFV File when downloading', True),
                ('crc_delete_log', 'Delete CRC Log file after completed', True),
                ('crc_delete_tags', 'Delete CRC status tag folder after completed transfer', True),
                ('crc_xcrc', 'Use XCRC if available', False)))
            self.crc_retries = self._spin(rows, 'Misc/crc_retries', 'Readd CRC bad file to Queues for', 0, 0, 999, ' times')
            self.misc_checks['crc_keep_bad'] = self._check(rows, 'Misc/crc_keep_bad', 'Do not delete downloaded bad files', False)
            checks('Folder', (
                ('enter_created', 'Enter folder after created FTP folder', False),
                ('no_empty_dirs', 'Do not Leave Empty Directory when transferring', False),
                ('deny_parent_name', 'Deny Create Directory has Same Name as Parent Directory', False)))
            checks('File name', (
                ('single_sfv', 'Only Allow Transfer of one SFV at Same Directory', True),
                ('skip_multiple_sfv', 'Skip All SFV files if More SFV inside a Directory', True)))
            checks('Transfer', (
                ('keep_file_time', 'Keep File Datetime same as Site when downloading', True),
                ('remove_missing_zero', 'Remove 0 byte file from target Site when source file does not exist', False)))
            checks('New Tab(Window)', (
                ('new_tab_remote', 'Always switch both sides to remote', False),
                ('quick_new_tab', 'Always open new tab (window) for quick connect mode', False)))
            self._check(form, "Misc/beep", "Play a sound when the queue finishes", False, self._apply_beep)
            description.setText('CRC cleanup manages FluxFTP-owned artifacts only. Skip All takes precedence for multiple SFVs. Changes save immediately.')

        elif title == "Sort":
            self.sort_controls = {}
            for location in ('Remote', 'Local'):
                section = QGroupBox(location)
                grid = QGridLayout(section)
                for row, kind in enumerate(('folder', 'file')):
                    prefix = f'Sort/{location.lower()}_{kind}_'
                    choices = QGroupBox('Sort ' + kind + ' with')
                    choices_layout = QGridLayout(choices)
                    key_group = QButtonGroup(choices)
                    direction_box = QGroupBox('Sorting')
                    direction_box.setStyleSheet('QRadioButton:disabled { color:#9a9a9a; }')
                    direction_layout = QVBoxLayout(direction_box)
                    direction_group = QButtonGroup(direction_box)
                    controls = {'keys': {}, 'directions': {}, 'direction_box': direction_box}
                    for index, label in enumerate(('Global', 'Name', 'Size', 'Date')):
                        button = QRadioButton(label)
                        button.setChecked(self._value(prefix + 'key', 'Global') == label)
                        key_group.addButton(button)
                        choices_layout.addWidget(button, index % 2, index // 2)
                        button.toggled.connect(lambda checked, value=label, key=prefix, box=direction_box:
                            (self.settings.setValue(key + 'key', value), box.setEnabled(value != 'Global'), self._apply_sort(None)) if checked else None)
                        controls['keys'][label] = button
                    for label, descending in (('Ascending', False), ('Descending', True)):
                        button = QRadioButton(label)
                        button.setChecked(self._value(prefix + 'descending', False, bool) == descending)
                        direction_group.addButton(button)
                        direction_layout.addWidget(button)
                        button.toggled.connect(lambda checked, value=descending, key=prefix:
                            (self.settings.setValue(key + 'descending', value), self._apply_sort(None)) if checked else None)
                        controls['directions'][label] = button
                    direction_box.setEnabled(self._value(prefix + 'key', 'Global') != 'Global')
                    grid.addWidget(choices, row, 0)
                    grid.addWidget(direction_box, row, 1)
                    grid.setColumnStretch(0, 2)
                    grid.setColumnStretch(1, 1)
                    self.sort_controls[(location.lower(), kind)] = controls
                form.addRow(section)
            box = QGroupBox('Miscellaneous')
            rows = QFormLayout(box)
            self.sort_checks = {}
            for key, label in (('separate_links', 'Separately Sort Folders and Links'),
                               ('date_descending', 'Default Descending Sort Order for Date Column'),
                               ('size_descending', 'Default Descending Sort Order for Size Column')):
                self.sort_checks[key] = self._check(rows, 'Sort/' + key, label, False, self._apply_sort)
            form.addRow(box)
            description.setText('Global follows the column and direction selected in each pane. Click a column again to reverse it. Changes save immediately.')

        elif title == "Highlights":
            from .highlight_rules import highlight_rules
            self.highlights_disabled = self._check(form, "Display/highlights_disabled", "Do not use Highlights", False, self._refresh_skip_colors)
            note = QLabel("NOTE:\nThe mask supports wildcards and regular expressions. If an item has an icon next to it, it is a regular expression.")
            note.setWordWrap(True)
            form.addRow(note)
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            self.highlight_list = QListWidget()
            self.highlight_list.setAccessibleName('Filename highlight rules')
            self.highlight_list.setMinimumHeight(350)
            row.addWidget(self.highlight_list, 1)
            buttons = QVBoxLayout()
            self.highlight_edit = QPushButton("Edit")
            self.highlight_delete = QPushButton("Delete")
            self.highlight_up = QPushButton("Move Up")
            self.highlight_down = QPushButton("Move Down")
            for label, callback, button in (
                ("Add", lambda: self._edit_highlight_rule(False), QPushButton("Add")),
                ("Edit", lambda: self._edit_highlight_rule(True), self.highlight_edit),
                ("Delete", self._delete_highlight_rule, self.highlight_delete),
                ("Move Up", lambda: self._move_highlight_rule(-1), self.highlight_up),
                ("Move Down", lambda: self._move_highlight_rule(1), self.highlight_down)):
                button.clicked.connect(callback)
                buttons.addWidget(button)
            buttons.addStretch()
            row.addLayout(buttons)
            form.addRow(container)
            try:
                rules = highlight_rules(self._value("Display/highlight_rules", "[]", str))
            except ValueError as error:
                rules = []
                form.addRow(QLabel(str(error) + ' Saved rules are retained until you edit the list.'))
            self._load_highlight_rules(rules)
            self.highlight_list.itemSelectionChanged.connect(self._highlight_selection_changed)
            self.highlight_list.itemDoubleClicked.connect(lambda _: self._edit_highlight_rule(True))
            self._highlight_selection_changed()
            description.setText('First matching rule wins. Colors apply to filenames in both panes. Skip List colors take precedence. Changes save immediately.')


        elif title == "Font":
            self.font_previews = {}
            self.font_buttons = {}
            for scope, title in (('application', 'Application'), ('files', 'Remote/Local/Queue Window'), ('logs', 'Log Window')):
                group = QGroupBox(title)
                row = QHBoxLayout(group)
                preview = QLabel()
                preview.setProperty('fontScope', scope)
                preview.setWordWrap(True)
                button = QPushButton('Change Font…')
                button.clicked.connect(lambda checked=False, scope=scope: self._choose_font(scope))
                row.addWidget(preview, 1)
                row.addWidget(button)
                self.font_previews[scope] = preview
                self.font_buttons[scope] = button
                form.addRow(group)
            self.default_font_button = QPushButton('Default Font')
            self.default_font_button.clicked.connect(self._reset_fonts)
            form.addRow('', self.default_font_button)
            self._refresh_font_previews()
            description.setText('Changes save immediately. Default Font restores all three font groups.')

        elif title == "Color":
            from .color_options import ColorControl, color_value
            self.color_controls = {}
            self.color_themes = {}
            for scope, title in (('remote', 'Row Background Color of Remote Window'),
                                 ('local', 'Row Background Color of Local Window'), ('queue', 'Queue Window')):
                box = QGroupBox(title)
                grid = QGridLayout(box)
                theme = QComboBox()
                theme.addItems(['(Default)', 'Classic', 'Light', 'Midnight', 'Dark Grey', 'Black', 'Custom'])
                theme.setCurrentText(self._value('Colors/' + scope + '/theme', '(Default)'))
                grid.addWidget(QLabel('Color theme:'), 0, 0)
                grid.addWidget(theme, 0, 1, 1, 2)
                self.color_themes[scope] = theme
                for col, (role, label) in enumerate((('selection', 'Selection'), ('even', 'Even row'), ('odd', 'Odd row'))):
                    control = ColorControl(color_value(self.settings, scope, role))
                    grid.addWidget(QLabel(label), 1, col)
                    grid.addWidget(control, 2, col)
                    self.color_controls[(scope, role)] = control
                    control.changed.connect(lambda value, scope=scope, role=role: self._set_display_color(scope, role, value))
                theme.currentTextChanged.connect(lambda value, scope=scope: self._set_color_theme(scope, value))
                theme.textActivated.connect(lambda value, scope=scope: self._set_color_theme(scope, value))
                form.addRow(box)
            box = QGroupBox('FTP Log')
            grid = QGridLayout(box)
            for index, (role, label) in enumerate((('background', 'Background Color'), ('error', 'Error Message'),
                ('reply1', 'Server (1) Reply Message'), ('reply2', 'Server (2) Reply Message'),
                ('command', 'FTP Command'), ('prompt', 'Prompt Message'))):
                row, col = divmod(index, 2)
                control = ColorControl(color_value(self.settings, 'log', role))
                grid.addWidget(QLabel(label), row * 2, col)
                grid.addWidget(control, row * 2 + 1, col)
                self.color_controls[('log', role)] = control
                control.changed.connect(lambda value, role=role: self._set_display_color('log', role, value))
            form.addRow(box)
            description.setText('Classic, Light, Midnight, Dark Grey and Black in Themes coordinate these colors automatically. Select System to edit custom colors. Low-contrast text is corrected for readability. Server (1) and (2) correspond to the left and right panes.')

        elif title == "Layout":
            from .layout_options import DEFAULTS
            from .color_options import ColorControl
            self.layout_checks = {}
            def group(title):
                box = QGroupBox(title)
                rows = QFormLayout(box)
                form.addRow(box)
                return rows
            def check(rows, key, title):
                control = self._check(rows, 'Layout/' + key, title, DEFAULTS[key], self.window.apply_layout_settings)
                self.layout_checks[key] = control
                return control
            rows = group('Window List')
            check(rows, 'navigate_tabs', 'Navigate via Tabs')
            check(rows, 'multiple_lines', 'Multiple Lines')
            check(rows, 'tabs_bottom', 'Show Tab(s) at Bottom')
            if hasattr(self.window, 'header_rows'):
                self.layout_checks['tabs_bottom'].setEnabled(False)
                self.layout_checks['tabs_bottom'].setToolTip('Use the Workspace tabs row handle to choose its position.')
                rows.addRow(QLabel('Drag row handles to arrange the toolbar, connection fields, tabs and window list.'))
            self.layout_checks['limit_tab_width'], self.tab_width = self._checked_spin(rows,
                'Layout/limit_tab_width', 'Limit width of Tabs', 'Layout/tab_width', 100, 60, 1000, ' px')
            self.layout_checks['limit_tab_width'].toggled.connect(self.window.apply_layout_settings)
            self.tab_width.valueChanged.connect(self.window.apply_layout_settings)
            active = check(rows, 'custom_active_font', 'Use customize font color for active tab')
            self.active_tab_color = ColorControl(self._value('Layout/active_color', '#ff0000', str))
            self.active_tab_color.setEnabled(active.isChecked())
            active.toggled.connect(self.active_tab_color.setEnabled)
            self.active_tab_color.changed.connect(lambda value: (self.settings.setValue('Layout/active_color', value), self.window.apply_layout_settings()))
            rows.addRow('Active tab color', self.active_tab_color)
            rows = group('Display Caption Area of Dock Panel')
            captions = QWidget()
            grid = QGridLayout(captions)
            grid.setContentsMargins(0, 0, 0, 0)
            for index, (key, label) in enumerate((('caption_tab_style', 'Docking as Tab Style'), ('caption_windows', 'Window List'),
                ('caption_transfer', 'Transfer Log'), ('caption_ftp', 'FTP Log'), ('caption_system', 'System Log'),
                ('caption_queue', 'Queue'), ('caption_tasks', 'Task Window'), ('caption_panes', 'Remote and Local'))):
                control = QCheckBox(label)
                control.setChecked(self._value('Layout/' + key, DEFAULTS[key], bool))
                control.toggled.connect(lambda value, key=key: (self.settings.setValue('Layout/' + key, value), self.window.apply_layout_settings()))
                self.layout_checks[key] = control
                grid.addWidget(control, index // 2, index % 2)
            rows.addRow(captions)
            rows = group('Remote and Local Window')
            check(rows, 'pane_tabs', 'Use Tab Switching for Remote and Local Window')
            large_icons = check(rows, 'large_icons', 'Use Large Icons for Remote/Local Window')
            self.toolbar_icon_size = self._spin(rows, 'Layout/toolbar_icon_size', 'Toolbar icon size', self.window.toolbar_icon_size(), 16, 48, ' px')
            self.toolbar_icon_size.valueChanged.connect(self.window.set_toolbar_icon_size)
            large_icons.toggled.connect(lambda value: self.toolbar_icon_size.setValue(32 if value else 16))
            self._combo(rows, 'Display/pane_layout', 'Pane arrangement', ['Side by side', 'Stacked'], 'Side by side', self._apply_layout)
            rows = group('Performance')
            check(rows, 'disable_toolbar_updates', 'Disable Update Main FTP Toolbar')
            check(rows, 'double_buffer', 'Enable Double Buffer painting for Dock panel')
            description.setText('Changes save immediately. Caption settings control panel titles. Double buffering can reduce painting flicker.')

        elif title == "Themes":

            self.theme_combo = self._combo(form, "Display/style", "Theme", ["Classic", "System", "Follow OS", "Light", "Midnight", "Dark Grey", "Black"], "Classic", self._apply_style)
            self.theme_combo.textActivated.connect(self._apply_style)
            self.header_theme_combo = self._combo(form, 'Display/header_theme', 'Remote / Site headers',
                ['Follow theme', 'Classic', 'System', 'Light', 'Midnight', 'Dark Grey', 'Black'], 'Follow theme',
                self.window.apply_display_settings)

            description.setText("Follow OS automatically switches between Light and Midnight when the operating system appearance changes. Named themes are fixed presets. System allows custom colors. Selecting a theme restores its matching headers.")

        elif title == "Status bar":

            self._check(form, "Display/status_bar", "Show the status bar", True, self._apply_status_bar)

            description.setText("Show or hide the connection and transfer status line at the bottom of the main window.")

        elif title == "Display":
            self.display_checks = {}
            def group(title):
                box = QGroupBox(title)
                rows = QFormLayout(box)
                form.addRow(box)
                return rows
            def check(rows, key, label, default=False):
                control = self._check(rows, 'Display/' + key, label, default, self.window.apply_display_settings)
                self.display_checks[key] = control
                return control
            rows = group('System Tray')
            check(rows, 'minimize_tray', 'Minimize to system tray')
            check(rows, 'flash_tray', 'Flash Tray icon when transferring', True)
            check(rows, 'always_tray', 'Always Show Tray Icon', True)
            rows = group('Local Window')
            check(rows, 'local_full_path', 'Display the full path in the local address bar')
            hidden = check(rows, 'local_show_hidden', 'Show hidden files and folders')
            hidden.toggled.connect(lambda _: [pane.refresh() for i in range(self.window.tabs.count())
                for pane in (self.window.tabs.widget(i).widget(0), self.window.tabs.widget(i).widget(1)) if pane.mode == 'Local'])
            rows = group('Display Mode of File Size')
            buttons = QWidget()
            layout = QHBoxLayout(buttons)
            layout.setContentsMargins(0, 0, 0, 0)
            self.size_modes = {}
            self.size_mode_group = QButtonGroup(self)
            for label in ('Bytes', 'KBytes', 'Dynamic'):
                button = QRadioButton(label)
                button.setChecked(self._value('Display/size_mode', 'Dynamic') == label)
                button.toggled.connect(lambda checked, value=label: (self.settings.setValue('Display/size_mode', value), self.window.apply_display_settings()) if checked else None)
                self.size_mode_group.addButton(button)
                self.size_modes[label] = button
                layout.addWidget(button)
            rows.addRow(buttons)
            rows = group('Transfer')
            check(rows, 'flash_completed', 'Flash Window when Transfer Completed', True)
            self.display_checks['update_download_size'], self.download_seconds = self._checked_spin(rows,
                'Display/update_download_size', 'Update Downloading File Size every', 'Display/download_seconds', 15, 1, 3600, ' second(s)')
            rows = group('Sites Menu')
            self.display_checks['split_sites'], self.sites_lines = self._checked_spin(rows,
                'Display/split_sites', 'Split Sites Menu every', 'Display/sites_lines', 25, 1, 1000, ' lines')
            for control in (self.download_seconds, self.sites_lines): control.valueChanged.connect(self.window.apply_display_settings)
            for key in ('update_download_size', 'split_sites'): self.display_checks[key].toggled.connect(self.window.apply_display_settings)
            check(rows, 'hide_quick_connect', 'Hide Quick Connect from Sites Dropdown Menu')
            check(rows, 'show_quick_dialog', 'Show Quick Connect dialog entry', True)
            check(rows, 'show_recent', 'Show "Recent sites" entry', True)
            rows = group('Miscellaneous')
            check(rows, 'gradient_indicator', 'Use Gradient Color for Side Indicator', True)
            check(rows, 'hide_unfocused_selection', 'Hide Selection when ListView Lost Focus')
            check(rows, 'hyphen_separator', 'Always use "-" to split Left/Right site name')
            description.setText('Changes save immediately. Tray options require a system tray. Recent sites store connection details without passwords.')

        elif title == "Mouse Action":
            from .mouse_options import DOUBLE_CLICK_DEFAULTS, DOUBLE_CLICK_LABELS, CHOICES, mouse_action
            wheel_box = QGroupBox('Ctrl + mouse wheel sizing')
            wheel_rows = QFormLayout(wheel_box)
            self._check(wheel_rows, 'Mouse/wheel_icons', 'Resize toolbar icons over toolbar buttons', True)
            self._check(wheel_rows, 'Mouse/wheel_text', 'Resize text in file panes, queue, logs and Workspace list', True)
            self._check(wheel_rows, 'Mouse/wheel_panels', 'Resize Workspace list height over its resize handle', True)
            form.addRow(wheel_box)
            self.mouse_combos = {}
            box = QGroupBox('Double Click')
            grid = QGridLayout(box)
            for index, (key, default) in enumerate(DOUBLE_CLICK_DEFAULTS.items()):
                row, column = divmod(index, 2)
                grid.addWidget(QLabel(DOUBLE_CLICK_LABELS[key]), row * 2, column)
                control = QComboBox()
                control.addItems(CHOICES[key])
                control.setCurrentText(mouse_action(self.settings, key))
                control.currentTextChanged.connect(lambda value, key=key: self.settings.setValue('Mouse/' + key, value))
                grid.addWidget(control, row * 2 + 1, column)
                self.mouse_combos[key] = control
            form.addRow(box)
            box = QGroupBox('Drag and Drop')
            rows = QFormLayout(box)
            self.mouse_combos['explorer_drop'] = self._combo(rows, 'Mouse/explorer_drop',
                'From Explorer to Site', CHOICES['explorer_drop'], 'Begin Transfer')
            self.mouse_paste = self._check(rows, 'Mouse/allow_paste',
                'Allow paste local files to Remote window', True)
            form.addRow(box)
            box = QGroupBox('Context menu')
            rows = QFormLayout(box)
            self.mouse_combos['local_context'] = self._combo(rows, 'Mouse/local_context',
                'Right click on local window', CHOICES['local_context'], 'Default popup menu')
            form.addRow(box)
            description.setText('Mouse action preferences save immediately.')

        elif title == "File Associations":
            from .file_associations import AssociationList
            self.association_lists = {}
            for kind in ('view', 'edit'):
                control = AssociationList(self.settings, kind)
                self.association_lists[kind] = control
                form.addRow(control)
            description.setText('TIP: You can use semicolons (;) to separate file masks for the same item.')

        elif title == "Event Notification":

            self._check(form, "Misc/beep", "Play a sound when the queue finishes", False, self._apply_beep)

            description.setText("Queue completion can play a system alert sound.")

        elif title == "Confirm":
            from .confirm_options import DEFAULTS, LABELS
            box = QGroupBox('Confirm')
            rows = QFormLayout(box)
            self.confirm_checks = {key: self._check(rows, 'Confirm/' + key, LABELS[key], default)
                                   for key, default in DEFAULTS.items()}
            form.addRow(box)
            description.setText('Confirmation preferences save immediately.')

        else:

            self._unavailable(description, title)



    def _select_page(self, item, _previous):

        if item:

            self.pages.setCurrentIndex(self.page_by_name[item.text(0)])
            if hasattr(self, "ascii_buttons"):
                ascii_page = item.text(0) == "ASCII List"
                self.ascii_buttons.setVisible(ascii_page)
                self.close_button.setVisible(not ascii_page)

    def _ascii_load_rules(self, rules):
        self.ascii_list.clear()
        for rule in rules:
            item = QListWidgetItem(rule["pattern"])
            item.setData(Qt.ItemDataRole.UserRole, rule)
            if rule["regex"]:
                item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
                item.setToolTip("Regular expression: " + rule["pattern"])
            self.ascii_list.addItem(item)

    def _ascii_selection_changed(self):
        count = len(self.ascii_list.selectedItems())
        self.ascii_edit.setEnabled(count == 1)
        self.ascii_delete.setEnabled(count > 0)

    def _ascii_rule_dialog(self, rule=None, kind="ASCII"):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit {kind} mask" if rule else f"Add {kind} mask")
        layout = QFormLayout(dialog)
        pattern = QLineEdit(rule["pattern"] if rule else "")
        pattern.setPlaceholderText("*.txt")
        regex = QCheckBox("Regular expression")
        regex.setChecked(bool(rule and rule["regex"]))
        layout.addRow("Mask", pattern)
        layout.addRow(regex)
        if kind == 'Highlights':
            color = QLineEdit(rule.get('color', '#0000ff') if rule else '#0000ff')
            choose = QPushButton('Choose…')
            def pick_color():
                selected = QColorDialog.getColor(QColor(color.text()), dialog, 'Highlight text color')
                if selected.isValid(): color.setText(selected.name())
            choose.clicked.connect(pick_color)
            color_row = QWidget()
            color_layout = QHBoxLayout(color_row)
            color_layout.setContentsMargins(0, 0, 0, 0)
            color_layout.addWidget(color)
            color_layout.addWidget(choose)
            layout.addRow('Text color', color_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        result = []
        def save():
            try:
                value = validate_rule(pattern.text(), regex.isChecked())
                if kind == 'Highlights':
                    if not QColor(color.text()).isValid(): raise ValueError('Choose a valid highlight color.')
                    value['color'] = QColor(color.text()).name()
                result.append(value)
            except ValueError as error:
                QMessageBox.warning(dialog, f"Invalid {kind} mask", str(error))
                return
            dialog.accept()
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        return result[0] if dialog.exec() and result else None

    def _ascii_edit_rule(self, editing):
        selected = self.ascii_list.selectedItems()
        if editing and len(selected) != 1:
            return
        if not editing and self.ascii_list.count() >= 1000:
            QMessageBox.warning(self, "ASCII List", "The list supports up to 1000 masks.")
            return
        old = selected[0] if editing else None
        rule = self._ascii_rule_dialog(old.data(Qt.ItemDataRole.UserRole) if old else None)
        if rule is None:
            return
        rules = [self.ascii_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.ascii_list.count())]
        index = self.ascii_list.row(old) if old else len(rules)
        if old:
            rules[index] = rule
        else:
            rules.append(rule)
        self._ascii_load_rules(rules)
        self.ascii_list.setCurrentRow(index)

    def _ascii_delete_rules(self):
        for item in self.ascii_list.selectedItems():
            self.ascii_list.takeItem(self.ascii_list.row(item))

    def _apply_ascii_rules(self):
        rules = [self.ascii_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.ascii_list.count())]
        self.settings.setValue("Transfer/ascii_rules", json.dumps(rules, ensure_ascii=False))
        self.settings.setValue("Transfer/ascii_no_resume", self.ascii_no_resume.isChecked())
        self.settings.sync()

    def accept(self):
        self._apply_ascii_rules()
        super().accept()

    def _refresh_skip_colors(self, *_):
        self.window.refresh_skip_colors()

    def _load_priority_rules(self, rules):
        self.priority_list.clear()
        for rule in rules:
            item = QListWidgetItem(rule['pattern'])
            item.setData(Qt.ItemDataRole.UserRole, rule)
            if rule['regex']:
                item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
                item.setToolTip('Regular expression: ' + rule['pattern'])
            self.priority_list.addItem(item)

    def _priority_selection_changed(self):
        index = self.priority_list.currentRow() if self.priority_list.selectedItems() else -1
        self.priority_edit.setEnabled(index >= 0)
        self.priority_delete.setEnabled(index >= 0)
        self.priority_up.setEnabled(index > 0)
        self.priority_down.setEnabled(0 <= index < self.priority_list.count() - 1)

    def _save_priority_rules(self):
        rules = [self.priority_list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.priority_list.count())]
        self.settings.setValue('Transfer/priority_rules', json.dumps(rules, ensure_ascii=False))
        self._priority_selection_changed()

    def _edit_priority_rule(self, editing):
        index = self.priority_list.currentRow() if editing else self.priority_list.count()
        if index < 0:
            return
        old = self.priority_list.item(index) if editing else None
        rule = self._ascii_rule_dialog(old.data(Qt.ItemDataRole.UserRole) if old else None, 'Priority')
        if rule is None:
            return
        rules = [self.priority_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.priority_list.count())]
        if editing:
            rules[index] = rule
        else:
            rules.append(rule)
        from .priority_rules import priority_rules
        try:
            priority_rules(json.dumps(rules))
        except ValueError as error:
            QMessageBox.warning(self, 'Priority List', str(error))
            return
        self._load_priority_rules(rules)
        self.priority_list.setCurrentRow(index)
        self._save_priority_rules()

    def _delete_priority_rule(self):
        index = self.priority_list.currentRow()
        if index >= 0:
            self.priority_list.takeItem(index)
            self._save_priority_rules()

    def _move_priority_rule(self, direction):
        index = self.priority_list.currentRow()
        target = index + direction
        if index >= 0 and 0 <= target < self.priority_list.count():
            item = self.priority_list.takeItem(index)
            self.priority_list.insertItem(target, item)
            self.priority_list.setCurrentRow(target)
            self._save_priority_rules()

    def _load_highlight_rules(self, rules):
        self.highlight_list.clear()
        for rule in rules:
            item = QListWidgetItem(rule['pattern'])
            item.setData(Qt.ItemDataRole.UserRole, rule)
            if rule['regex']:
                item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
                item.setToolTip('Regular expression: ' + rule['pattern'])
            item.setForeground(QColor(rule['color']))
            item.setToolTip(item.toolTip() + ' · ' + rule['color'])
            self.highlight_list.addItem(item)

    def _highlight_selection_changed(self):
        index = self.highlight_list.currentRow() if self.highlight_list.selectedItems() else -1
        self.highlight_edit.setEnabled(index >= 0)
        self.highlight_delete.setEnabled(index >= 0)
        self.highlight_up.setEnabled(index > 0)
        self.highlight_down.setEnabled(0 <= index < self.highlight_list.count() - 1)

    def _save_highlight_rules(self):
        rules = [self.highlight_list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.highlight_list.count())]
        self.settings.setValue('Display/highlight_rules', json.dumps(rules, ensure_ascii=False))
        self._highlight_selection_changed()
        self.window.refresh_skip_colors()

    def _edit_highlight_rule(self, editing):
        index = self.highlight_list.currentRow() if editing else self.highlight_list.count()
        if index < 0:
            return
        old = self.highlight_list.item(index) if editing else None
        rule = self._ascii_rule_dialog(old.data(Qt.ItemDataRole.UserRole) if old else None, 'Highlights')
        if rule is None:
            return
        rules = [self.highlight_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.highlight_list.count())]
        if editing:
            rules[index] = rule
        else:
            rules.append(rule)
        from .highlight_rules import highlight_rules
        try:
            highlight_rules(json.dumps(rules))
        except ValueError as error:
            QMessageBox.warning(self, 'Highlights List', str(error))
            return
        self._load_highlight_rules(rules)
        self.highlight_list.setCurrentRow(index)
        self._save_highlight_rules()

    def _delete_highlight_rule(self):
        index = self.highlight_list.currentRow()
        if index >= 0:
            self.highlight_list.takeItem(index)
            self._save_highlight_rules()

    def _move_highlight_rule(self, direction):
        index = self.highlight_list.currentRow()
        target = index + direction
        if index >= 0 and 0 <= target < self.highlight_list.count():
            item = self.highlight_list.takeItem(index)
            self.highlight_list.insertItem(target, item)
            self.highlight_list.setCurrentRow(target)
            self._save_highlight_rules()

    def _set_skip_mode(self, mode):
        self.settings.setValue("Transfer/skip_mode", mode)
        self._refresh_skip_colors()

    def _save_skip_color(self, *_):
        color = self.skip_color.currentData()
        self.settings.setValue("Transfer/skip_color", color)
        self.skip_color.setStyleSheet(f"background:{color};")
        self._refresh_skip_colors()

    def _choose_skip_color(self):
        color = QColorDialog.getColor(QColor(self.skip_color.currentData()), self, "Color of Skipped items")
        if color.isValid():
            index = self.skip_color.findData(color.name())
            if index < 0:
                self.skip_color.addItem(color.name(), color.name())
                index = self.skip_color.count() - 1
            self.skip_color.setCurrentIndex(index)

    def _load_skip_rules(self, rules):
        self.skip_list.clear()
        for rule in rules:
            item = QListWidgetItem(rule["pattern"])
            item.setData(Qt.ItemDataRole.UserRole, rule)
            if rule["regex"]:
                item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
                item.setToolTip("Regular expression: " + rule["pattern"])
            self.skip_list.addItem(item)

    def _skip_rules(self):
        return [self.skip_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.skip_list.count())]

    def _save_skip_rules(self):
        self.settings.setValue("Transfer/skip_rules", json.dumps(self._skip_rules(), ensure_ascii=False))
        self._refresh_skip_colors()

    def _skip_selection_changed(self):
        count = len(self.skip_list.selectedItems())
        self.skip_edit.setEnabled(count == 1)
        self.skip_delete.setEnabled(count > 0)

    def _edit_skip_rule(self, editing):
        selected = self.skip_list.selectedItems()
        if editing and len(selected) != 1:
            return
        old = selected[0] if editing else None
        rule = self._ascii_rule_dialog(old.data(Qt.ItemDataRole.UserRole) if old else None, "Skip")
        if rule is None:
            return
        rules = self._skip_rules()
        index = self.skip_list.row(old) if old else len(rules)
        if old:
            rules[index] = rule
        else:
            rules.append(rule)
        try:
            read_rules(json.dumps(rules))
        except ValueError as error:
            QMessageBox.warning(self, "Skip List", str(error))
            return
        self._load_skip_rules(rules)
        self.skip_list.setCurrentRow(index)
        self._save_skip_rules()

    def _delete_skip_rules(self):
        for item in self.skip_list.selectedItems():
            self.skip_list.takeItem(self.skip_list.row(item))
        self._save_skip_rules()

    def import_skip_rules(self, path):
        path = Path(path)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Skip List import must be smaller than 1 MB.")
        text = path.read_text("utf-8-sig")
        if text.lstrip().startswith(("[", "{")):
            data = json.loads(text)
            if isinstance(data, dict):
                if data.get("format") != "fluxftp-skip-list" or data.get("version") != 1:
                    raise ValueError("Unsupported Skip List file.")
                data = data.get("rules")
            incoming = read_rules(json.dumps(data))
        else:
            incoming = [validate_rule(line.strip()[6:], True) if line.strip().startswith("regex:") else validate_rule(line.strip())
                        for line in text.splitlines() if line.strip()]
        merged = self._skip_rules()
        for rule in incoming:
            if rule not in merged:
                merged.append(rule)
        read_rules(json.dumps(merged))
        self._load_skip_rules(merged)
        self._save_skip_rules()

    def export_skip_rules(self, path):
        Path(path).write_text(json.dumps({"format": "fluxftp-skip-list", "version": 1, "rules": self._skip_rules()}, indent=2, ensure_ascii=False), "utf-8")

    def _import_skip_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Skip List", "", "Skip lists (*.json *.txt);;All files (*)")
        if path:
            try:
                self.import_skip_rules(path)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Skip List import failed", str(error))

    def _export_skip_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Skip List", "skip-list.json", "JSON (*.json)")
        if path:
            try:
                self.export_skip_rules(path)
            except OSError as error:
                QMessageBox.warning(self, "Skip List export failed", str(error))



    def _read_proxy_profiles(self):
        raw = self.settings.value("Connection/proxy_profiles", "", type=str)
        try:
            profiles = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            profiles = []
        if isinstance(profiles, list):
            profiles = [
                {"id": str(item.get("id", "")), "name": str(item.get("name", "")),
                 "type": str(item.get("type", "SOCKS5")), "host": str(item.get("host", "")),
                 "port": int(item.get("port", 1080)), "auth": bool(item.get("auth", False)),
                 "username": str(item.get("username", ""))}
                for item in profiles if isinstance(item, dict) and item.get("id") and item.get("name")
            ]
        else:
            profiles = []
        if not profiles:
            profiles = [{
                "id": "proxy", "name": "Default", "type": str(self.settings.value("Connection/proxy_type", "SOCKS5", type=str)),
                "host": str(self.settings.value("Connection/proxy_host", "", type=str)),
                "port": self.settings.value("Connection/proxy_port", 1080, type=int),
                "auth": self.settings.value("Connection/proxy_auth", False, type=bool),
                "username": str(self.settings.value("Connection/proxy_username", "", type=str)),
            }]
            self.settings.setValue("Connection/proxy_profiles", json.dumps(profiles))
            self.settings.setValue("Connection/proxy_profile_id", "proxy")
        return profiles

    @staticmethod
    def _proxy_credential_id(profile_id):
        return "proxy" if profile_id == "proxy" else f"proxy:{profile_id}"

    def _load_proxy_profile(self, profile_id):
        profile = next((item for item in self._proxy_profiles if item["id"] == profile_id), None)
        if not profile:
            return
        self._active_proxy_profile_id = profile_id
        credential_id = self._proxy_credential_id(profile_id)
        self.window.proxy_credential_id = credential_id
        try:
            stored_password = self.window.credential_vault.get(credential_id)
        except Exception:
            stored_password = getattr(self.window, "proxy_password", "") if profile_id == "proxy" else ""
        fields = self._proxy_profile_fields
        for key, value in (("type", profile["type"]), ("host", profile["host"]),
                           ("port", profile["port"]), ("auth", profile["auth"]),
                           ("username", profile["username"])):
            control = fields[key]
            blocked = control.blockSignals(True)
            if key == "port":
                control.setValue(max(1, min(65535, int(value))))
            elif key == "auth":
                control.setChecked(bool(value))
            elif key == "type":
                control.setCurrentText(value if value in ("SOCKS5", "SOCKS4a", "HTTP CONNECT") else "SOCKS5")
            else:
                control.setText(str(value))
            control.blockSignals(blocked)
        password = fields["password"]
        blocked = password.blockSignals(True)
        password.setText(stored_password)
        password.blockSignals(blocked)
        self.window.proxy_password = stored_password
        self.window._proxy_password_entered = bool(stored_password)
        self._proxy_password_dirty = False
        self.settings.setValue("Connection/proxy_profile_id", profile_id)
        self.settings.setValue("Connection/proxy_enabled", bool(fields["enabled"].isChecked()))
        for key, setting_key in (("type", "proxy_type"), ("host", "proxy_host"), ("port", "proxy_port"),
                                 ("auth", "proxy_auth"), ("username", "proxy_username")):
            value = fields[key].currentText() if key == "type" else (
                fields[key].value() if key == "port" else fields[key].isChecked() if key == "auth" else fields[key].text())
            self.settings.setValue(f"Connection/{setting_key}", value)
        auth_enabled = fields["auth"].isChecked() and fields["enabled"].isChecked()
        fields["type"].setEnabled(fields["enabled"].isChecked())
        fields["host"].setEnabled(fields["enabled"].isChecked())
        fields["port"].setEnabled(fields["enabled"].isChecked())
        fields["auth"].setEnabled(fields["enabled"].isChecked())
        fields["username"].setEnabled(auth_enabled)
        password.setEnabled(auth_enabled)

    def _set_proxy_password(self, value):
        self.window.proxy_password = value
        self.window._proxy_password_entered = True
        self._proxy_password_dirty = True

    def _save_proxy_password(self):
        field = getattr(self, "proxy_password_field", None)
        if field is None or not getattr(self, "_proxy_password_dirty", False):
            return False
        profile_id = getattr(self, "_active_proxy_profile_id", "proxy")
        self.window.proxy_credential_id = self._proxy_credential_id(profile_id)
        try:
            self.window.set_proxy_password(field.text())
        except Exception:
            self.window.statusBar().showMessage("Proxy password could not be saved to encrypted storage.", 10000)
            return False
        self._proxy_password_dirty = False
        return True

    def _save_proxy_profile(self):
        profile_id = getattr(self, "_active_proxy_profile_id", "")
        if not profile_id or not hasattr(self, "_proxy_profile_fields"):
            return
        fields = self._proxy_profile_fields
        profile = next(item for item in self._proxy_profiles if item["id"] == profile_id)
        profile.update({"type": fields["type"].currentText(), "host": fields["host"].text().strip(),
                        "port": fields["port"].value(), "auth": fields["auth"].isChecked(),
                        "username": fields["username"].text()})
        self.settings.setValue("Connection/proxy_profiles", json.dumps(self._proxy_profiles, ensure_ascii=False))
        self.settings.setValue("Connection/proxy_profile_id", profile_id)
        self.settings.setValue("Connection/proxy_enabled", fields["enabled"].isChecked())
        for key, value in (("proxy_type", profile["type"]), ("proxy_host", profile["host"]),
                           ("proxy_port", profile["port"]), ("proxy_auth", profile["auth"]),
                           ("proxy_username", profile["username"])):
            self.settings.setValue(f"Connection/{key}", value)
        password_was_dirty = self._proxy_password_dirty
        password_saved = self._save_proxy_password()
        if hasattr(self.window, "statusBar") and (not password_was_dirty or password_saved):
            self.window.statusBar().showMessage(f"Proxy profile '{profile['name']}' saved", 4000)

    def _switch_proxy_profile(self, index):
        if index < 0:
            return
        previous = self._active_proxy_profile_id
        next_id = self.proxy_profile_combo.itemData(index)
        if previous and next_id != previous:
            self._save_proxy_profile()
            self._load_proxy_profile(next_id)

    def _add_proxy_profile(self):
        self._save_proxy_profile()
        name, accepted = QInputDialog.getText(self, "Add proxy profile", "Profile name:")
        name = name.strip()
        if not accepted or not name:
            return
        profile_id = uuid.uuid4().hex
        self._proxy_profiles.append({"id": profile_id, "name": name, "type": "SOCKS5", "host": "",
                                     "port": 1080, "auth": False, "username": ""})
        self.settings.setValue("Connection/proxy_profiles", json.dumps(self._proxy_profiles, ensure_ascii=False))
        self.proxy_profile_combo.addItem(name, profile_id)
        self.proxy_profile_combo.setCurrentIndex(self.proxy_profile_combo.count() - 1)

    def _delete_proxy_profile(self):
        if len(self._proxy_profiles) <= 1:
            self.window.statusBar().showMessage("At least one proxy profile must remain", 5000)
            return
        profile_id = self._active_proxy_profile_id
        profile = next(item for item in self._proxy_profiles if item["id"] == profile_id)
        answer = QMessageBox.question(self, "Delete proxy profile", f"Delete proxy profile ‘{profile['name']}’?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._save_proxy_password()
        self._proxy_profiles = [item for item in self._proxy_profiles if item["id"] != profile_id]
        try:
            self.window.credential_vault.set(self._proxy_credential_id(profile_id), "")
        except Exception:
            self.window.statusBar().showMessage("The proxy profile was removed, but its encrypted password could not be deleted.", 10000)
        self.proxy_profile_combo.blockSignals(True)
        self.proxy_profile_combo.removeItem(self.proxy_profile_combo.currentIndex())
        self.proxy_profile_combo.blockSignals(False)
        self.settings.setValue("Connection/proxy_profiles", json.dumps(self._proxy_profiles, ensure_ascii=False))
        self._active_proxy_profile_id = ""
        self.proxy_profile_combo.setCurrentIndex(0)
        self._load_proxy_profile(self.proxy_profile_combo.currentData())

    def done(self, result):
        self._save_proxy_profile()
        super().done(result)

    def _apply_resume(self, enabled):

        if hasattr(self.window, "resume"):

            self.window.resume.setChecked(enabled)



    def _apply_list_mode(self, _value):

        for pane in self.window.current_panes():

            pane.refresh()

    def _select_list_mode(self, value):
        self.settings.setValue("Connection/list_mode", value)
        self.custom_list_command.setEnabled(value == "Custom")

    def _checked_field(self, form, check_key, label, value_key, default, placeholder, checked=False):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        check = QCheckBox(label)
        check.setChecked(self._value(check_key, checked, bool))
        field = QLineEdit(self._value(value_key, default, str))
        field.setAccessibleName(label)
        field.setPlaceholderText(placeholder)
        field.setEnabled(check.isChecked())
        check.toggled.connect(lambda enabled: (self.settings.setValue(check_key, enabled), field.setEnabled(enabled)))
        field.editingFinished.connect(lambda: self.settings.setValue(value_key, field.text().strip()))
        layout.addWidget(check)
        layout.addWidget(field)
        form.addRow(row)
        return check, field

    def _checked_spin(self, form, check_key, label, value_key, default, low, high, suffix, checked=False):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        check = QCheckBox(label)
        check.setChecked(self._value(check_key, checked, bool))
        spin = QSpinBox()
        from .accessibility import name_control
        name_control(spin, label)
        spin.setRange(low, high)
        spin.setValue(self._value(value_key, default, int))
        spin.setSuffix(suffix)
        spin.setMaximumWidth(170)
        spin.setEnabled(check.isChecked())
        check.toggled.connect(lambda enabled: (self.settings.setValue(check_key, enabled), spin.setEnabled(enabled)))
        spin.valueChanged.connect(lambda value: self.settings.setValue(value_key, value))
        layout.addWidget(check)
        layout.addStretch()
        layout.addWidget(spin)
        form.addRow(row)
        return check, spin



    def _apply_sort(self, _value):
        for index in range(self.window.tabs.count()):
            split = self.window.tabs.widget(index)
            for pane in (split.widget(0), split.widget(1)):
                pane.resort_rows()

    def _apply_hidden(self, _value):

        for pane in self.window.current_panes():

            pane.refresh()



    def _apply_layout(self, value):

        orientation = Qt.Orientation.Horizontal if value == "Side by side" else Qt.Orientation.Vertical

        for i in range(self.window.tabs.count()):

            self.window.tabs.widget(i).setOrientation(orientation)
            if getattr(self.window, '_panel_positions_ready', False):
                self.window.initialize_pane_positions(self.window.tabs.widget(i))



    def _apply_style(self, value):

        app = self.window.app_instance
        self.settings.setValue('Display/style', value)
        # Explicit theme selection restores preset appearance, even when reselected.
        self.settings.setValue('Display/header_theme', 'Follow theme')
        self.settings.setValue('Display/gradient_indicator', True)
        self.settings.setValue('Layout/custom_active_font', False)
        if hasattr(self, 'header_theme_combo'):
            self.header_theme_combo.blockSignals(True)
            self.header_theme_combo.setCurrentText('Follow theme')
            self.header_theme_combo.blockSignals(False)
        if hasattr(self, 'display_checks'):
            self.display_checks['gradient_indicator'].setChecked(True)
        if hasattr(self, 'layout_checks'):
            self.layout_checks['custom_active_font'].setChecked(False)

        from .themes import apply_application
        from .color_options import color_value
        apply_application(app, self.settings, self.window.STYLE)
        self.window.apply_display_settings()
        self._refresh_theme_controls()
        if hasattr(self.window, 'system_appearance'):
            self.window.system_appearance.refresh()

    def _refresh_theme_controls(self):
        from .themes import palette_for, theme_name
        from .color_options import color_value
        coordinated = palette_for(self.settings) is not None
        for scope, control in self.color_themes.items():
            control.blockSignals(True)
            control.setCurrentText(theme_name(self.settings) if coordinated
                                   else self.settings.value(f'Colors/{scope}/theme', '(Default)', type=str))
            control.blockSignals(False)
            control.setEnabled(not coordinated)
        for (scope, role), control in self.color_controls.items():
            control.set_color(color_value(self.settings, scope, role))
            control.setEnabled(not coordinated)



    def _apply_status_bar(self, visible):

        self.window.statusBar().setVisible(visible)



    def _apply_target(self, value):

        self.settings.setValue("Mouse/connect_target", value)



    def _apply_beep(self, enabled):

        self.settings.setValue("Misc/beep", enabled)



    def _refresh_font_previews(self):
        from .font_options import display_font, font_description
        for scope, label in self.font_previews.items():
            font = display_font(self.settings, scope)
            label.setFont(font)
            label.setText(font_description(font))

    def _choose_font(self, scope='application'):
        from .font_options import display_font
        font, accepted = FontPicker.getFont(
            display_font(self.settings, scope), self, 'Change font')
        if accepted:
            from .font_zoom import reset_zoom
            reset_zoom(self.settings, scope)
            self.settings.setValue('Display/font_' + scope, font.toString())
            self.window.apply_fonts()
            self._refresh_font_previews()

    def _reset_fonts(self):
        from .font_options import SCOPES
        from .font_zoom import reset_zoom
        reset_zoom(self.settings)
        for scope in SCOPES: self.settings.remove('Display/font_' + scope)
        self.settings.remove('Display/font')
        self.window.apply_fonts()
        self._refresh_font_previews()

    def _set_display_color(self, scope, role, value):
        self.settings.setValue(f'Colors/{scope}/{role}', value)
        if scope != 'log':
            theme = self.color_themes[scope]
            theme.blockSignals(True)
            theme.setCurrentText('Custom')
            theme.blockSignals(False)
            self.settings.setValue(f'Colors/{scope}/theme', 'Custom')
        self.window.apply_colors()

    def _set_color_theme(self, scope, theme):
        from .color_options import LIST_DEFAULTS
        self.settings.setValue(f'Colors/{scope}/theme', theme)
        if theme == 'Custom': return
        values = dict(LIST_DEFAULTS[scope])
        if theme in ('Classic', 'Light', 'Midnight', 'Dark Grey', 'Black'):
            from .themes import PALETTES
            p = PALETTES[theme]
            values.update(even=p['base'], odd=p['alternate'], selection=p['selection'], text=p['text'])
        else: values['text'] = '#17202a'
        for role, value in values.items():
            self.settings.setValue(f'Colors/{scope}/{role}', value)
            if (scope, role) in self.color_controls: self.color_controls[(scope, role)].set_color(value)
        self.window.apply_colors()

    def _choose_color(self):

        current = QColor(self._value("Display/selection_color", "#254b57"))

        color = QColorDialog.getColor(current, self, "Selection color")

        if color.isValid():

            self.settings.setValue("Display/selection_color", color.name())

            self.window.apply_selection_color(color.name())

