import socket
import threading
import pytest
from fluxftp import core
from fluxftp.core import Connection, Site, open_proxy_socket
from test_integration import servers


def _serve_proxy(handshake):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    captured = {}

    def run():
        try:
            conn, _ = listener.accept()
            with conn:
                handshake(conn, captured)
                data = conn.recv(4)
                captured["payload"] = data
                conn.sendall(data)
        finally:
            listener.close()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, captured, thread


def test_http_connect_proxy_tunnels_target_without_local_dns():
    def handshake(conn, captured):
        request = bytearray()
        while b"\r\n\r\n" not in request:
            request.extend(conn.recv(1024))
        captured["request"] = bytes(request)
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

    port, captured, thread = _serve_proxy(handshake)
    with open_proxy_socket("destination.invalid", 990, 2, {
        "type": "HTTP CONNECT", "host": "127.0.0.1", "port": port,
        "username": "proxy-user", "password": "proxy-secret",
    }) as tunnel:
        tunnel.sendall(b"ping")
        assert tunnel.recv(4) == b"ping"
    thread.join(2)
    request = captured["request"]
    assert b"CONNECT destination.invalid:990 HTTP/1.1" in request
    assert b"proxy-user" not in request
    assert b"proxy-secret" not in request
    assert b"Proxy-Authorization: Basic " in request


def test_socks5_proxy_keeps_target_hostname_for_remote_dns():
    def handshake(conn, captured):
        assert conn.recv(3) == b"\x05\x01\x00"
        conn.sendall(b"\x05\x00")
        request = conn.recv(4)
        size = conn.recv(1)[0]
        host = conn.recv(size)
        port = int.from_bytes(conn.recv(2), "big")
        captured["target"] = (request, host.decode(), port)
        conn.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50")

    port, captured, thread = _serve_proxy(handshake)
    with open_proxy_socket("remote.example", 2121, 2, {
        "type": "SOCKS5", "host": "127.0.0.1", "port": port,
    }) as tunnel:
        tunnel.sendall(b"ping")
        assert tunnel.recv(4) == b"ping"
    thread.join(2)
    assert captured["target"] == (b"\x05\x01\x00\x03", "remote.example", 2121)


def test_connect_retries_only_during_connection_setup(monkeypatch):
    attempts = []
    delays = []
    connection = Connection(Site(host="example.test"), max_retries=2, retry_delay=7)

    def fail_twice_then_connect():
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("not ready")

    monkeypatch.setattr(connection, "_connect", fail_twice_then_connect)
    monkeypatch.setattr(core.time, "sleep", delays.append)
    connection.connect()
    assert len(attempts) == 3
    assert delays == [7, 7]


