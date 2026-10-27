import json
import os
from threading import Event

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from fluxftp.priority_rules import priority_rank, priority_rules
from fluxftp.transfer_options import TransferOptions
from fluxftp.core import transfer_tree


def test_priority_masks_first_match_regex_and_empty_list():
    assert priority_rank('FILE.SFV') == 0
    assert priority_rank('/folder/file.nfo') == 1
    assert priority_rank('Sample') == 4
    assert priority_rank('Sample.bin') == 5
    rules = json.dumps([{'pattern': '^readme$', 'regex': True}, {'pattern': '*', 'regex': False}])
    assert priority_rank('README', rules) == 0
    assert priority_rank('other', rules) == 1
    assert priority_rank('file.sfv', '[]') == 0
    with pytest.raises(ValueError):
        priority_rules('[{"pattern":"[","regex":true}]')


def test_recursive_sibling_order(tmp_path, monkeypatch):
    import fluxftp.core as core
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    for name in ('movie.bin', 'info.nfo', 'checksum.sfv', 'playlist.m3u', 'cover.jpg'):
        (source / name).write_bytes(b'contents')
    (source / 'Sample').mkdir()
    (source / 'Sample' / 'clip.bin').write_bytes(b'clip')
    original = core.transfer_tree
    order = []
    def record(*args, **kwargs):
        from pathlib import Path
        order.append(Path(args[2]).name)
        return original(*args, **kwargs)
    monkeypatch.setattr(core, 'transfer_tree', record)
    original(None, None, str(source), str(target), True, Event(), lambda *_: None)
    assert order == ['checksum.sfv', 'info.nfo', 'playlist.m3u', 'cover.jpg', 'Sample', 'clip.bin', 'movie.bin']
    assert (target / 'movie.bin').read_bytes() == b'contents'


@pytest.fixture
def window(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from fluxftp import app as module
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'priority.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(module, 'settings_store', lambda: settings)
    window = module.Window()
    yield window
    from test_ui import settle
    window.stop_queue()
    settle(window)
    window.close()


def test_pending_order_stable_and_respects_eligibility(window, monkeypatch):
    from test_queue import job_for
    from test_ui import settle
    window.preferences.setValue('Transfer/single_connection', True)
    order = []
    names = ['first.bin', 'blocked.sfv', 'info.nfo', 'second.bin', 'check.sfv', 'more.nfo']
    for name in names:
        job_for(window, lambda _, name=name: order.append(name), name=name)
    original = window._can_start_transfer
    monkeypatch.setattr(window, '_can_start_transfer', lambda job: original(job) and (job['row'].text(0) != 'blocked.sfv' or bool(order)))
    window.start_next()
    settle(window)
    assert order == ['check.sfv', 'blocked.sfv', 'info.nfo', 'more.nfo', 'first.bin', 'second.bin']


def test_priority_ui_edit_move_delete_persistence(window, monkeypatch):
    from PySide6.QtCore import Qt
    from fluxftp.preferences import PreferencesDialog
    from fluxftp.ascii_rules import validate_rule
    dialog = PreferencesDialog(window)
    assert [dialog.priority_list.item(i).text() for i in range(5)] == ['*.sfv', '*.nfo', '*.m3u', '*.jpg', 'Sample']
    assert not dialog.priority_edit.isEnabled() and not dialog.priority_up.isEnabled()
    dialog.priority_list.setCurrentRow(0)
    assert not dialog.priority_up.isEnabled() and dialog.priority_down.isEnabled()
    dialog._move_priority_rule(1)
    assert dialog.priority_list.item(0).text() == '*.nfo'
    dialog._move_priority_rule(-1)
    monkeypatch.setattr(dialog, '_ascii_rule_dialog', lambda *_: validate_rule('^README$', True))
    dialog._edit_priority_rule(False)
    item = dialog.priority_list.item(5)
    assert not item.icon().isNull() and item.data(Qt.ItemDataRole.UserRole)['regex']
    assert not dialog.priority_down.isEnabled()
    monkeypatch.setattr(dialog, '_ascii_rule_dialog', lambda *_: validate_rule('*.txt'))
    dialog._edit_priority_rule(True)
    assert dialog.priority_list.item(5).text() == '*.txt'
    assert dialog.priority_list.item(5).icon().isNull()
    while dialog.priority_list.count():
        dialog.priority_list.setCurrentRow(0)
        dialog._delete_priority_rule()
    assert window.preferences.value('Transfer/priority_rules') == '[]'
    assert TransferOptions.from_settings(window.preferences).priority('file.sfv') == 0
    dialog.close()
    dialog = PreferencesDialog(window)
    assert dialog.priority_list.count() == 0
    dialog.close()


def test_reorder_affects_waiting_queue(window):
    from test_queue import job_for
    from test_ui import settle
    from fluxftp.preferences import PreferencesDialog
    window.preferences.setValue('Transfer/single_connection', True)
    window.paused = True
    order = []
    for name in ('check.sfv', 'info.nfo'):
        job_for(window, lambda _, name=name: order.append(name), name=name)
    dialog = PreferencesDialog(window)
    dialog.priority_list.setCurrentRow(1)
    dialog._move_priority_rule(-1)
    dialog.close()
    window.play_queue()
    settle(window)
    assert order == ['info.nfo', 'check.sfv']
