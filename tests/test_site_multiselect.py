from dataclasses import asdict
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox
from fluxftp.core import Site
from fluxftp.site_manager import SitesDialog
from test_queue import window


def manager(window):
    profiles = {name: asdict(Site(name=name, host=name.lower() + '.example.test')) for name in ('Alpha', 'Beta', 'Gamma')}
    window.save_sites(profiles)
    window.preferences.setValue('Sites/folders', ['Servers'])
    dialog = SitesDialog(window)
    dialog.show()
    dialog.activateWindow()
    QTest.qWait(30)
    return dialog


def row(dialog, name):
    return dialog.tree.findItems(name, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)[0]


def click(dialog, name, modifier=Qt.KeyboardModifier.NoModifier):
    QTest.mouseClick(dialog.tree.viewport(), Qt.MouseButton.LeftButton, modifier,
                     dialog.tree.visualItemRect(row(dialog, name)).center())


def test_ctrl_shift_and_right_click_preserve_selection(window, monkeypatch):
    dialog = manager(window)
    click(dialog, 'Alpha')
    QTest.qWait(QApplication.doubleClickInterval() + 30)
    assert dialog.isVisible() and dialog.connection is None
    click(dialog, 'Gamma', Qt.KeyboardModifier.ControlModifier)
    assert set(dialog.selected()) == {'Alpha', 'Gamma'}
    assert not dialog.connect_button.isEnabled()
    menus = []
    build = dialog._build_context_menu
    def menu_for_test(*args):
        menu = build(*args)
        menus.append(menu)
        QTimer.singleShot(20, menu.close)
        return menu
    monkeypatch.setattr(dialog, '_build_context_menu', menu_for_test)
    dialog.show_context_menu(dialog.tree.visualItemRect(row(dialog, 'Alpha')).center())
    assert set(dialog.selected()) == {'Alpha', 'Gamma'}
    actions = {a.text(): a for a in menus[-1].actions()}
    assert actions['Delete'].isEnabled() and actions['Copy Sites'].isEnabled()
    assert not actions['Connect'].isEnabled() and not actions['Site Properties'].isEnabled()
    click(dialog, 'Alpha')
    click(dialog, 'Gamma', Qt.KeyboardModifier.ShiftModifier)
    assert set(dialog.selected()) == {'Alpha', 'Beta', 'Gamma'}
    dialog.close()


def test_bulk_disable_move_copy_delete_preserves_credentials(window, monkeypatch):
    dialog = manager(window)
    window.set_site_password('Alpha', 'synthetic-alpha')
    window.set_site_password('Beta', 'synthetic-beta')
    originals = {name: window.sites[name]['_credential_id'] for name in ('Alpha', 'Beta')}
    click(dialog, 'Alpha')
    click(dialog, 'Beta', Qt.KeyboardModifier.ControlModifier)
    dialog.toggle_disabled(True)
    assert all(window.sites[name]['_disabled'] for name in originals)
    assert set(dialog.selected()) == set(originals)
    dialog.toggle_disabled(False)
    dialog.move_sites('Servers')
    assert all(window.sites[name]['_folder'] == 'Servers' for name in originals)
    dialog.copy_site()
    for name, identity in originals.items():
        assert window.sites[name]['_credential_id'] == identity
        clone = window.sites[name + ' (copy)']
        assert clone['_credential_id'] != identity
        assert window.get_site_password(clone) == 'synthetic-' + name.lower()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    dialog.delete()
    assert set(window.sites) == {'Alpha (copy)', 'Beta (copy)', 'Gamma'}
    dialog.close()


def test_filtered_select_all_does_not_include_hidden_sites_or_folders(window):
    dialog = manager(window)
    dialog.select_all_sites()
    dialog.search.setText('Alpha')
    dialog.tree.setFocus()
    QTest.keyClick(dialog.tree, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert dialog.selected() == ['Alpha']
    assert len(dialog.tree.selectedItems()) == 1
    assert dialog.connect_button.isEnabled()
    dialog.connect_target.setCurrentIndex(1)
    dialog.connect_button.click()
    assert dialog.connection == (1, 'Alpha')