def test_proxy_password_is_saved_when_options_dialog_closes(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QLineEdit
    from fluxftp.app import Window
    from fluxftp.preferences import PreferencesDialog
    from fluxftp.credentials import CredentialVault
    import fluxftp.credentials as credentials

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(credentials, "_key", lambda create=False: b"\x22" * 32)
    window = Window()
    window.credential_vault = CredentialVault(tmp_path / "credentials.enc")
    window.preferences.clear()
    options = PreferencesDialog(window)
    options.navigation.setCurrentItem(options.navigation.topLevelItem(1).child(2))
    password = next(widget for widget in options.findChildren(QLineEdit)
                    if widget.echoMode() == QLineEdit.EchoMode.Password)
    password.setText("close-path-secret")

    # Exercise the dialog's close callback directly, without relying on editingFinished.
    options.done(0)

    encrypted = (tmp_path / "credentials.enc").read_bytes()
    assert b"close-path-secret" not in encrypted
    assert window.credential_vault.get("proxy") == "close-path-secret"
    window.close()



def test_proxy_profiles_switch_and_encrypt_each_password(tmp_path, monkeypatch):
    import json
    from PySide6.QtWidgets import QApplication, QLineEdit, QInputDialog
    from fluxftp.app import Window
    from fluxftp.preferences import PreferencesDialog
    from fluxftp.credentials import CredentialVault
    import fluxftp.credentials as credentials

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(credentials, "_key", lambda create=False: b"\x44" * 32)
    window = Window()
    class MemorySettings:
        def __init__(self):
            self.values = {}
        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            return type(value) if type and value is not None else value
        def setValue(self, key, value):
            self.values[key] = value
        def sync(self):
            pass
    window.preferences = MemorySettings()
    window.credential_vault = CredentialVault(tmp_path / "credentials.enc")
    options = PreferencesDialog(window)
    options.navigation.setCurrentItem(options.navigation.topLevelItem(1).child(2))

    fields = options._proxy_profile_fields
    fields["enabled"].setChecked(True)
    fields["host"].setText("proxy-one.example")
    fields["auth"].setChecked(True)
    fields["username"].setText("one-user")
    fields["password"].setText("one-secret")
    options._save_proxy_profile()

    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Office", True))
    options._add_proxy_profile()
    fields = options._proxy_profile_fields
    fields["host"].setText("proxy-two.example")
    fields["auth"].setChecked(True)
    fields["username"].setText("two-user")
    fields["password"].setText("two-secret")
    options._save_proxy_profile()

    assert options.proxy_profile_combo.count() == 2
    options.proxy_profile_combo.setCurrentIndex(0)
    assert fields["host"].text() == "proxy-one.example"
    assert fields["password"].text() == "one-secret"
    options.proxy_profile_combo.setCurrentIndex(1)
    assert fields["host"].text() == "proxy-two.example"
    assert fields["password"].text() == "two-secret"

    proxy = window.new_connection(Site(host="ftp.example.test"), "site-secret").proxy
    assert proxy["host"] == "proxy-two.example"
    assert proxy["username"] == "two-user"
    assert proxy["password"] == "two-secret"
    profiles = json.loads(window.preferences.value("Connection/proxy_profiles", "", type=str))
    assert len(profiles) == 2
    office_id = profiles[1]["id"]
    assert window.credential_vault.get("proxy") == "one-secret"
    assert window.credential_vault.get(f"proxy:{office_id}") == "two-secret"
    encrypted = (tmp_path / "credentials.enc").read_bytes()
    assert b"one-secret" not in encrypted and b"two-secret" not in encrypted
    options.close()
    window.close()


def test_proxy_password_save_failure_does_not_block_dialog_close(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QLineEdit
    from fluxftp.app import Window
    from fluxftp.preferences import PreferencesDialog
    from fluxftp.credentials import CredentialVault
    import fluxftp.credentials as credentials

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(credentials, "_key", lambda create=False: b"\x33" * 32)
    window = Window()
    window.credential_vault = CredentialVault(tmp_path / "credentials.enc")
    window.preferences.clear()
    options = PreferencesDialog(window)
    options.navigation.setCurrentItem(options.navigation.topLevelItem(1).child(2))
    password = next(widget for widget in options.findChildren(QLineEdit)
                    if widget.echoMode() == QLineEdit.EchoMode.Password)
    password.setText("do-not-block-secret")

    def fail_to_save(_password):
        raise AttributeError("simulated secure storage failure")

    window.set_proxy_password = fail_to_save
    options.done(0)

    assert options.result() == 0
    assert "could not be saved" in window.statusBar().currentMessage()
    window.close()


def test_proxy_password_is_written_only_to_encrypted_vault(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from fluxftp.app import Window
    from fluxftp.preferences import PreferencesDialog
    from fluxftp.credentials import CredentialVault
    import fluxftp.credentials as credentials

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(credentials, "_key", lambda create=False: b"\x11" * 32)
    window = Window()
    window.credential_vault = CredentialVault(tmp_path / "credentials.enc")
    window.preferences.clear()
    options = PreferencesDialog(window)
    options.navigation.setCurrentItem(options.navigation.topLevelItem(1).child(2))
    password = next(widget for widget in options.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit)
                    if widget.echoMode() == widget.EchoMode.Password)
    password.setText("proxy-session-secret")
    assert window.proxy_password == "proxy-session-secret"
    assert window.preferences.value("Connection/proxy_password", "") == ""
    password.editingFinished.emit()
    encrypted = (tmp_path / "credentials.enc").read_bytes()
    assert b"proxy-session-secret" not in encrypted
    assert window.credential_vault.get("proxy") == "proxy-session-secret"
    options.close()
    window.close()


def test_changing_data_folder_migrates_and_merges_saved_sites(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from fluxftp.app import Window

    app = QApplication.instance() or QApplication([])
    window = Window()
    window.preferences.clear()
    class MemorySettings:
        def __init__(self):
            self.values = {}
        def setValue(self, key, value):
            self.values[key] = value
        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            return type(value) if type and value is not None else value
        def sync(self):
            pass
    window.preferences = MemorySettings()
    window.sites = {"current": {"host": "current.example"}}
    destination = tmp_path / "fluxftp-data"
    destination.mkdir()
    (destination / "sites.json").write_text('{"existing": {"host": "existing.example"}}', "utf-8")
    assert window.change_data_folder(destination)
    assert set(window.sites) == {"existing", "current"}
    assert set(__import__("json").loads((destination / "sites.json").read_text("utf-8"))) == {"existing", "current"}
    assert window.preferences.value("General/data_path", "", type=str) == str(destination)
    window.close()



def test_proxy_forces_passive_mode_for_active_profile(monkeypatch, caplog):
    modes = []

    class FakeSocket:
        def getpeername(self):
            return ("127.0.0.1", 21)
        def settimeout(self, _timeout):
            pass

    class FakeFTP:
        def __init__(self, *_args, **_kwargs):
            from types import SimpleNamespace
            self.sock = FakeSocket()
            self.file = SimpleNamespace(encoding='utf-8')
            self.smart_pasv = False
        def connect(self, *_args, **_kwargs):
            pass
        def login(self, *_args):
            pass
        def set_pasv(self, value):
            modes.append(value)

    monkeypatch.setattr(core, "LoggedFTP", FakeFTP)
    site = Site(host="example.test", protocol=core.PROTOCOLS[4], passive=False)
    connection = Connection(site, proxy={"type": "SOCKS5", "host": "proxy.test", "port": 1080})
    connection._connect()
    assert modes == [True]
    assert site.passive is False  # The imported/saved site setting is preserved.
    assert "Using passive data mode" in caplog.text


def test_passive_listing_fallback_retries_once_in_active_mode(monkeypatch):
    connection = Connection(Site(host="example.test", passive=True), passive_fallback=True)
    modes = []

    class FakeFTP:
        def set_pasv(self, value):
            modes.append(value)

    connection.ftp = FakeFTP()
    calls = []
    def listing(path, allow_mlsd=True):
        calls.append((path, allow_mlsd))
        if len(calls) == 1:
            raise OSError("PASV data connection failed")
        return []
    monkeypatch.setattr(connection, "_list_ftp", listing)
    assert connection.list("/") == []
    assert modes == [False, True]
    assert calls == [("/", True), ("/", False)]


def test_window_new_connection_uses_proxy_and_transport_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from fluxftp import app as app_module
    from fluxftp.app import Window

    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'proxy-settings.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(app_module, 'settings_store', lambda: settings)
    window = Window()
    class MemorySettings:
        values = {
            "Connection/proxy_enabled": True,
            "Connection/proxy_type": "SOCKS5",
            "Connection/proxy_host": "proxy.example",
            "Connection/proxy_port": 1080,
            "Connection/proxy_auth": True,
            "Connection/proxy_username": "ftp-proxy-user",
            "Connection/max_retries": 4,
            "Connection/retry_delay": 9,
            "Connection/passive_fallback": True,
            "Connection/tcp_keepalive": True,
            "Connection/keepalive_interval": 45,
            "Connection/bind_local_ip": True,
            "Connection/local_ip": "127.0.0.1",
            "Connection/limit_local_ports": True,
            "Connection/local_port_range": "50000-50010",
            "Connection/limit_local_ports": True,
            "Connection/use_mlsd": False,
        }
        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            return type(value) if type and value is not None else value
        def setValue(self, key, value):
            self.values[key] = value
        def sync(self):
            pass
    window.preferences = MemorySettings()
    window.proxy_password = "session-only"
    connection = window.new_connection(Site(host="example.test", use_masq=False, force_utf8=True), "site-password")
    assert connection.proxy == {
        "type": "SOCKS5", "host": "proxy.example", "port": 1080,
        "username": "ftp-proxy-user", "password": "session-only",
    }
    assert connection.smart_pasv is False
    assert connection.site.force_utf8 is True
    assert connection.max_retries == 4
    assert connection.retry_delay == 9
    assert connection.passive_fallback
    assert connection.tcp_keepalive
    assert connection.keepalive_interval == 45
    assert connection.local_bind_ip == "127.0.0.1"
    assert connection.local_port_range == (50000, 50010)
    assert not connection.use_mlsd
    window.close()


def test_active_ftp_uses_configured_local_ip_and_port_range(servers):
    _, site = servers(False)
    site.passive = False
    with Connection(site, "password", local_bind_ip="127.0.0.1", local_port_range=(50000, 50100)) as connection:
        assert connection.list("/") == []


def test_connection_rejects_invalid_active_port_range():
    with pytest.raises(ValueError, match="port range"):
        Connection(Site(host="example.test"), local_port_range=(51000, 50000))


def test_site_timezone_formats_utc_mlsd_timestamps():
    from fluxftp.core import Entry
    connection = Connection(Site(host="example.test", timezone="UTC-05:00"))
    values = connection._apply_site_timezone([Entry("file", False, modified="20240115120000")])
    assert values[0].modified == "2024-01-15 07:00 UTC-05:00"
