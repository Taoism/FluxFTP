import json
from dataclasses import asdict
from types import SimpleNamespace
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QContextMenuEvent, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QMenu
from shiboken6 import isValid
from fluxftp.app import Window
from fluxftp.core import Site, Entry, PROTOCOLS
from fluxftp.preferences import PreferencesDialog
from fluxftp import themes
from fluxftp.tray_menu import queue_manual_download
from test_queue import window
from test_ui import settle
from test_integration import servers


def rename(window, monkeypatch, index, value):
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: (value, True))
    window.rename_workspace(index)


def test_workspace_double_click_and_all_name_surfaces(window, monkeypatch):
    window.show()
    window.add_workspace()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Music uploads', True))
    QTest.qWait(30)
    row = window.window_list.topLevelItem(0)
    rect = window.window_list.visualItemRect(row)
    point = QPoint(window.window_list.columnViewportPosition(0) + 35, rect.center().y())
    QTest.mouseClick(window.window_list.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(window.window_list.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.qWait(20)
    assert window.tabs.tabText(0) == 'Music uploads'
    assert row.text(0) == 'Music uploads'
    assert window.workspace_navigation.buttons[0].toolTip().startswith('Music uploads\n')
    assert json.loads(window.preferences.value('Workspaces/session'))['items'][0]['name'] == 'Music uploads'


def test_name_validation_and_cancel(window, monkeypatch):
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    window.add_workspace()
    for invalid in (' ', 'x' * 81, 'two\nlines', 'Workspace 1'):
        rename(window, monkeypatch, 1, invalid)
    assert len(errors) == 4
    assert window.tabs.tabText(1) == 'Workspace 2'
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Changed', False))
    window.rename_workspace(1)
    assert window.tabs.tabText(1) == 'Workspace 2'


def test_session_restart_names_count_selection_and_splitters(window, monkeypatch):
    window.show()
    window.resize(1500, 950)
    rename(window, monkeypatch, 0, 'Left work')
    window.add_workspace()
    rename(window, monkeypatch, 1, 'Right work')
    window.current_panes()[0].change_mode('Local')
    window.tabs.currentWidget().setSizes([300, 800])
    window.tabs.setCurrentIndex(0)
    window.tabs.currentWidget().setSizes([800, 300])
    QTest.qWait(30)
    expected = window.tabs.widget(0).sizes()
    window.save_panel_layout()
    window.close()
    reopened = Window()
    try:
        reopened.resize(1500, 950)
        reopened.show()
        reopened.resize(1500, 950)
        QTest.qWait(40)
        assert [reopened.tabs.tabText(i) for i in range(2)] == ['Left work', 'Right work']
        assert reopened.tabs.count() == 2 and reopened.tabs.currentIndex() == 0
        actual = reopened.tabs.widget(0).sizes()
        assert abs(actual[0] / sum(actual) - expected[0] / sum(expected)) < .03
        assert reopened.tabs.widget(1).widget(0).mode == 'Local'
        assert all(reopened.tabs.widget(i).widget(s).site is None for i in range(2) for s in range(2))
        saved = window.preferences.value('Workspaces/session')
        assert 'password' not in saved and 'site' not in saved
    finally:
        reopened.close()


def test_close_then_restart_does_not_resurrect_workspace(window, monkeypatch):
    window.add_workspace()
    window.add_workspace()
    rename(window, monkeypatch, 2, 'Keep me')
    assert window.close_tab(1)
    window.close()
    reopened = Window()
    try:
        assert [reopened.tabs.tabText(i) for i in range(reopened.tabs.count())] == ['Workspace 1', 'Keep me']
    finally:
        reopened.close()


def test_duplicate_and_close_others_guard(window, monkeypatch):
    rename(window, monkeypatch, 0, 'Archive')
    window.duplicate_workspace(window.tabs.widget(0))
    assert window.tabs.tabText(1) == 'Archive (copy)'
    window.add_workspace()
    keep = window.tabs.widget(0)
    busy = window.tabs.widget(1)
    busy.widget(0).busy = True
    window.close_other_workspaces(keep)
    assert window.tabs.count() == 2 and window.tabs.currentWidget() is keep
    busy.widget(0).busy = False
    window.close_other_workspaces(keep)
    assert window.tabs.count() == 1


def test_context_menu_repeated_native_events(window, monkeypatch):
    window.show()
    for _ in range(3):
        seen = []
        def inspect():
            popup = QApplication.activePopupWidget()
            assert isinstance(popup, QMenu)
            seen.extend(a.text() for a in popup.actions() if not a.isSeparator())
            popup.close()
        QTimer.singleShot(30, inspect)
        button = window.workspace_navigation.buttons[0]
        QApplication.sendEvent(button, QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                               button.rect().center(), button.mapToGlobal(button.rect().center())))
        assert seen == ['Rename', 'Duplicate', 'Close', 'Close Others']
        QApplication.processEvents()
    assert isValid(window.tabs.widget(0))


def test_reset_layout_preserves_work_and_names(window, monkeypatch):
    window.show()
    rename(window, monkeypatch, 0, 'Keep my name')
    window.header_rows.join('tabs', 'tools')
    window.header_rows.rows['overview'].set_height(240)
    window.set_toolbar_icon_size(40)
    window.work_area.insertWidget(0, window.queue_panel)
    window.tabs.widget(0).setOrientation(Qt.Orientation.Vertical)
    window.preferences.setValue('Display/auto_size/local', False)
    window.preferences.setValue('Display/column_width/local/0', 900)
    window.preferences.setValue('Transfer/download_kib', 72)
    site = Site('Preserved', 'example.invalid')
    window.current_panes()[0].set_remote(site, 'not-persisted')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.reset_layout()
    QTest.qWait(25)
    assert window.tabs.tabText(0) == 'Keep my name'
    assert window.current_panes()[0].site is site
    assert window.current_panes()[0].password == 'not-persisted'
    assert window.work_area.widget(0) is window.workspace_panel
    assert window.tabs.widget(0).orientation() == Qt.Orientation.Horizontal
    assert window.header_rows.groups == [['tools'], ['connection'], ['tabs'], ['overview']]
    assert window.toolbar_icon_size() == 24
    assert window.header_rows.rows['overview'].height() <= 120
    assert window.preferences.value('Transfer/download_kib', type=int) == 72
    assert not window.preferences.contains('Display/column_width/local/0')
    window.current_panes()[0].set_remote()


def test_reset_cancel_changes_nothing(window, monkeypatch):
    window.set_toolbar_icon_size(40)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
    window.reset_layout()
    assert window.toolbar_icon_size() == 40
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)


