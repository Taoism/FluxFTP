from types import SimpleNamespace

import pytest

from fluxftp.connection_model import Entry, Site
from fluxftp.connection_operations import ConnectionOperationsMixin
from fluxftp.core import Connection


def test_list_marks_shortcuts_as_directories_and_applies_timezone(monkeypatch):
    connection = Connection(Site(timezone="UTC+05:30"))
    entry = Entry("+Latest", False, modified="20240101120000")
    monkeypatch.setattr(connection, "_list_ftp", lambda _path: [entry])

    result = connection.list("/")

    assert result[0].is_dir
    assert result[0].modified.startswith("2024-01-01 17:30")
    assert not entry.is_dir  # Listing normalization returns a copy.


def test_passive_listing_fallback_restores_passive_mode(monkeypatch):
    connection = Connection(Site(passive=True), passive_fallback=True)
    connection.ftp = SimpleNamespace(set_pasv=lambda value: modes.append(value))
    modes = []
    calls = []

    def listing(_path, allow_mlsd=True):
        calls.append(allow_mlsd)
        if len(calls) == 1:
            raise OSError("passive data connection failed")
        return [Entry("file.bin", False, 4)]

    monkeypatch.setattr(connection, "_list_ftp", listing)
    assert connection.list("/")[0].name == "file.bin"
    assert calls == [True, False]
    assert modes == [False, True]


def test_list_parser_error_does_not_leave_ftp_in_changed_directory():
    class FakeFTP:
        def __init__(self):
            self.current = "/before"
        def pwd(self):
            return self.current
        def cwd(self, path):
            self.current = path
        def retrlines(self, _command, callback):
            callback("not a recognized listing")

    connection = Connection(Site(), use_mlsd=False)
    connection.ftp = FakeFTP()
    with pytest.raises(ValueError, match="Unsupported LIST"):
        connection._list_ftp("/target")
    assert connection.ftp.current == "/before"


def test_sftp_mutations_validate_inputs_and_route_operations():
    calls = []
    sftp = SimpleNamespace(
        mkdir=lambda path: calls.append(("mkdir", path)),
        rename=lambda old, new: calls.append(("rename", old, new)),
        chmod=lambda path, mode: calls.append(("chmod", path, mode)),
        stat=lambda path: SimpleNamespace(st_atime=10, st_mtime=20, st_size=30),
        utime=lambda path, times: calls.append(("utime", path, times)),
        remove=lambda path: calls.append(("remove", path)),
        rmdir=lambda path: calls.append(("rmdir", path)),
    )
    connection = Connection(Site())
    connection.sftp = sftp

    connection.mkdir("/folder")
    connection.rename("/old", "/new")
    connection.chmod("/file", 0o640)
    connection.set_modified("/file", 50)
    connection.remove("/file")
    connection.remove("/folder", is_dir=True)
    assert connection.size("/file") == 30
    assert calls == [
        ("mkdir", "/folder"), ("rename", "/old", "/new"),
        ("chmod", "/file", 0o640), ("utime", "/file", (10, 50)),
        ("remove", "/file"), ("rmdir", "/folder"),
    ]
    with pytest.raises(ValueError, match="Permissions"):
        connection.chmod("/file", 0o10000)
    with pytest.raises(ValueError, match="control characters"):
        connection.remove("/bad\npath")


def test_stat_listing_parses_multiline_reply_and_strips_reply_codes():
    class FakeFTP:
        def sendcmd(self, command):
            assert command == "STAT /folder"
            return "213-Status follows\r\n213 -rw-r--r-- 1 user group 7 Jan 02 2025 report.txt\r\n213 End"

    connection = Connection(Site(), list_mode="STAT")
    connection.ftp = FakeFTP()
    entries = connection._list_ftp("/folder")
    assert [(entry.name, entry.size) for entry in entries] == [("report.txt", 7)]
