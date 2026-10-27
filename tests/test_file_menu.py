import json
from dataclasses import asdict
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox, QSystemTrayIcon
from PySide6.QtTest import QTest
from fluxftp.core import Site
from fluxftp import config_backup
from fluxftp.config_backup import configuration, write_json, read_configuration, restore_configuration
from test_queue import window
from test_mouse import local_pair
from test_integration import servers
from test_ui import settle


def isolated_sites(window, tmp_path):
    window.sites_file = tmp_path / 'data' / 'sites.json'
    window.sites_file.parent.mkdir()
    window.sites = {'test': dict(asdict(Site(name='test', host='example.test')), _credential_id='private-reference')}
    window.sites_file.write_text(json.dumps(window.sites))


def test_menu_order_shortcuts_and_close_state(window):
    actions = window.file_menu.actions()
    assert [action.text() if not action.isSeparator() else '---' for action in actions] == [
        'New', 'Close Current Window', '---', 'Minimize to system tray', '---',
        'Backup configuration now…', 'Restore configuration from backup file…', '---', 'Exit…']
    new = actions[0].menu().actions()
    assert [(a.text(), a.shortcut().toString()) for a in new] == [('New Window', 'Ctrl+N'), ('New Window Of Current', 'Ctrl+Shift+N')]
    assert actions[1].shortcut().toString() == 'Ctrl+W'
    assert actions[3].shortcut().toString() == 'F12'
    assert actions[-1].shortcut().toString() == 'Alt+X'
    assert not actions[1].isEnabled()
    new[0].trigger()
    assert window.tabs.count() == 2 and actions[1].isEnabled()
    actions[1].trigger()
    window.update_file_menu()
    assert window.tabs.count() == 1 and not actions[1].isEnabled()


def test_clone_local_locations_filters_without_queue(window, tmp_path):
    left, right, a, b = local_pair(window, tmp_path)
    left.filter.setText('text')
    original = window.tabs.currentWidget()
    window.new_window_of_current()
    clone = window.tabs.currentWidget()
    assert clone is not original and window.tabs.count() == 2
    assert [Path(p.path) for p in window.current_panes()] == [a, b]
    assert clone.widget(0).filter.text() == 'text'
    assert not window.pending


def test_clone_remote_site_path_and_independent_profile(window, servers):
    root, site = servers()
    (root / 'folder').mkdir()
    source = window.current_panes()[0]
    source.set_remote(site, 'password', '/folder')
    window.new_window_of_current()
    settle(window)
    pane = window.current_panes()[0]
    assert pane.site == site and pane.site is not site
    assert pane.remote_site is pane.site
    assert pane.path == '/folder' and pane.password == 'password'
    pane.site.name = 'Changed clone'
    assert source.site.name != pane.site.name


