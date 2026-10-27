import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from PySide6.QtTest import QTest

from fluxftp.core import Site
from fluxftp.preferences import PreferencesDialog
from fluxftp import display_options
from test_queue import window, job_for
from test_ui import settle


def test_controls_defaults_and_persistence(window):
    dialog = PreferencesDialog(window)
    expected = {'minimize_tray': False, 'flash_tray': True, 'always_tray': True,
        'local_full_path': False, 'local_show_hidden': False, 'flash_completed': True,
        'update_download_size': False, 'split_sites': False, 'hide_quick_connect': False,
        'show_quick_dialog': True, 'show_recent': True, 'gradient_indicator': True,
        'hide_unfocused_selection': False, 'hyphen_separator': False}
    assert {key: value.isChecked() for key, value in dialog.display_checks.items()} == expected
    assert dialog.size_modes['Dynamic'].isChecked()
    assert dialog.download_seconds.value() == 15 and not dialog.download_seconds.isEnabled()
    assert dialog.sites_lines.value() == 25 and not dialog.sites_lines.isEnabled()
    dialog.display_checks['update_download_size'].setChecked(True)
    dialog.download_seconds.setValue(3)
    assert window.download_display_timer.isActive() and window.download_display_timer.interval() == 3000
    dialog.display_checks['update_download_size'].setChecked(False)
    assert not window.download_display_timer.isActive()
    dialog.display_checks['split_sites'].setChecked(True)
    dialog.sites_lines.setValue(10)
    assert window.preferences.value('Display/sites_lines', type=int) == 10
    dialog.close()


def test_local_hidden_address_size_and_focus(window, tmp_path):
    (tmp_path / 'visible').write_bytes(b'x' * 2048)
    (tmp_path / '.hidden').write_text('hidden')
    pane = window.current_panes()[0]
    pane.local_path = str(tmp_path)
    pane.change_mode('Local')
    assert '.hidden' not in [pane.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).name for i in range(pane.tree.topLevelItemCount())]
    assert pane.address.text() == tmp_path.name
    window.show()
    pane.address.setFocus()
    QTest.qWait(20)
    assert pane.address.text() == str(tmp_path)
    pane.tree.setFocus()
    QTest.qWait(20)
    assert pane.address.text() == tmp_path.name
    window.preferences.setValue('Display/local_full_path', True)
    window.preferences.setValue('Display/local_show_hidden', True)
    window.preferences.setValue('Display/size_mode', 'Bytes')
    pane.refresh()
    assert pane.address.text() == str(tmp_path)
    rows = {pane.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).name: pane.tree.topLevelItem(i) for i in range(pane.tree.topLevelItemCount())}
    assert '.hidden' in rows and rows['visible'].text(1) == '2,048 B'
    row = QTreeWidgetItem(['queued'])
    window.queue.addTopLevelItem(row)
    window.transfer_display_progress(row, 50, 1, 1024, 2048)
    window.preferences.setValue('Display/size_mode', 'KBytes')
    window.apply_display_settings()
    assert rows['visible'].text(1) == '2.0 KB'
    assert row.text(3) == '1.0 KB / 2.0 KB' and row.text(4) == '2.0 KB'


def test_sites_grouping_recent_and_menu_visibility(window, monkeypatch):
    pane = window.current_panes()[0]
    window.sites = {f'Site {i}': asdict(Site(name=f'Site {i}', host='example.test')) for i in range(5)}
    window.preferences.setValue('Display/split_sites', True)
    window.preferences.setValue('Display/sites_lines', 2)
    site = Site(name='Recent', host='recent.test')
    pane.set_remote(site, 'secret-password')
    pane.set_remote(site, 'secret-password')
    assert not window.recent_sites()
    window.remember_recent_site(site)
    window.remember_recent_site(site)
    assert len(window.recent_sites()) == 1
    assert 'secret-password' not in window.preferences.value('Display/recent_connected_sites', type=str)
    pane.populate_site_menu()
    actions = pane.sites_menu.actions()
    assert [a.text() for a in actions[:2]] == ['Quick Connect', 'Quick Connect dialog…']
    assert actions[2].isSeparator()
    assert actions[3].text() == 'Reconnect to Recent'
    assert actions[4].text() == 'Recent sites'
    assert actions[5].isSeparator()
    assert actions[6].text().startswith('Sites ')
    assert not any(a.text() in ('Manage sites…', 'Disconnect') for a in actions)
    groups = [a.menu() for a in actions if a.text().startswith('Sites ')]
    assert [len(g.actions()) for g in groups] == [2, 2, 1]
    recent = next(a.menu() for a in actions if a.text() == 'Recent sites')
    calls = []
    monkeypatch.setattr(pane, 'connect_profile', lambda profile: calls.append(profile))
    recent.actions()[0].trigger()
    assert calls == [asdict(site)]
    for name, value in [('hide_quick_connect', True), ('show_quick_dialog', False), ('show_recent', False)]:
        window.preferences.setValue('Display/' + name, value)
    pane.populate_site_menu()
    assert all(a.text() not in ('Quick Connect', 'Quick Connect dialog…', 'Recent sites') for a in pane.sites_menu.actions())


