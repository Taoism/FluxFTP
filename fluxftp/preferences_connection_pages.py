"""Connection and proxy preference page construction and profile helpers."""
import json
import uuid

from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QWidget, QInputDialog, QMessageBox,
)


class PreferencesConnectionPages:
    """Build and manage connection-related preference pages."""

    CONNECTION_PAGES = {
        "Ident", "List Mode", "Cloud Accounts", "Key Manager", "SSL",
        "Connection", "Data connection", "Keep Alive", "Proxy Server",
    }

    def _populate_connection_page(self, title, form, description):
        if title == "Ident":
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
            self.data_smart_nat = self._check(form, "Connection/smart_pasv", "Smart handle FTP server behind NAT", False)
            self.data_bind_sockets = self._check(form, "Connection/bind_sockets", "Bind sockets to an IP Address",
                                                 self._value("Connection/bind_local_ip", False, bool))
            self.data_bind_local, self.data_local_ip = self._checked_field(
                form, "Connection/bind_local_ip", "Bind Local IP address to:",
                "Connection/local_ip", "", "", False)
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

        return title in self.CONNECTION_PAGES

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