def test_tray_menu_reference_and_shared_actions(window, monkeypatch):
    menu = window.tray_menu
    assert [a.text() if not a.isSeparator() else '---' for a in menu.actions()] == [
        'Restore main window', '---', 'Limit Upload Speed', 'Limit Download Speed', '---',
        'On Transfer Completed', '---', 'Clipboard', '---', 'Manual Download from Site…', '---',
        'Queue Information…', '---', 'About FluxFTP…', '---', 'Exit']
    assert window.queue_commands.actions['Limit Upload Speed'] in menu.actions()
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *a, **k: (123, True))
    menu.aboutToShow.emit()
    window.queue_commands.actions['Limit Upload Speed'].trigger()
    assert window.preferences.value('Transfer/limit_upload', type=bool)
    assert window.preferences.value('Transfer/upload_kib', type=int) == 123
    completed = next(a.menu() for a in menu.actions() if a.text() == 'On Transfer Completed')
    next(a for a in completed.actions() if a.text() == 'Disconnect').trigger()
    assert window.preferences.value('Transfer/completed') == 'Disconnect'
    window.tools_commands.update()
    assert next(a for a, k, v, _ in window.tools_commands.choices if k == 'Transfer/completed' and v == 'Disconnect').isChecked()
    assert window.tools_commands.actions['Manual Download from Site…'].isEnabled()


def test_follow_os_changes_live_and_fixed_theme_stays_fixed(window, monkeypatch):
    state = {'dark': False}
    monkeypatch.setattr(themes, 'os_dark', lambda system=False: state['dark'])
    dialog = PreferencesDialog(window)
    try:
        dialog.theme_combo.setCurrentText('Follow OS')
        assert themes.theme_name(window.preferences) == 'Light'
        assert QApplication.palette().color(QPalette.ColorRole.Base).name() == themes.PALETTES['Light']['base']
        before_icon = window.tray_normal.pixmap(32, 32).toImage()
        state['dark'] = True
        window.system_appearance.refresh()
        assert themes.theme_name(window.preferences) == 'Midnight'
        assert window.preferences.value('Display/style') == 'Follow OS'
        assert QApplication.palette().color(QPalette.ColorRole.Base).name() == themes.PALETTES['Midnight']['base']
        assert before_icon != window.tray_normal.pixmap(32, 32).toImage()
        assert all(control.currentText() == 'Midnight' for control in dialog.color_themes.values())
        dialog.theme_combo.setCurrentText('Black')
        state['dark'] = False
        window.system_appearance.refresh()
        assert QApplication.palette().color(QPalette.ColorRole.Base).name() == themes.PALETTES['Black']['base']
    finally:
        dialog.close()


