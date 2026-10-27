import os
import time
from dataclasses import replace
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from fluxftp.core import Connection, Site, Cancelled, transfer_tree
from fluxftp.transfer_options import TransferOptions, RateLimiter
from test_integration import servers


@pytest.mark.parametrize("tls", [False, True])
def test_ascii_upload_download_and_auto_binary(servers, tmp_path, tls):
    root, site = servers(tls)
    source = tmp_path / "text.txt"
    source.write_bytes(b"first\nsecond\r\nthird\n")
    with Connection(site, "password", transfer_options=TransferOptions(mode="ASCII")) as connection:
        commands = []
        original = connection.ftp.putcmd
        def record(command):
            commands.append(command)
            original(command)
        connection.ftp.putcmd = record
        connection.transfer(source, "/text.txt", True, Event(), lambda *_: None, resume=True)
        assert (root / "text.txt").read_bytes().splitlines() == [b"first", b"second", b"third"]
        target = tmp_path / "received.txt"
        connection.transfer(target, "/text.txt", False, Event(), lambda *_: None, resume=True)
        assert target.read_bytes() == os.linesep.encode().join([b"first", b"second", b"third", b""])
        assert commands.count("TYPE A") >= 2
        assert not any(command.startswith("REST") for command in commands)
    source.write_bytes(b"binary\x00\r\n\xff")
    with Connection(site, "password", transfer_options=TransferOptions(mode="Auto")) as connection:
        connection.transfer(source, "/raw.bin", True, Event(), lambda *_: None)
    assert (root / "raw.bin").read_bytes() == source.read_bytes()


def test_resume_rolls_back_corrupted_partial_tail(servers, tmp_path):
    root, site = servers(False)
    payload = bytes(range(256)) * 400
    (root / "file.bin").write_bytes(payload)
    target = tmp_path / "file.bin"
    target.with_name("file.bin.fluxpart").write_bytes(payload[:40000] + b"!" * 1000)
    with Connection(site, "password", transfer_options=TransferOptions(rollback_kib=2)) as connection:
        connection.transfer(target, "/file.bin", False, Event(), lambda *_: None, resume=True)
    assert target.read_bytes() == payload


def test_timeout_reconnects_and_resumes_partial(servers, tmp_path, monkeypatch):
    root, site = servers(False)
    payload = b"correct content" * 4000
    (root / "file.bin").write_bytes(payload)
    target = tmp_path / "file.bin"
    with Connection(site, "password") as connection:
        original = connection._transfer_once
        calls = []
        def stall_once(local, remote, upload, cancel, progress, resume):
            calls.append(resume)
            if len(calls) == 1:
                target.with_name("file.bin.fluxpart").write_bytes(payload[:1000])
                raise TimeoutError("data stalled")
            return original(local, remote, upload, cancel, progress, resume)
        monkeypatch.setattr(connection, "_transfer_once", stall_once)
        connection.transfer(target, "/file.bin", False, Event(), lambda *_: None)
    assert calls == [False, True]
    assert target.read_bytes() == payload


def test_stall_retries_are_bounded(servers, tmp_path, monkeypatch):
    _, site = servers(False)
    with Connection(site, "password") as connection:
        attempts = []
        def always_stall(*args):
            attempts.append(True)
            raise TimeoutError("still stalled")
        monkeypatch.setattr(connection, "_transfer_once", always_stall)
        with pytest.raises(TimeoutError):
            connection.transfer(tmp_path / "file", "/file", False, Event(), lambda *_: None)
    assert len(attempts) == 3


def test_force_active_buffers_and_local_limit_bypass(servers, tmp_path):
    root, site = servers(False)
    (root / "file").write_bytes(b"content")
    consumed = []
    limiter = SimpleNamespace(consume=lambda count, cancel: consumed.append(count))
    options = TransferOptions(force_active=True, send_kib=64, receive_kib=64)
    with Connection(site, "password", transfer_options=options, download_limiter=limiter) as connection:
        original = connection.ftp.ntransfercmd
        observed = []
        def capture(*args, **kwargs):
            observed.append(connection.ftp.passiveserver)
            data, size = original(*args, **kwargs)
            assert data.gettimeout() == 120
            return data, size
        connection.ftp.ntransfercmd = capture
        connection.transfer(tmp_path / "file", "/file", False, Event(), lambda *_: None)
        assert observed == [False]
        assert connection.ftp.passiveserver
        assert connection.ftp.send_buffer == connection.ftp.receive_buffer == 65536
    assert not consumed
    with Connection(site, "password", transfer_options=replace(options, bypass_local=False), download_limiter=limiter) as connection:
        connection.transfer(tmp_path / "other", "/file", False, Event(), lambda *_: None)
    assert sum(consumed) == len(b"content")


def test_shared_speed_budget_and_cancellation():
    limiter = RateLimiter()
    limiter.configure(100 * 1024)
    started = time.monotonic()
    workers = [Thread(target=limiter.consume, args=(10 * 1024, Event())) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(2)
    assert all(not worker.is_alive() for worker in workers)
    assert time.monotonic() - started >= 0.18
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled):
        limiter.consume(1024, cancel)


