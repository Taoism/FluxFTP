import json
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from fluxftp.core import Site, Entry
from test_queue import window


def remote(window, name='one', host='one.example'):
    pane = window.current_panes()[0]
    pane.set_remote(Site(name=name, host=host), '', '/')
    pane.show_entries(([], ''))
    return pane


def test_recents_deduplicate_cap_and_keep_sites_separate(window):
    pane = remote(window)
    first = pane.address.scope()
    for index in range(30):
        pane.path = f'/folder{index}'
        pane.show_entries(([], ''))
    pane.path = '/folder10'
    pane.show_entries(([], ''))
    history = pane.address.history()[first]
    assert len(history) == 25 and history[0] == '/folder10'
    assert history.count('/folder10') == 1
    remote(window, 'two', 'two.example')
    assert pane.address.scope() != first
    assert pane.address.history()[pane.address.scope()] == ['/']
    assert pane.address.history()[first] == history


def test_bookmarks_and_recent_items_navigate_and_guard_stale_scope(window, monkeypatch):
    pane = remote(window)
    window.preferences.setValue('Bookmarks/sites', json.dumps({'one': [{'name': 'Music', 'path': '/music'}]}))
    pane.address.populate_paths()
    actions = pane.address.path_menu.actions()
    bookmark = next(a for a in actions if a.text() == 'Music — /music')
    paths = []
    monkeypatch.setattr(pane, 'navigate', paths.append)
    bookmark.trigger()
    assert paths == ['/music']
    pane.busy = True
    bookmark.trigger()
    assert paths == ['/music']
    pane.busy = False
    pane.site = Site(name='other', host='other.example')
    bookmark.trigger()
    assert paths == ['/music']


def test_dropdown_keyboard_and_editable_path(window, monkeypatch):
    pane = remote(window)
    window.show()
    window.activateWindow()
    pane.address.setFocus()
    QTest.qWait(20)
    QTest.keyClick(pane.address, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert pane.address.path_menu.isVisible()
    pane.address.path_menu.hide()
    paths = []
    monkeypatch.setattr(pane, 'navigate', paths.append)
    pane.address.setText('/typed/path')
    QTest.keyClick(pane.address, Qt.Key.Key_Return)
    assert paths == ['/typed/path']


def test_corrupt_history_and_disconnected_menu(window):
    pane = window.current_panes()[0]
    window.preferences.setValue('General/path_history', 'invalid json')
    pane.set_remote(None, '', '/')
    pane.address.populate_paths()
    assert all(not a.isEnabled() for a in pane.address.path_menu.actions() if not a.isSeparator())