def test_manual_download_held_queue_and_close_guard(window, monkeypatch, tmp_path):
    site = Site('Example', 'example.invalid', username='anonymous', protocol=PROTOCOLS[4])
    window.sites[site.name] = asdict(site)
    window.preferences.setValue('Confirm/unencrypted', False)
    window.preferences.setValue('Confirm/queue', False)
    monkeypatch.setattr(window, 'get_site_password', lambda data: '')
    monkeypatch.setattr(window, 'ensure_proxy_credentials', lambda *a: True)
    requests = []
    monkeypatch.setattr(window, 'run', lambda task, done, failed: requests.append((task, done, failed)))
    window.add_workspace()
    workspace = window.tabs.currentWidget()
    queue_manual_download(window, site.name, '/music/song.flac', str(tmp_path))
    assert window.workspace_busy(workspace)
    assert not window.close_tab(1)
    requests[0][1](Entry('song.flac', False, 50))
    assert not window.workspace_busy(workspace)
    job = window.queue_jobs[0]
    assert job['held'] and job['src'] == '/music/song.flac'
    assert job['dst'] == str(tmp_path / 'song.flac')
    assert job['workspace'] is workspace
    assert not window.active
    window.discard_queue_jobs([job])


def test_corrupt_workspace_session_falls_back(window):
    for raw in ('invalid', '[]', '{"items": "bad"}', '{"items": []}'):
        window.preferences.setValue('Workspaces/session', raw)
        reopened = Window()
        try:
            assert reopened.tabs.count() == 1
        finally:
            reopened.close()


def test_manual_download_real_ftp(window, monkeypatch, tmp_path, servers):
    root, site = servers()
    (root / 'sample.bin').write_bytes(b'example bytes' * 100)
    destination = tmp_path / 'downloads'
    destination.mkdir()
    window.sites[site.name] = asdict(site)
    window.preferences.setValue('Confirm/unencrypted', False)
    window.preferences.setValue('Confirm/queue', False)
    monkeypatch.setattr(window, 'get_site_password', lambda data: 'password')
    queue_manual_download(window, site.name, '/sample.bin', str(destination))
    import time
    deadline = time.monotonic() + 10
    while window.workers:
        QTest.qWait(10)
        assert time.monotonic() < deadline
    assert len(window.pending) == 1 and window.pending[0]['held']
    window.play_queue()
    settle(window)
    assert (destination / 'sample.bin').read_bytes() == (root / 'sample.bin').read_bytes()


def test_smoke_window_isolates_workspace_settings(window, monkeypatch):
    rename(window, monkeypatch, 0, 'Real session')
    original = window.preferences.value('Workspaces/session')
    smoke = Window(smoke=True)
    try:
        assert smoke.tabs.count() == 1 and smoke.tabs.tabText(0) == 'Workspace 1'
        assert smoke.preferences.fileName() != window.preferences.fileName()
        assert str(smoke.sites_file).startswith(smoke._smoke_directory.name)
        smoke.add_workspace()
    finally:
        smoke.close()
    assert window.preferences.value('Workspaces/session') == original


def test_layout_choice_overrides_hidden_restored_workspace(window, monkeypatch):
    window.add_workspace()
    window.tabs.setCurrentIndex(0)
    window.save_workspaces()
    reopened = Window()
    try:
        reopened.show()
        reopened.tools_commands.layout('Stacked')
        reopened.tabs.setCurrentIndex(1)
        QTest.qWait(20)
        assert reopened.tabs.currentWidget().orientation() == Qt.Orientation.Vertical
        rename(reopened, monkeypatch, 1, 'x' * 80)
        reopened.duplicate_workspace(reopened.tabs.currentWidget())
        assert len(reopened.tabs.tabText(2)) <= 80
    finally:
        reopened.close()