def test_disk_reserve_preserves_existing_destination(servers, tmp_path, monkeypatch):
    from fluxftp import transfer_options
    root, site = servers(False)
    (root / "file").write_bytes(b"new data")
    target = tmp_path / "file"
    target.write_bytes(b"existing")
    monkeypatch.setattr(transfer_options.shutil, "disk_usage", lambda _: SimpleNamespace(free=1024))
    with Connection(site, "password", transfer_options=TransferOptions(free_mib=1)) as connection:
        with pytest.raises(OSError, match="free"):
            connection.transfer(target, "/file", False, Event(), lambda *_: None)
    assert target.read_bytes() == b"existing"
    assert not target.with_name("file.fluxpart").exists()


def test_recursive_filename_conversion_and_collision(servers, tmp_path):
    root, site = servers(False)
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "Mixed.TXT").write_bytes(b"data")
    options = TransferOptions(upload_case="Lowercase")
    with Connection(site, "password", transfer_options=options) as connection:
        transfer_tree(None, connection, str(folder), "/target", True, Event(), lambda *_: None)
    assert (root / "target" / "mixed.txt").read_bytes() == b"data"
    # A remote case-sensitive listing can contain names that conversion collapses.
    from fluxftp.core import Entry
    remote = SimpleNamespace(transfer_options=TransferOptions(download_case="Lowercase"),
                             list=lambda _: [Entry("A.txt", False), Entry("a.txt", False)])
    with pytest.raises(ValueError, match="overwrite"):
        transfer_tree(remote, None, "/", str(tmp_path / "download"), True, Event(), lambda *_: None)


def test_transfer_controls_and_queue_limits(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QGroupBox, QTreeWidgetItem
    from fluxftp import app as app_module
    from fluxftp.preferences import PreferencesDialog
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(app_module, "settings_store", lambda: settings)
    window = app_module.Window()
    dialog = PreferencesDialog(window)
    page = dialog.pages.widget(dialog.page_by_name["Transfer"])
    assert [g.title() for g in page.findChildren(QGroupBox)] == ["Speed Limit", "Filename Convert", "Uploading and Downloading", "TCP/IP Buffer Size", "Transfer Reliability"]
    choices = [[box.itemText(i) for i in range(box.count())] for box in page.findChildren(QComboBox)]
    assert ["Binary", "ASCII", "Auto"] in choices
    assert ["Idle", "Disconnect", "Close FluxFTP"] in choices
    dialog.transfer_download_limit.setChecked(True)
    dialog.transfer_download_speed.setValue(500)
    for box in page.findChildren(QCheckBox):
        if box.text() == "Force Active Mode for Uploading and Downloading": box.setChecked(True)
    connection = window.new_connection(Site(host="example.test"), "")
    assert connection.transfer_options.force_active
    assert window.download_limiter.rate == 500 * 1024
    workspace = window.tabs.currentWidget()
    job = {"row": QTreeWidgetItem(["file"]), "task": lambda _: None, "route": "Copy",
           "workspace": workspace, "source_site": None, "destination_site": None}
    settings.setValue("Transfer/per_tab", 1)
    window.active["existing"] = window._transfer_record(job)
    assert not window._can_start_transfer(job)
    settings.setValue("Transfer/per_tab", 10)
    assert window._can_start_transfer(job)
    settings.setValue("Transfer/single_connection", True)
    assert not window._can_start_transfer(job)
    window.active.clear()
    dialog.close()
    window.close()


@pytest.mark.parametrize("action,failed", [("Idle", False), ("Disconnect", False), ("Close FluxFTP", False), ("Close FluxFTP", True)])
def test_completion_action_waits_for_successful_queue(tmp_path, monkeypatch, action, failed):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QTreeWidgetItem
    from PySide6.QtTest import QTest
    from fluxftp import app as app_module
    from test_ui import settle
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "completion.ini"), QSettings.Format.IniFormat)
    settings.setValue("Transfer/completed", action)
    monkeypatch.setattr(app_module, "settings_store", lambda: settings)
    window = app_module.Window()
    panes = window.current_panes()
    for pane in panes:
        pane.set_remote(Site(host="example.test"), "secret", "/")
        monkeypatch.setattr(pane, "refresh", lambda: None)
    closed = []
    original_close = window.close
    monkeypatch.setattr(window, "close", lambda: closed.append(True))
    def task(worker):
        if failed:
            raise OSError("failed transfer")
    row = QTreeWidgetItem(["file", "", "", "", "", "", "", ""])
    window.queue.addTopLevelItem(row)
    window.pending.append({"row": row, "task": task, "route": "Copy", "workspace": window.tabs.currentWidget(),
                           "source_site": None, "destination_site": None})
    window.start_next()
    settle(window)
    QTest.qWait(150)
    assert bool(closed) == (action == "Close FluxFTP" and not failed)
    assert all(pane.site is None for pane in panes) == (action == "Disconnect" and not failed)
    original_close()
