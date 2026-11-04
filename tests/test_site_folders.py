from dataclasses import asdict
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox
from fluxftp.core import Site
from fluxftp.site_manager import SitesDialog
from fluxftp.site_icons import SITE_MARKS
from test_queue import window


def select(dialog, label):
    dialog.tree.clearSelection()
    rows = dialog.tree.findItems(label, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)
    assert rows
    dialog.tree.setCurrentItem(rows[0])
    rows[0].setSelected(True)


def test_folder_create_move_rename_reopen_and_remove_keeps_sites(window, monkeypatch):
    profile = asdict(Site(name='One', host='example.test'))
    profile['_credential_id'] = 'retained-secret-reference'
    assert window.save_sites({'One': profile})
    dialog = SitesDialog(window)
    select(dialog, 'One')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Servers', True))
    dialog.new_folder()
    assert window.sites['One']['_folder'] == 'Servers'
    select(dialog, 'Servers')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Production', True))
    dialog.rename()
    assert window.sites['One']['_folder'] == 'Production'
    dialog.close()
    dialog = SitesDialog(window)
    assert dialog.tree.topLevelItem(0).text(0) == 'Production'
    assert dialog.tree.topLevelItem(0).child(0).text(0) == 'One'
    select(dialog, 'One')
    dialog.move_sites('')
    assert '_folder' not in window.sites['One']
    select(dialog, 'One')
    dialog.move_sites('Production')
    select(dialog, 'Production')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog.delete()
    assert dialog.folders() == []
    assert window.sites['One']['_credential_id'] == 'retained-secret-reference'
    assert '_folder' not in window.sites['One']
    dialog.close()


def test_configuration_backup_keeps_empty_site_folders(window):
    from fluxftp.config_backup import configuration
    window.preferences.setValue('Sites/folders', ['Empty folder'])
    backup = configuration(window.preferences, {})
    assert backup['settings']['Sites/folders'] == ['Empty folder']


def test_folder_click_does_not_connect_and_duplicate_is_rejected(window, monkeypatch):
    window.preferences.setValue('Sites/folders', ['Servers'])
    dialog = SitesDialog(window)
    select(dialog, 'Servers')
    dialog.schedule_site_click(dialog.tree.currentItem(), 0)
    assert not dialog.click_timer.isActive() and dialog.connection is None
    notices = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: notices.append(a))
    assert not dialog.valid_folder('servers')
    assert not dialog.valid_folder('../bad')
    assert notices
    menu = dialog._build_context_menu()
    assert next(a for a in menu.actions() if a.text() == 'Rename').isEnabled()
    dialog.close()


def test_site_manager_shows_folder_and_protocol_provider_icons(window):
    protocols = ['FTP · Unencrypted', 'FTPS · AUTH TLS', 'SFTP · SSH', 'SCP · SSH',
                 'Amazon S3', 'Google Drive', 'Microsoft OneDrive', 'WebDAV']
    window.preferences.setValue('Sites/folders', ['Cloud and servers'])
    window.sites = {f'Site {index}': asdict(Site(name=f'Site {index}', host='example.test',
                      protocol=protocol, site_options='{}'))
                    for index, protocol in enumerate(protocols)}
    window.sites['Site 4']['_folder'] = 'Cloud and servers'
    dialog = SitesDialog(window)
    rows = {}
    pending = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
    while pending:
        row = pending.pop()
        rows[row.text(0)] = row
        pending.extend(row.child(i) for i in range(row.childCount()))
    group = rows['Cloud and servers']
    assert not group.icon(0).isNull()
    assert not group.child(0).icon(0).isNull()
    icons = [rows[f'Site {index}'].icon(0) for index in range(len(protocols))]
    assert all(not icon.isNull() for icon in icons)
    assert len({icon.cacheKey() for icon in icons}) == len(protocols)
    assert set(protocols) <= SITE_MARKS.keys()
    dialog.close()
