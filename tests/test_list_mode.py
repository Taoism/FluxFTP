import ftplib

import pytest

from fluxftp.core import Connection, Site, parse_list_entry
from test_integration import servers


@pytest.mark.parametrize("mode,custom,expected", [
    ("Default (LIST)", "LIST -al", "LIST"),
    ("Show Hidden Files", "LIST -al", "LIST -a"),
    ("Custom", "LIST -al", "LIST -al"),
    ("Custom", "LIST -a", "LIST -a"),
])
def test_list_commands_against_ftp_server(servers, mode, custom, expected):
    root, site = servers(False)
    (root / "file with spaces.txt").write_bytes(b"hello")
    (root / ".hidden").write_bytes(b"hidden")
    (root / "folder").mkdir()
    with Connection(site, "password", list_mode=mode, list_command=custom, use_mlsd=False) as connection:
        commands = []
        original = connection.ftp.retrlines
        def record(command, callback):
            commands.append(command)
            return original(command, callback)
        connection.ftp.retrlines = record
        entries = {entry.name: entry for entry in connection.list("/")}
        assert commands == [expected]
        assert entries["file with spaces.txt"].size == 5
        assert entries["folder"].is_dir
        assert ".hidden" in entries
        assert connection.ftp.pwd() == "/"


def test_mlsd_preferred_and_selected_list_used_when_unavailable(servers):
    root, site = servers(False)
    (root / "file").write_bytes(b"payload")
    with Connection(site, "password", list_mode="Default (LIST)") as connection:
        commands = []
        original = connection.ftp.retrlines
        def record(command, callback):
            commands.append(command)
            return original(command, callback)
        connection.ftp.retrlines = record
        assert connection.list("/")[0].name == "file"
        assert commands == ["MLSD /"]
        commands.clear()
        def unsupported(*args, **kwargs):
            raise ftplib.error_perm("502 MLSD unavailable")
        connection.ftp.mlsd = unsupported
        assert connection.list("/")[0].size == 7
        assert commands == ["LIST"]


def test_list_parser_preserves_names_and_detects_links():
    entry = parse_list_entry("-rw-r--r-- 1 user group 123 Sep 23 12:34 file with spaces.txt")
    assert entry.name == "file with spaces.txt" and entry.size == 123
    entry = parse_list_entry("lrwxrwxrwx 1 user group 6 Sep 23 2026 link -> target")
    assert entry.name == "link" and entry.is_link
    assert parse_list_entry("09-23-26  01:30PM       <DIR>          My Folder").is_dir
    assert parse_list_entry("09-23-2026  01:30PM       456 file.txt").size == 456
    assert parse_list_entry("total 2") is None
    assert parse_list_entry("drwxr-xr-x 1 user group 0 Sep 23 2026 ..") is None
    with pytest.raises(ValueError, match="Unsupported LIST"):
        parse_list_entry("unrecognized listing format")
    with pytest.raises(ValueError):
        parse_list_entry("-rw-r--r-- 1 user group 1 Sep 23 12:34 ../escape")
    with pytest.raises(ValueError, match="Custom listing command"):
        Connection(Site(), list_mode="Custom", list_command="LIST\r\nDELE file")


def test_list_options_layout_and_persistence(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from fluxftp import app as app_module
    from fluxftp.preferences import PreferencesDialog
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("Connection/list_mode", "LIST only")
    monkeypatch.setattr(app_module, "settings_store", lambda: settings)
    window = app_module.Window()
    dialog = PreferencesDialog(window)
    assert list(dialog.list_mode_buttons) == ["Default (LIST)", "Show Hidden Files", "Custom"]
    assert dialog.list_mode_buttons["Default (LIST)"].isChecked()
    assert not dialog.list_use_mlsd.isChecked()  # Preserve the previous forced-LIST choice.
    assert not dialog.custom_list_command.isEnabled()
    dialog.list_mode_buttons["Custom"].setChecked(True)
    assert dialog.custom_list_command.isEnabled()
    dialog.custom_list_command.setCurrentText("LIST -a")
    dialog.list_use_mlsd.setChecked(True)
    dialog.list_port_fallback.setChecked(False)
    dialog.close()
    dialog = PreferencesDialog(window)
    assert dialog.list_mode_buttons["Custom"].isChecked()
    assert dialog.custom_list_command.currentText() == "LIST -a"
    connection = window.new_connection(Site(host="example.test"), "")
    assert connection.list_mode == "Custom" and connection.list_command == "LIST -a"
    assert connection.use_mlsd and not connection.passive_fallback
    dialog.close()
    window.close()