def test_explicit_tray_action_and_missing_tray_fallback(window, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', staticmethod(lambda: True))
    window.preferences.setValue('Display/minimize_tray', False)
    window.preferences.setValue('Display/always_tray', False)
    window.show()
    window.file_minimize_to_tray()
    assert not window.isVisible() and window.tray.isVisible()
    window.restore_from_tray()
    assert window.isVisible()
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', staticmethod(lambda: False))
    window.file_minimize_to_tray()
    assert window.isVisible() and window.isMinimized()


def test_configuration_roundtrip_excludes_secrets_and_keeps_types(window, tmp_path):
    isolated_sites(window, tmp_path)
    window.preferences.setValue('Confirm/exit', False)
    window.preferences.setValue('Layout/tab_width', 133)
    window.preferences.setValue('General/data_path', 'LOCAL-MACHINE-PATH')
    window.preferences.setValue('Connection/proxy_password', 'DO-NOT-EXPORT')
    window.preferences.setValue('Display/recent_sites', json.dumps([{'name': 'test', 'password': 'ALSO-PRIVATE'}]))
    file = tmp_path / 'backup.json'
    write_json(file, configuration(window.preferences, window.sites))
    raw = file.read_text()
    assert all(value not in raw for value in ('DO-NOT-EXPORT', 'ALSO-PRIVATE', 'private-reference', 'LOCAL-MACHINE-PATH'))
    data = read_configuration(file)
    assert data['settings']['Confirm/exit'] is False
    assert data['settings']['Layout/tab_width'] == 133
    assert data['sites']['test']['host'] == 'example.test'


def test_restore_creates_recovery_and_fresh_credentials_preserves_data_path(window, tmp_path):
    isolated_sites(window, tmp_path)
    window.preferences.setValue('General/data_path', str(window.sites_file.parent))
    window.preferences.setValue('Layout/tab_width', 100)
    data = configuration(window.preferences, window.sites)
    data['settings']['Layout/tab_width'] = 166
    data['settings']['Connection/proxy_profile_id'] = 'proxy'
    recovery = restore_configuration(window, data)
    assert read_configuration(recovery)['settings']['Layout/tab_width'] == 100
    assert window.preferences.value('Layout/tab_width', type=int) == 166
    assert window.preferences.value('General/data_path') == str(window.sites_file.parent)
    assert window.sites['test']['_credential_id'] != 'private-reference'
    assert window.preferences.value('Connection/proxy_profile_id') != 'proxy'
    assert json.loads(window.sites_file.read_text(encoding='utf-8')) == window.sites


@pytest.mark.parametrize('damage', ['format', 'site', 'type', 'key', 'transfer'])
def test_reject_invalid_backup_before_mutation(window, tmp_path, damage):
    isolated_sites(window, tmp_path)
    data = configuration(window.preferences, window.sites)
    if damage == 'format': data['format'] = 'foreign'
    if damage == 'site': data['sites']['test']['port'] = -1
    if damage == 'type': data['settings']['Layout/tab_width'] = {'bad': 1}
    if damage == 'key': data['settings']['General/data_path'] = 'elsewhere'
    if damage == 'transfer': data['settings']['Transfer/mode'] = 'invalid'
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps(data))
    before = window.sites_file.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        read_configuration(path)
    assert window.sites_file.read_bytes() == before


def test_restore_write_failure_rolls_back_preferences_and_sites(window, tmp_path, monkeypatch):
    isolated_sites(window, tmp_path)
    window.preferences.setValue('Layout/tab_width', 99)
    data = configuration(window.preferences, window.sites)
    data['settings']['Layout/tab_width'] = 177
    before = window.sites_file.read_bytes()
    original = config_backup.write_json
    def fail(path, value):
        if Path(path) == window.sites_file:
            raise OSError('write failure')
        original(path, value)
    monkeypatch.setattr(config_backup, 'write_json', fail)
    with pytest.raises(OSError, match='write failure'):
        restore_configuration(window, data)
    assert window.preferences.value('Layout/tab_width', type=int) == 99
    assert window.sites_file.read_bytes() == before
    assert window.sites['test']['_credential_id'] == 'private-reference'


def test_restore_ui_cancel_busy_guard_and_success_exits(window, tmp_path, monkeypatch):
    isolated_sites(window, tmp_path)
    path = tmp_path / 'backup.json'
    data = configuration(window.preferences, window.sites)
    data['settings']['Layout/tab_width'] = 177
    write_json(path, data)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.No)
    before = window.sites_file.read_bytes()
    window.restore_configuration_backup()
    assert window.sites_file.read_bytes() == before
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    pane = window.current_panes()[0]
    pane.site = Site(host='example.test')
    window.restore_configuration_backup()
    assert errors and window.sites_file.read_bytes() == before
    pane.site = None
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    closed = []
    monkeypatch.setattr(window, 'close', lambda: closed.append(True))
    window.restore_configuration_backup()
    assert closed == [True] and window._configuration_restored
    assert window.preferences.value('Layout/tab_width', type=int) == 177
