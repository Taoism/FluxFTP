import json
import os
from dataclasses import replace
from threading import Event

import pytest

from fluxftp.ascii_rules import validate_rule
from fluxftp.core import Connection, transfer_tree
from fluxftp.transfer_options import TransferOptions
from test_integration import servers

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def test_skip_modes_masks_and_folder_scope():
    options = TransferOptions(skip_enabled=True)
    assert options.skip_reason('FILE.LOG', False) == 'Skip List mask'
    assert options.skip_reason('zero.bin', False, 0) == 'Zero byte file'
    assert options.skip_reason('empty', True, empty=True) == 'Empty directory'
    assert not options.skip_reason('folder.log', True)
    assert replace(options, skip_folders=True).skip_reason('folder.log', True)
    assert not replace(options, skip_enabled=False).skip_reason('file.log', False, 0)
    for mode, automatic in [('Auto Transfer', True), ('Manual Transfer', False)]:
        scoped = replace(options, skip_mode=mode)
        assert scoped.skip_reason('file.log', False, automatic=automatic)
        assert not scoped.skip_reason('file.log', False, automatic=not automatic)
    regex = replace(options, skip_rules=json.dumps([validate_rule(r'^temp[0-9]+$', True)]))
    assert regex.skip_reason('TEMP123', False)
    assert not regex.skip_reason('temp.bin', False)
    assert not replace(options, skip_rules='[]').skip_reason('file.log', False)


@pytest.mark.parametrize('mode,expected', [('Both', {'keep.bin'}), ('Auto Transfer', {'keep.bin'}),
                                          ('Manual Transfer', {'keep.bin', 'file.log', 'zero', 'empty'})])
def test_recursive_skip_local(tmp_path, mode, expected):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'keep.bin').write_bytes(b'keep')
    (source / 'file.log').write_bytes(b'log')
    (source / 'zero').touch()
    (source / 'empty').mkdir()
    target = tmp_path / 'target'
    skipped = []
    options = TransferOptions(skip_enabled=True, skip_mode=mode)
    assert transfer_tree(None, None, str(source), str(target), True, Event(), lambda *_: None,
                         options=options, skipped=lambda *item: skipped.append(item))
    assert {p.name for p in target.iterdir()} == expected
    assert len(skipped) == (0 if mode == 'Manual Transfer' else 3)
    assert (target / 'keep.bin').read_bytes() == b'keep'


def test_skip_selected_root_preserves_destination(tmp_path):
    source = tmp_path / 'source.log'
    source.write_bytes(b'new')
    target = tmp_path / 'target'
    target.write_bytes(b'old')
    skipped = []
    assert transfer_tree(None, None, str(source), str(target), False, Event(), lambda *_: None,
                         options=TransferOptions(skip_enabled=True), skipped=lambda *item: skipped.append(item)) is False
    assert target.read_bytes() == b'old'
    assert skipped == [(str(source), 'Skip List mask')]


@pytest.mark.parametrize('upload', [True, False])
def test_ftp_recursive_skip(servers, tmp_path, upload):
    root, site = servers(False)
    folder = tmp_path / 'source' if upload else root / 'source'
    folder.mkdir()
    (folder / 'file.log').write_bytes(b'log')
    (folder / 'keep.bin').write_bytes(b'keep')
    (folder / 'zero').touch()
    (folder / 'empty').mkdir()
    options = TransferOptions(skip_enabled=True)
    skipped = []
    with Connection(site, 'password', transfer_options=options) as connection:
        transfer_tree(None if upload else connection, connection if upload else None,
                      str(folder) if upload else '/source', '/target' if upload else str(tmp_path / 'target'),
                      True, Event(), lambda *_: None, options=options, skipped=lambda *item: skipped.append(item))
    target = root / 'target' if upload else tmp_path / 'target'
    assert {p.name for p in target.iterdir()} == {'keep.bin'}
    assert len(skipped) == 3


def test_skip_list_ui_and_import_transaction(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from fluxftp import app as app_module
    from fluxftp.preferences import PreferencesDialog
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'skip.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(app_module, 'settings_store', lambda: settings)
    window = app_module.Window()
    dialog = PreferencesDialog(window)
    assert dialog.skip_enabled.isChecked() and dialog.skip_zero.isChecked() and dialog.skip_empty.isChecked()
    assert not dialog.skip_folders.isChecked()
    assert dialog.skip_list.count() == 5
    monkeypatch.setattr(dialog, '_ascii_rule_dialog', lambda *_: validate_rule(r'^temp$', True))
    dialog._edit_skip_rule(False)
    assert not dialog.skip_list.item(5).icon().isNull()
    monkeypatch.setattr(dialog, '_ascii_rule_dialog', lambda *_: validate_rule('*.bak'))
    dialog._edit_skip_rule(True)
    assert dialog.skip_list.item(5).text() == '*.bak'
    dialog._delete_skip_rules()
    assert dialog.skip_list.count() == 5
    incoming = tmp_path / 'import.txt'
    incoming.write_text('*.log\n*.bak\nregex:^temp$\n', encoding='utf-8')
    dialog.import_skip_rules(incoming)
    assert dialog.skip_list.count() == 7
    exported = tmp_path / 'export.json'
    dialog.export_skip_rules(exported)
    assert len(json.loads(exported.read_text())['rules']) == 7
    dialog.import_skip_rules(exported)
    assert dialog.skip_list.count() == 7
    saved = settings.value('Transfer/skip_rules')
    incoming.write_text('*.new\nregex:[\n', encoding='utf-8')
    with pytest.raises(ValueError): dialog.import_skip_rules(incoming)
    assert settings.value('Transfer/skip_rules') == saved
    assert dialog.skip_list.count() == 7
    dialog.skip_color.setCurrentIndex(1)
    dialog._set_skip_mode('Auto Transfer')
    options = TransferOptions.from_settings(settings)
    assert options.skip_color == '#0000ff' and options.skip_mode == 'Auto Transfer'
    dialog.close()
    window.close()


@pytest.mark.parametrize('root_skipped', [True, False])
def test_queue_marks_skips_without_failure(tmp_path, monkeypatch, root_skipped):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QTreeWidgetItem
    from fluxftp import app as app_module
    from test_ui import settle
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'queue.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(app_module, 'settings_store', lambda: settings)
    window = app_module.Window()
    row = QTreeWidgetItem(['folder', '', '', '', '', '', '', 'Queued'])
    window.queue.addTopLevelItem(row)
    def task(*_):
        return {'transferred': not root_skipped, 'skipped': [('file.log', 'Skip List mask')]}
    window.pending.append({'row': row, 'task': task, 'route': 'Copy', 'workspace': window.tabs.currentWidget(),
                           'source_site': None, 'destination_site': None})
    window.start_next()
    settle(window)
    skipped_row = row if root_skipped else window.queue.topLevelItem(1)
    assert 'Skipped' in skipped_row.text(7)
    assert skipped_row.foreground(0).color().name() == '#ff0000'
    assert 'Skipped: file.log' in window.log.toPlainText()
    assert not window.queue_batch_failed
    window.close()
