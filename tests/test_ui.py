import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog, QTreeWidgetItem, QFileDialog, QPushButton
from PySide6.QtTest import QTest

import fluxftp.app as app_module
from fluxftp.app import Window, STYLE, SiteDialog
from fluxftp.site_manager import SitesDialog, ImportDialog
from fluxftp.preferences import PreferencesDialog
from fluxftp.site_import import read_import
from fluxftp.core import Site, Connection, PROTOCOLS
from dataclasses import asdict
from PySide6.QtCore import Qt
from test_integration import servers


def settle(window):
    deadline = time.monotonic() + 10
    while window.workers or window.active or window.pending:
        QTest.qWait(10)
        assert time.monotonic() < deadline, "UI operation timed out"
    QTest.qWait(20)


def test_ui_prompts_then_pins_untrusted_ftps_certificate(servers, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    _, site = servers(True)
    site.name = "Self-signed test site"
    site.ca_file = ""

    class FakeButton:
        def __init__(self, text):
            self.text = text
            self.enabled = True
        def setEnabled(self, enabled):
            self.enabled = enabled

    class FakeMessageBox:
        class Icon:
            Warning = 1
        class ButtonRole:
            AcceptRole = 1
            RejectRole = 2
        def __init__(self, _parent):
            self.buttons = []
            self._clicked = None
            self._informative = ""
        def setIcon(self, *_args): pass
        def setWindowTitle(self, *_args): pass
        def setText(self, *_args): pass
        def setDetailedText(self, text): self.details = text
        def setInformativeText(self, text): self._informative = text
        def informativeText(self): return self._informative
        def addButton(self, text, _role):
            button = FakeButton(text)
            self.buttons.append(button)
            return button
        def exec(self): self._clicked = self.buttons[0]
        def clickedButton(self): return self._clicked

    monkeypatch.setattr(app_module, "QMessageBox", FakeMessageBox)
    window = Window()
    window.sites_file = tmp_path / "sites.json"
    assert window.save_sites({site.name: __import__("dataclasses").asdict(site)})
    results, failures = [], []
    def task(_worker):
        with Connection(site, "password") as connection:
            return connection.security
    window.run(task, results.append, failures.append)
    settle(window)

    assert not failures, failures
    assert results and "TLSv1." in results[0]
    saved = window.sites[site.name]
    assert saved["tls_cert_sha256"]
    assert saved["tls_pin_host"] == "127.0.0.1"
    assert saved["tls_pin_port"] == site.port
    assert saved["tls_pin_protocol"] == PROTOCOLS[0]
    window.close()


def test_ui_upload_queue_and_workspaces(servers, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    root, site = servers(True)
    local = tmp_path / "local"
    local.mkdir()
    (local / "queued.txt").write_bytes(b"queued via UI")
    window = Window()
    # Keep the transfer smoke test independent from this machine's saved proxy settings.
    monkeypatch.setattr(window, "new_connection", lambda site, password: Connection(site, password))
    window.show()
    left, right = window.tabs.currentWidget().widget(0), window.tabs.currentWidget().widget(1)
    assert left.mode_button.text() == right.mode_button.text() == "Remote"
    left.change_mode("Local")
    left.navigate(str(local))
    right.set_remote(site, "password", "/")
    right.refresh()
    settle(window)
    left.tree.topLevelItem(0).setSelected(True)
    # Exercise a pane-to-pane drop into the encrypted upload queue.
    from types import SimpleNamespace
    left.tree.drag_source = SimpleNamespace(mode=left.mode, site=left.site,
        password=left.password, busy=False, join=left.join)
    left.tree.drag_entries = left.selected()
    class PaneDrop:
        accepted = False
        def source(self): return left.tree
        def setDropAction(self, action): self.action = action
        def accept(self): self.accepted = True
        def ignore(self): self.accepted = False
    drop = PaneDrop()
    right.tree.dropEvent(drop)
    left.tree.drag_source = None
    assert drop.accepted and drop.action == Qt.DropAction.CopyAction
    settle(window)
    assert (root / "queued.txt").read_bytes() == b"queued via UI"
    assert window.queue.topLevelItem(0).text(7) == "Upload · Complete"
    assert right.tree.topLevelItemCount() == 1

    # Switch the right pane to another local folder and verify local-to-local copy.
    right.change_mode("Local")
    local_target = tmp_path / "local-target"
    local_target.mkdir()
    right.navigate(str(local_target))
    left.tree.topLevelItem(0).setSelected(True)
    window.enqueue(True)
    settle(window)
    assert (local_target / "queued.txt").read_bytes() == b"queued via UI"
    assert window.queue.topLevelItem(1).text(7) == "Copy · Complete"

    # Switching back restores the pane's last remote profile and path.
    right.change_mode("Remote")
    settle(window)
    assert right.site == site
    assert right.path == "/"
    window.add_workspace()
    assert window.tabs.count() == 2
    window.close_tab(1)
    assert window.tabs.count() == 1
    assert not errors
    window.close()


def test_sites_import_preview_cancel_and_save(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.sites = {"example.test": asdict(Site(name="example.test", host="original.test"))}
    window.sites_file = tmp_path / "config/sites.json"
    window.save_sites()
    before = window.sites_file.read_bytes()
    source = tmp_path / "sites.txt"
    source.write_text("ftpes://alice:SECRET@example.test/\nsftp://bob@second.test/", "utf-8")
    options = PreferencesDialog(window)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(ImportDialog, "exec", lambda self: 0)
    options._import_sites()
    assert window.sites_file.read_bytes() == before
    monkeypatch.setattr(ImportDialog, "exec", lambda self: 1)
    options._import_sites()
    assert set(window.sites) == {"example.test", "example.test (2)", "second.test"}
    assert "SECRET" not in window.sites_file.read_text("utf-8")
    manager = SitesDialog(window)
    manager.search.setText("second.test")
    visible = [manager.tree.topLevelItem(i) for i in range(manager.tree.topLevelItemCount()) if not manager.tree.topLevelItem(i).isHidden()]
    assert len(visible) == 1
    visible[0].setSelected(True)
    manager.connect_to(1)
    assert manager.connection == (1, "second.test")
    manager.close()
    window.close()


def test_import_selection_and_failed_save(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "sites.txt"
    source.write_text("sftp://one.test/\nsftp://two.test/", "utf-8")
    preview = ImportDialog(None, read_import(source))
    preview.check_all(Qt.CheckState.Unchecked)
    assert not preview.import_button.isEnabled()
    preview.tree.topLevelItem(1).setCheckState(0, Qt.CheckState.Checked)
    assert [site.host for site in preview.selected_sites()] == ["two.test"]
    preview.close()
    window = Window()
    window.sites = {"original": asdict(Site(name="original", host="old.test"))}
    window.sites_file = tmp_path / "not-a-directory" / "sites.json"
    window.sites_file.parent.write_text("file", "utf-8")
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[2]))
    assert not window.save_sites({})
    assert "original" in window.sites
    assert errors
    window.close()


def test_site_properties_uses_category_layout_and_saves_description():
    app = QApplication.instance() or QApplication([])
    window = Window()
    editor = SiteDialog(window, {}, editing=True)
    site = Site(name="Media", host="media.example.test", port=3001, protocol=PROTOCOLS[1], description="Primary media host")
    editor.load(asdict(site))
    roots = [editor.navigation.topLevelItem(i).text(0) for i in range(editor.navigation.topLevelItemCount())]
    assert roots == ["General", "Connection", "Transfer", "Notification"]
    assert [editor.navigation.topLevelItem(1).child(i).text(0) for i in range(6)] == [
        "Account", "Ident", "Proxy Server", "List Mode", "SSL", "SFTP"]
    assert editor.windowTitle() == "Property - Media"
    assert editor.description.toPlainText() == "Primary media host"
    assert editor.site().description == "Primary media host"
    assert editor.site().port == 3001
    editor.force_utf8.setChecked(True)
    editor.utf8_username.setChecked(False)
    editor.use_masq.setChecked(False)
    editor.site_timezone.setCurrentIndex(editor.site_timezone.findData("UTC-05:00"))
    editor.single_connection.setChecked(True)
    editor.max_transfers_total.setValue(4)
    editor.max_downloads.setValue(3)
    editor.max_uploads.setValue(2)
    editor.fields["local_path"].setText("C:/Media")
    configured = editor.site()
    assert configured.force_utf8 and not configured.utf8_username and configured.use_masq is False
    assert configured.timezone == "UTC-05:00" and configured.single_connection
    assert (configured.max_transfers_total, configured.max_downloads, configured.max_uploads) == (4, 3, 2)
    assert configured.local_path == "C:/Media"
    assert editor.test_button.text() == "Test…"
    editor.close()
    window.close()


def test_editing_site_protocol_keeps_custom_port():
    app = QApplication.instance() or QApplication([])
    window = Window()
    dialog = SiteDialog(window, {}, editing=True)
    dialog.port.setValue(30001)
    dialog.protocol.setCurrentIndex(1)
    assert dialog.port.value() == 30001
    dialog.protocol.setCurrentIndex(2)
    assert dialog.port.value() == 30001
    dialog.close()
    window.close()


def test_new_site_protocol_defaults_respect_custom_port():
    app = QApplication.instance() or QApplication([])
    window = Window()
    dialog = SiteDialog(window, {}, editing=False)
    dialog.protocol.setCurrentIndex(2)
    assert dialog.port.value() == 990
    dialog.port.setValue(30001)
    dialog.protocol.setCurrentIndex(0)
    assert dialog.port.value() == 30001
    dialog.close()
    window.close()




def test_quick_connect_protocol_change_preserves_custom_port():
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.quick_protocol.setCurrentIndex(1)  # AUTH SSL to AUTH TLS keeps the shared default port.
    assert window.quick_port.value() == 21
    window.quick_protocol.setCurrentIndex(2)
    assert window.quick_port.value() == 990
    window.quick_port.setValue(30001)
    window.quick_protocol.setCurrentIndex(0)
    assert window.quick_port.value() == 30001
    window.close()


def test_reference_style_shell_exposes_quick_connect_and_classic_work_areas():
    app = QApplication.instance() or QApplication([])
    window = Window()
    assert [window.menuBar().actions()[i].text().replace("&", "")
            for i in range(window.menuBar().actions().__len__())] == ["File", "Edit", "Mark", "View", "FTP", "Tools", "Help"]
    assert window.quick_protocol.count() == len(PROTOCOLS)
    assert [window.queue.headerItem().text(i) for i in range(window.queue.columnCount())] == [
        "Name", "Progress", "Speed", "Transferred", "Size", "Source path", "Target path", "Remark"]
    assert [window.log_tabs.tabText(i) for i in range(window.log_tabs.count())] == [
        "FTP Log", "Transfer Log", "System Log", "Tasks"]
    window.close()


def test_pane_toolbar_exposes_ftp_rush_actions_and_shortcuts():
    app = QApplication.instance() or QApplication([])
    window = Window()
    left, right = window.current_panes()
    left_tips = [action.toolTip() for action in left.file_toolbar.actions() if action.toolTip()]
    assert left.sites_button.toolTip().startswith('Sites\n') and 'saved site' in left.sites_button.toolTip()
    assert left.switch_servers_button.toolTip().startswith('Switch servers\n')
    assert any(tip.startswith('Parent directory\n') for tip in left_tips)
    assert any(tip.startswith('Refresh\n') and '(F5)' in tip for tip in left_tips)
    assert any(tip.startswith('Compare folders\n') and '(Ctrl+D)' in tip for tip in left_tips)
    assert any(tip.startswith('Transfer selection\n') and '(Ctrl+T)' in tip for tip in left_tips)
    assert left.mode_button.text() == right.mode_button.text() == "Remote"
    assert left.refresh_shortcut.key().toString() == "F5"
    assert left.transfer_shortcut.key().toString() == "Ctrl+T"
    refreshed = []
    transfers = []
    left.refresh = lambda: refreshed.append(True)
    window.enqueue = lambda right_to: transfers.append(right_to)
    window.show()
    QTest.qWait(50)
    left.tree.setFocus()
    QTest.keyClick(left.tree, Qt.Key.Key_F5)
    QTest.keyClick(left.tree, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier)
    QTest.qWait(20)
    assert refreshed == [True]
    assert transfers == [True]
    window.close()


def test_pane_sites_menu_lists_imported_site_manager_profiles():
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.sites = {"backup server": asdict(Site(name="backup server", host="backup.example.test"))}
    pane = window.current_panes()[0]
    pane.populate_site_menu()
    labels = [action.text() for action in pane.sites_menu.actions()]
    assert "backup server" in labels
    assert "Quick Connect dialog…" in labels
    assert "Manage sites…" not in labels
    assert "Disconnect" not in labels
    window.close()


def test_switch_remote_servers_swaps_profiles_and_directories():
    app = QApplication.instance() or QApplication([])
    window = Window()
    left, right = window.current_panes()
    left_site = Site(name="left", host="left.example.test")
    right_site = Site(name="right", host="right.example.test")
    left.set_remote(left_site, "left-secret", "/left/path")
    right.set_remote(right_site, "right-secret", "/right/path")
    left.refresh = lambda: None
    right.refresh = lambda: None
    window.swap_remote_servers()
    assert (left.site, left.password, left.path) == (right_site, "right-secret", "/right/path")
    assert (right.site, right.password, right.path) == (left_site, "left-secret", "/left/path")
    window.close()


def test_compare_folder_contents_marks_unique_and_different_files(tmp_path):
    app = QApplication.instance() or QApplication([])
    left_path, right_path = tmp_path / "left", tmp_path / "right"
    left_path.mkdir()
    right_path.mkdir()
    (left_path / "same.txt").write_bytes(b"same")
    (right_path / "same.txt").write_bytes(b"same")
    (left_path / "left-only.txt").write_bytes(b"left")
    (right_path / "right-only.txt").write_bytes(b"right")
    (left_path / "different.txt").write_bytes(b"one")
    (right_path / "different.txt").write_bytes(b"longer")
    window = Window()
    left, right = window.current_panes()
    left.change_mode("Local")
    right.change_mode("Local")
    left.navigate(str(left_path))
    right.navigate(str(right_path))

    window.compare_commands.actions["Compare name and size"].setChecked(True)
    window.compare_contents()

    def row_for(pane, name):
        for index in range(pane.tree.topLevelItemCount()):
            row = pane.tree.topLevelItem(index)
            if row.data(0, Qt.ItemDataRole.UserRole).name == name:
                return row
        raise AssertionError(f"missing row: {name}")

    assert row_for(left, "left-only.txt").background(0).color().name() == "#fff0c2"
    assert row_for(right, "right-only.txt").background(0).color().name() == "#fff0c2"
    assert row_for(left, "different.txt").background(0).color().name() == "#ffd9cc"
    assert row_for(right, "different.txt").background(0).color().name() == "#ffd9cc"
    assert "left only: 1 · right only: 1 · different: 1" in window.statusBar().currentMessage()
    window.close()


def test_site_selection_and_explicit_connect_and_options_section():
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.sites = {"saved": asdict(Site(name="saved", host="saved.example.test"))}
    manager = SitesDialog(window)
    calls = []
    manager.connect_to = lambda pane, site_name=None: calls.append((pane, site_name))
    row = manager.tree.topLevelItem(0)
    manager.connect_target.setCurrentIndex(1)
    manager.tree.itemClicked.emit(row, 0)
    QTest.qWait(QApplication.doubleClickInterval() + 30)
    assert calls == []
    row.setSelected(True)
    manager.connect_button.click()
    assert calls == [(1, None)]
    assert [manager.navigation.topLevelItem(i).text(0) for i in range(manager.navigation.topLevelItemCount())] == ["Saved sites"]
    options = PreferencesDialog(window)
    assert [options.navigation.topLevelItem(i).text(0) for i in range(options.navigation.topLevelItemCount())][:3] == ["General", "Sites", "Connection"]
    options.navigation.setCurrentItem(options.navigation.findItems("Sites", Qt.MatchFlag.MatchRecursive)[0])
    assert {button.text() for button in options.pages.widget(options.page_by_name["Sites"]).findChildren(QPushButton)} >= {
        "Import Sites…", "Export Sites…"}
    options.close()
    context_names = [action.text() for action in manager._build_context_menu().actions() if not action.isSeparator()]
    assert context_names == ["New Folder", "New Site", "Disabled", "Connect", "Connect to", "Rename", "Delete", "Move to Folder", "Copy Site", "Test…", "Site Properties", "Select All", "Clear Selection"]
    menu = manager._build_context_menu()
    shortcuts = {action.text(): action.shortcut().toString() for action in menu.actions()}
    assert shortcuts["Rename"] == "F2"
    assert shortcuts["Delete"] == "Del"
    assert shortcuts["Site Properties"] == "Alt+Enter"
    manager.close()
    window.close()


def test_connect_saved_site_prompts_and_remembers_password(monkeypatch, tmp_path):
    import fluxftp.app as app_module
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.sites_file = tmp_path / "config/sites.json"
    monkeypatch.setattr(window, "ensure_proxy_credentials", lambda *_: True)
    class MemoryVault:
        records = {}
        def get(self, key): return self.records.get(key, "")
        def set(self, key, value):
            if value: self.records[key] = value
            else: self.records.pop(key, None)
    vault = MemoryVault()
    window.credential_vault = vault
    site = Site(name="saved", host="saved.example.test", port=30001)
    window.sites = {site.name: asdict(site)}
    pane = window.tabs.currentWidget().widget(0)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("secret", True))
    result = {}
    pane.refresh = lambda: result.update(site=pane.site, password=pane.password, path=pane.path)
    pane.connect_saved_site("saved")
    assert result["site"].port == 30001
    assert result["password"] == "secret"
    assert result["path"] == site.root
    assert "password" not in window.sites["saved"]
    assert vault.get(window.sites["saved"]["_credential_id"]) == "secret"
    window.close()


def test_preferences_navigation_and_connection_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'navigation-settings.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(app_module, 'settings_store', lambda: settings)
    window = Window()
    window.preferences.clear()
    options = PreferencesDialog(window)
    roots = [options.navigation.topLevelItem(i).text(0) for i in range(options.navigation.topLevelItemCount())]
    assert roots == ["General", "Sites", "Connection", "Transfer", "Misc.", "Display", "Mouse Action", "File Associations", "Event Notification", "Confirm"]
    connection_item = options.navigation.topLevelItem(roots.index('Connection'))
    connection_children = [connection_item.child(i).text(0)
                           for i in range(connection_item.childCount())]
    assert connection_children == ["Key Manager", "Cloud Accounts", "Ident", "Keep Alive", "Proxy Server", "List Mode", "Data connection"]
    class MemorySettings:
        values = {"Connection/connect_timeout": 47,
                  "Connection/command_timeout": 83,
                  "Connection/list_mode": "LIST only"}
        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            return type(value) if type else value
        def setValue(self, key, value):
            self.values[key] = value
        def sync(self):
            pass
    window.preferences = MemorySettings()
    connection = window.new_connection(Site(host="example.test"), "")
    assert connection.connect_timeout == 47
    assert connection.command_timeout == 83
    assert connection.list_mode == "LIST only"
    options.close()
    window.close()



def test_transfer_queue_runs_parallel_jobs_up_to_site_limit():
    from threading import Event, Lock
    app = QApplication.instance() or QApplication([])
    window = Window()
    workspace = window.tabs.currentWidget()
    site = Site(name="parallel", host="parallel.example.test", max_transfers_total=3,
                max_downloads=3, max_uploads=3)
    gate = Event()
    three_started = Event()
    lock = Lock()
    started = [0]
    def task(worker):
        with lock:
            started[0] += 1
            if started[0] >= 3:
                three_started.set()
        gate.wait(5)
    for index in range(4):
        row = QTreeWidgetItem([f"file-{index}", "0%", "—", "—", "1 B", "/remote/file", "C:/file", "Download · Queued"])
        window.queue.addTopLevelItem(row)
        window.pending.append({"row": row, "task": task, "route": "Download", "workspace": workspace,
                               "source_site": site, "destination_site": None})
    try:
        window.start_next()
        assert three_started.wait(3), "the queue did not start three worker threads concurrently"
        assert len(window.active) == 3
        assert len(window.pending) == 1
    finally:
        gate.set()
    settle(window)
    assert started[0] == 4
    assert not window.active and not window.pending
    window.close()


def test_single_connection_site_serializes_queue_jobs():
    app = QApplication.instance() or QApplication([])
    window = Window()
    workspace = window.tabs.currentWidget()
    site = Site(name="serial", host="serial.example.test", single_connection=True)
    first = {"row": QTreeWidgetItem(["a"]), "task": lambda _worker: None, "route": "Download",
             "workspace": workspace, "source_site": site, "destination_site": None}
    second = {"row": QTreeWidgetItem(["b"]), "task": lambda _worker: None, "route": "Download",
              "workspace": workspace, "source_site": site, "destination_site": None}
    window.active["first"] = window._transfer_record(first)
    assert not window._can_start_transfer(second)
    window.active.clear()
    assert window._can_start_transfer(second)
    window.close()


def test_ident_options_persist_and_control_listener(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from test_ident import unused_port
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "options.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(app_module, "settings_store", lambda: settings)
    window = Window()
    options = PreferencesDialog(window)
    assert options.ident_enabled.text() == "Enable Ident"
    assert options.ident_connecting.text() == "Enable only when connecting"
    assert options.ident_port.value() == 113
    options.ident_port.setValue(unused_port())
    options.ident_name.setText("my-ident")
    options.ident_name.editingFinished.emit()
    options.ident_system.setText("UNIX")
    options.ident_system.editingFinished.emit()
    options.ident_enabled.setChecked(True)
    assert window.ident.enabled and window.ident.listener is None
    options.ident_connecting.setChecked(False)
    assert window.ident.listener is not None
    assert (window.ident.name, window.ident.system) == ("my-ident", "UNIX")
    options.close()
    reopened = PreferencesDialog(window)
    assert reopened.ident_enabled.isChecked()
    assert not reopened.ident_connecting.isChecked()
    assert reopened.ident_name.text() == "my-ident"
    reopened.ident_enabled.setChecked(False)
    assert window.ident.listener is None
    reopened.close()
    window.close()


def test_external_drop_copies_folders_and_preserves_sources(tmp_path, monkeypatch):
    from PySide6.QtCore import QMimeData, QUrl, QPointF
    from PySide6.QtGui import QDropEvent
    app = QApplication.instance() or QApplication([])
    window = Window()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "nested").mkdir()
    (source / "nested" / "file.txt").write_text("drop payload")
    pane = window.current_panes()[1]
    pane.change_mode("Local")
    pane.navigate(str(target))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
                      mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    pane.tree.dropEvent(event)
    settle(window)
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.CopyAction
    assert (target / "source" / "nested" / "file.txt").read_text() == "drop payload"
    assert (source / "nested" / "file.txt").exists()
    assert not errors
    # A drop onto its own source must not enqueue destructive work.
    pane.navigate(str(tmp_path))
    count = window.queue.topLevelItemCount()
    window.enqueue_local_paths(pane, [str(source)])
    assert window.queue.topLevelItemCount() == count
    assert errors
    window.close()


def test_drop_rejects_urls_and_honors_cancel(tmp_path, monkeypatch):
    from PySide6.QtCore import QMimeData, QUrl, QPointF
    from PySide6.QtGui import QDropEvent
    app = QApplication.instance() or QApplication([])
    window = Window()
    pane = window.current_panes()[1]
    pane.change_mode("Local")
    pane.navigate(str(tmp_path))
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.test/file")])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    pane.tree.dropEvent(event)
    assert not event.isAccepted()
    source = tmp_path / "source.txt"
    source.write_text("original")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    window.preferences.setValue("Confirm/queue", True)
    window.enqueue_local_paths(pane, [str(source)])
    assert window.queue.topLevelItemCount() == 0
    assert source.read_text() == "original"
    window.close()

def test_site_password_uses_secure_vault_and_is_excluded_from_profile(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.sites_file = tmp_path / "config/sites.json"
    window.sites = {"Saved": asdict(Site(name="Saved", host="example.test"))}
    class MemoryVault:
        records = {}
        def get(self, key): return self.records.get(key, "")
        def set(self, key, value):
            if value: self.records[key] = value
            else: self.records.pop(key, None)
    vault = MemoryVault()
    window.credential_vault = vault
    assert window.save_sites()
    window.set_site_password("Saved", "stored-secret")
    profile = window.sites["Saved"]
    assert window.get_site_password(profile) == "stored-secret"
    assert "stored-secret" not in window.sites_file.read_text("utf-8")
    assert window.site_from_profile(profile).host == "example.test"

    editor = SiteDialog(window, {}, editing=True)
    editor.load(profile)
    assert editor.password.text() == "stored-secret"
    editor.close()

    assert window.save_sites({})
    assert not vault.records
    window.close()

