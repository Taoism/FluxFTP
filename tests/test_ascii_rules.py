import io
import json
import os
from threading import Event

import pytest

from fluxftp.ascii_rules import matches, read_rules, validate_rule, NormalizedReader
from fluxftp.core import Connection
from fluxftp.transfer_options import TransferOptions
from test_integration import servers


def test_ascii_masks_regex_and_filename_matching():
    rules = [validate_rule("*.c"), validate_rule(r"^README(?:\.[^.]+)?$", True)]
    assert matches("/folder/PROGRAM.C", rules)
    assert matches("/folder/readme.md", rules)
    assert not matches("/README/folder.bin", rules)
    assert not matches("program.cpp", rules)
    options = TransferOptions(mode="Auto", ascii_rules=json.dumps(rules))
    assert options.ascii("README") and not options.ascii("file.txt")
    assert not TransferOptions(mode="Auto", ascii_rules="[]").ascii("file.txt")
    assert TransferOptions(mode="Auto").ascii("index.asp")
    assert read_rules("", ".custom;.TXT") == [validate_rule("*.custom"), validate_rule("*.TXT")]
    for pattern in ("", "\n", "x" * 513):
        with pytest.raises(ValueError): validate_rule(pattern)
    with pytest.raises(ValueError, match="regular expression"):
        validate_rule("[", True)
    with pytest.raises(ValueError): read_rules('{"pattern":"*.txt"}')


def test_normalized_reader_handles_split_crlf():
    data = b"a" * 16383 + b"\r\nsecond\r\nlast\r"
    reader = NormalizedReader(io.BytesIO(data))
    result = b""
    while block := reader.read(7):
        result += block
    assert result == data.replace(b"\r\n", b"\n")


@pytest.mark.parametrize("tls", [False, True])
def test_ascii_resume_upload_verifies_then_appends(servers, tmp_path, tls):
    root, site = servers(tls)
    source = tmp_path / "text.txt"
    source.write_bytes(b"first\r\nsecond\nthird\r\n")
    (root / "text.txt").write_bytes(b"first" + os.linesep.encode() + b"sec")
    options = TransferOptions(mode="Auto", ascii_no_resume=False)
    with Connection(site, "password", transfer_options=options) as connection:
        commands = []
        putcmd = connection.ftp.putcmd
        def record(command):
            commands.append(command)
            putcmd(command)
        connection.ftp.putcmd = record
        connection.transfer(source, "/text.txt", True, Event(), lambda *_: None, resume=True)
        assert "APPE /text.txt" in commands
        assert not any(command.startswith("REST") for command in commands)
    assert (root / "text.txt").read_bytes().splitlines() == [b"first", b"second", b"third"]


def test_ascii_resume_upload_refuses_mismatched_prefix(servers, tmp_path):
    root, site = servers(False)
    source = tmp_path / "text.txt"
    source.write_bytes(b"source content\n")
    (root / "text.txt").write_bytes(b"unrelated content")
    with Connection(site, "password", transfer_options=TransferOptions(mode="ASCII", ascii_no_resume=False)) as connection:
        with pytest.raises(ValueError, match="matching prefix"):
            connection.transfer(source, "/text.txt", True, Event(), lambda *_: None, resume=True)
    assert (root / "text.txt").read_bytes() == b"unrelated content"


@pytest.mark.parametrize("matching", [True, False])
def test_ascii_download_resume_checks_converted_prefix(servers, tmp_path, matching):
    root, site = servers(False)
    (root / "text.txt").write_bytes(b"first\nsecond\nthird\n")
    target = tmp_path / "text.txt"
    target.write_bytes(b"original destination")
    partial = tmp_path / "text.txt.fluxpart"
    partial.write_bytes((b"first" + os.linesep.encode() + b"sec") if matching else b"bad prefix")
    options = TransferOptions(mode="ASCII", ascii_no_resume=False, rollback_kib=0)
    with Connection(site, "password", transfer_options=options) as connection:
        if matching:
            connection.transfer(target, "/text.txt", False, Event(), lambda *_: None, resume=True)
        else:
            with pytest.raises(ValueError, match="does not match"):
                connection.transfer(target, "/text.txt", False, Event(), lambda *_: None, resume=True)
    if matching:
        assert target.read_bytes() == os.linesep.encode().join([b"first", b"second", b"third", b""])
    else:
        assert target.read_bytes() == b"original destination"
        assert partial.read_bytes() == b"bad prefix"


def test_ascii_list_add_edit_delete_apply_and_cancel(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QApplication
    from fluxftp import app as app_module
    from fluxftp.preferences import PreferencesDialog
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "ascii.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(app_module, "settings_store", lambda: settings)
    window = app_module.Window()
    dialog = PreferencesDialog(window)
    assert dialog.ascii_no_resume.isChecked()
    assert dialog.ascii_list.item(0).text() == "*.asp"
    count = dialog.ascii_list.count()
    monkeypatch.setattr(dialog, "_ascii_rule_dialog", lambda *_: validate_rule(r"^README$", True))
    dialog._ascii_edit_rule(False)
    assert dialog.ascii_list.count() == count + 1
    assert not dialog.ascii_list.item(count).icon().isNull()
    assert dialog.ascii_list.item(count).data(Qt.ItemDataRole.UserRole)["regex"]
    dialog.ascii_no_resume.setChecked(False)
    dialog._apply_ascii_rules()
    saved = settings.value("Transfer/ascii_rules")
    monkeypatch.setattr(dialog, "_ascii_rule_dialog", lambda *_: validate_rule("*.special"))
    dialog._ascii_edit_rule(True)
    assert dialog.ascii_list.item(count).text() == "*.special"
    assert dialog.ascii_list.item(count).icon().isNull()
    dialog.reject()
    assert settings.value("Transfer/ascii_rules") == saved
    dialog = PreferencesDialog(window)
    assert not dialog.ascii_no_resume.isChecked()
    assert dialog.ascii_list.item(count).text() == "^README$"
    dialog.ascii_list.setCurrentRow(count)
    dialog._ascii_delete_rules()
    dialog.accept()
    assert len(read_rules(settings.value("Transfer/ascii_rules"))) == count
    window.close()
