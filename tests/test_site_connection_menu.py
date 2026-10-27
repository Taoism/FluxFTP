from dataclasses import asdict
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox
from fluxftp.core import Site
from fluxftp.site_manager import SitesDialog
from test_queue import window


def action(menu, title):
    return next(item for item in menu.actions() if item.text() == title)


def test_create_move_rename_disable_and_remove_reflected_in_both_panes(window, monkeypatch):
    window.save_sites({name: asdict(Site(name=name, host='example.test')) for name in ('Alpha', 'Beta')})
    manager = SitesDialog(window)
    manager.select_all_sites()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('New group', True))
    move = action(manager._build_context_menu(), 'Move to Folder').menu()
    action(move, 'New Folder…').trigger()
    assert all(profile['_folder'] == 'New group' for profile in window.sites.values())
    for pane in window.current_panes():
        pane.populate_site_menu()
        group = action(pane.sites_menu, 'New group').menu()
        assert [a.text() for a in group.actions()] == ['Alpha', 'Beta']
        calls = []
        monkeypatch.setattr(pane, 'connect_saved_site', calls.append)
        action(group, 'Beta').trigger()
        assert calls == ['Beta']
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Renamed group', True))
    manager.rename_folder('New group')
    manager.toggle_disabled(True)
    for pane in window.current_panes():
        pane.populate_site_menu()
        assert 'New group' not in [a.text() for a in pane.sites_menu.actions()]
        group = action(pane.sites_menu, 'Renamed group').menu()
        assert not any(a.isEnabled() for a in group.actions())
    manager.move_sites('')
    for pane in window.current_panes():
        pane.populate_site_menu()
        assert not action(pane.sites_menu, 'Renamed group').isEnabled()
        assert action(pane.sites_menu, 'Alpha')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    manager.delete()
    for pane in window.current_panes():
        pane.populate_site_menu()
        assert 'Alpha' not in [a.text() for a in pane.sites_menu.actions()]
        assert 'Beta' not in [a.text() for a in pane.sites_menu.actions()]
    manager.close()
