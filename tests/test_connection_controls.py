import socket
from threading import Event

import pytest

from fluxftp.core import Connection, Site, PROTOCOLS, bypass_proxy
from fluxftp.diagnostics import Trace
from fluxftp.upnp import PortMapper
from test_integration import servers


@pytest.mark.parametrize("host,local,prefixes,expected", [
    ("192.168.1.20", True, "", True), ("172.20.1.2", True, "", True),
    ("172.32.1.2", True, "", False), ("127.0.0.1", True, "", True),
    ("::1", True, "", True), ("fd00::1", True, "", True),
    ("nas", True, "", True), ("example.com", True, "", False),
    ("example.com", False, "EXAMPLE.;192.168.", True),
    ("192.168.1.20", False, "", False),
])
def test_proxy_bypass_rules(host, local, prefixes, expected):
    assert bypass_proxy(host, local, prefixes) == expected


def test_connection_options_persist_and_bypass_auth(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from fluxftp import app as app_module
    from fluxftp.preferences import PreferencesDialog
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "options.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(app_module, "settings_store", lambda: settings)
    window = app_module.Window()
    dialog = PreferencesDialog(window)
    assert dialog.data_port_range.text() == ""
    assert not dialog.data_limit_ports.isChecked()
    assert not dialog.data_bind_local.isEnabled()
    dialog.data_bind_sockets.setChecked(True)
    assert dialog.data_bind_local.isEnabled() and not dialog.data_local_ip.isEnabled()
    connection = window.new_connection(Site(host="example.test"), "")
    assert connection.bind_sockets and connection.local_bind_ip == ""
    dialog.data_bind_local.setChecked(True)
    with pytest.raises(ValueError, match="Enter a local IP"):
        window.new_connection(Site(host="example.test"), "")
    dialog.data_local_ip.setText("127.0.0.1")
    dialog.data_local_ip.editingFinished.emit()
    dialog.data_limit_ports.setChecked(True)
    for invalid in ("", "1500-1400", "0-100", "1-65536", "nonsense"):
        dialog.data_port_range.setText(invalid)
        dialog.data_port_range.editingFinished.emit()
        with pytest.raises(ValueError):
            window.new_connection(Site(host="example.test"), "")
    dialog.data_port_range.setText("1400-1500")
    dialog.data_port_range.editingFinished.emit()
    dialog.general_upnp.setChecked(True)
    dialog.proxy_noop.setChecked(True)
    dialog.proxy_passive.setChecked(False)
    dialog.proxy_bypass_local.setChecked(True)
    dialog.proxy_exceptions.setText("ftp.internal.; 10.")
    dialog.proxy_exceptions.editingFinished.emit()
    settings.setValue("Connection/proxy_enabled", True)
    settings.setValue("Connection/proxy_auth", True)
    settings.setValue("Connection/proxy_host", "proxy.example")
    connection = window.new_connection(Site(host="ftp.internal.example"), "")
    assert connection.proxy is None
    assert connection.local_bind_ip == "127.0.0.1"
    assert connection.local_port_range == (1400, 1500)
    assert connection.upnp_enabled and connection.noop_during_transfer
    assert not connection.proxy_force_passive
    assert window.ensure_proxy_credentials(window, "127.0.0.1")
    dialog.close()
    dialog = PreferencesDialog(window)
    assert dialog.data_bind_sockets.isChecked() and dialog.data_bind_local.isChecked()
    assert dialog.data_port_range.text() == "1400-1500"
    assert dialog.general_upnp.isChecked() and dialog.proxy_noop.isChecked()
    dialog.close()
    window.close()


def test_auto_binding_for_passive_and_active_listings(servers):
    _, site = servers(False)
    for passive in (True, False):
        site.passive = passive
        with Connection(site, "password", bind_sockets=True) as connection:
            assert connection.ftp.source_address == ("127.0.0.1", 0)
            if not passive:
                with connection.ftp.makeport() as listener:
                    assert listener.getsockname()[0] == "127.0.0.1"
            assert connection.list("/") == []


def test_unavailable_active_port_is_reported(servers):
    _, site = servers(False)
    site.passive = False
    with socket.socket() as occupied:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        with Connection(site, "password", bind_sockets=True, local_port_range=(port, port)) as connection:
            with pytest.raises(OSError, match="No free active FTP port"):
                connection.list("/")


def test_proxy_passive_override_can_be_disabled():
    site = Site(host="example.test", protocol=PROTOCOLS[4], passive=False)
    with pytest.raises(ValueError, match="Active FTP through a proxy"):
        with Connection(site, proxy={"host": "proxy.example"}, proxy_force_passive=False):
            pass


def test_noop_transfer_replies_leave_control_channel_synchronized(servers, tmp_path):
    root, site = servers(False)
    payload = b"x" * (1024 * 1024)
    upload = tmp_path / "upload.bin"
    upload.write_bytes(payload)
    with Connection(site, "password", noop_during_transfer=True) as connection:
        commands = []
        putcmd = connection.ftp.putcmd
        def record(command):
            commands.append(command)
            putcmd(command)
        connection.ftp.putcmd = record
        def progress(*_):
            connection.ftp.last_transfer_noop = 0
        connection.transfer(upload, "/file.bin", True, Event(), progress)
        assert (root / "file.bin").read_bytes() == payload
        assert connection.ftp.pwd() == "/"
        assert "NOOP" in commands
        commands.clear()
        target = tmp_path / "download.bin"
        connection.transfer(target, "/file.bin", False, Event(), progress)
        assert target.read_bytes() == payload
        assert "NOOP" in commands
        assert connection.ftp.pwd() == "/"


class FakeGateway:
    def __init__(self, **_):
        self.mappings = {}
        self.deleted = []
    def discover(self): return 1
    def selectigd(self): return "gateway"
    def externalipaddress(self): return "127.0.0.1"
    def getspecificportmapping(self, port, protocol): return self.mappings.get(port)
    def addportmapping(self, port, protocol, host, internal, description, remote, lease):
        assert protocol == "TCP" and lease == 3600
        self.mappings[port] = (host, internal, description, True, lease)
        return True
    def deleteportmapping(self, port, protocol):
        self.deleted.append(port)
        self.mappings.pop(port)
        return True


def test_upnp_maps_active_listener_and_cleans_up(servers, monkeypatch):
    from fluxftp import upnp
    gateway = FakeGateway()
    monkeypatch.setattr(upnp.miniupnpc, "UPnP", lambda **_: gateway)
    _, site = servers(False)
    site.passive = False
    with Connection(site, "password", upnp_enabled=True) as connection:
        assert connection.list("/") == []
        assert gateway.mappings
    assert not gateway.mappings and gateway.deleted


def test_upnp_does_not_replace_or_delete_other_mappings(monkeypatch):
    from fluxftp import upnp
    gateway = FakeGateway()
    monkeypatch.setattr(upnp.miniupnpc, "UPnP", lambda **_: gateway)
    mapper = PortMapper(Trace())
    gateway.mappings[1400] = ("192.168.1.2", 1400, "Another app", True, 0)
    with pytest.raises(OSError, match="already has a mapping"):
        mapper.map("127.0.0.1", 1400)
    mapper.map("127.0.0.1", 1401)
    gateway.mappings[1401] = ("192.168.1.2", 1401, "Replaced", True, 0)
    mapper.close()
    assert not gateway.deleted


def test_upnp_failure_is_reported(monkeypatch):
    from fluxftp import upnp
    gateway = FakeGateway()
    def fail(): raise RuntimeError("No UPnP gateway")
    gateway.selectigd = fail
    monkeypatch.setattr(upnp.miniupnpc, "UPnP", lambda **_: gateway)
    with pytest.raises(OSError, match="No UPnP gateway"):
        PortMapper(Trace()).map("127.0.0.1", 1400)