def test_indicator_separator_and_selection(window):
    pane = window.current_panes()[0]
    pane.set_remote(Site(name='Left', host='example.test'))
    window.preferences.setValue('Display/hyphen_separator', True)
    window.preferences.setValue('Display/gradient_indicator', False)
    window.preferences.setValue('Display/hide_unfocused_selection', True)
    window.apply_display_settings()
    assert window.tabs.tabToolTip(0) == 'Left - Remote'
    assert window.tabs.tabText(0) == 'Workspace 1'
    assert 'qlineargradient' not in pane.title_label.styleSheet()
    assert 'background:transparent' in pane.tree.styleSheet()
    window.show()
    pane.tree.setFocus()
    QTest.qWait(20)
    assert 'background:transparent' not in pane.tree.styleSheet()
    pane.address.setFocus()
    QTest.qWait(20)
    assert 'background:transparent' in pane.tree.styleSheet()
    window.disconnect_pane(pane)
    assert window.tabs.tabToolTip(0) == 'Remote - Remote'


def test_toolbar_disconnect_targets_its_own_pane(window):
    left, right = window.current_panes()
    assert not left.disconnect_button.isEnabled()
    left.set_remote(Site(name='Left', host='left.test'), 'left-password')
    right.set_remote(Site(name='Right', host='right.test'), 'right-password')
    assert left.disconnect_button.isEnabled() and right.disconnect_button.isEnabled()
    left.busy = True
    window.view_commands.options.refresh_windows()
    assert not left.disconnect_button.isEnabled()
    left.busy = False
    window.view_commands.options.refresh_windows()
    left.disconnect_button.click()
    assert left.site is None and left.remote_site is None
    assert left.password == left.remote_password == ''
    assert left.status.text() == 'Disconnected' and not left.disconnect_button.isEnabled()
    assert right.site.host == 'right.test' and right.disconnect_button.isEnabled()


def test_download_listing_timer_keeps_selection(window, tmp_path):
    pane = window.current_panes()[0]
    pane.local_path = str(tmp_path)
    (tmp_path / 'existing').write_text('keep')
    pane.change_mode('Local')
    row = next(pane.tree.topLevelItem(i) for i in range(pane.tree.topLevelItemCount()) if pane.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).name == 'existing')
    row.setSelected(True)
    (tmp_path / 'download.fluxpart').write_bytes(b'x' * 4096)
    window.active['test'] = {'route': 'Download', 'job': {'dst': str(tmp_path / 'download'), 'directory': False}}
    try:
        window.update_downloading_sizes()
        assert [e.name for e in pane.selected()] == ['existing']
        entries = [pane.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(pane.tree.topLevelItemCount())]
        assert any(e.name == 'download.fluxpart' and e.size == 4096 for e in entries)
    finally: window.active.clear()


def test_tray_visibility_animation_and_safe_fallback(window, monkeypatch):
    window.tray.hide()
    events = {}
    fake = SimpleNamespace(show=lambda: events.update(visible=True), hide=lambda: events.update(visible=False),
        setVisible=lambda value: events.update(visible=value), setIcon=lambda icon: events.update(icon=icon.cacheKey()),
        setToolTip=lambda text: None)
    monkeypatch.setattr(window, 'tray', fake)
    monkeypatch.setattr(display_options.QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    window.update_tray()
    assert events['visible']
    window.preferences.setValue('Display/always_tray', False)
    window.update_tray()
    assert not events['visible']
    window.active['test'] = {}
    window.update_tray()
    assert events['visible']
    first = events['icon']
    window.update_tray()
    assert events['icon'] != first
    window.active.clear()
    window.preferences.setValue('Display/minimize_tray', True)
    window.showMinimized()
    window.minimize_to_tray()
    assert not window.isVisible() and events['visible']
    window.restore_from_tray()
    assert window.isVisible() and not window.isMinimized()
    monkeypatch.setattr(display_options.QSystemTrayIcon, 'isSystemTrayAvailable', lambda: False)
    window.showMinimized()
    window.minimize_to_tray()
    assert window.isVisible()


def test_completion_flash_honors_option(window, monkeypatch):
    calls = []
    monkeypatch.setattr(QApplication, 'alert', lambda *args: calls.append(True))
    job_for(window, lambda _: None)
    window.start_next()
    settle(window)
    assert calls == [True]
    window.preferences.setValue('Display/flash_completed', False)
    job_for(window, lambda _: None)
    window.start_next()
    settle(window)
    assert calls == [True]
