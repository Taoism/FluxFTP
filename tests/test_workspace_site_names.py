import json

from fluxftp.core import Site
from test_queue import window


def connect(pane, name, monkeypatch):
    monkeypatch.setattr(pane.window, 'run', lambda task, done, failed: done(([], 'Test connection')))
    pane.set_remote(Site(name=name, host=name.lower() + '.test'), 'password')
    pane.refresh()


def test_connected_names_follow_pane_order_and_disconnect(window, monkeypatch):
    left, right = window.current_panes()
    connect(right, 'Beta', monkeypatch)
    assert window.tabs.tabText(0) == 'Beta'
    connect(left, 'Alpha', monkeypatch)
    assert window.tabs.tabText(0) == 'Alpha - Beta'
    assert window.window_list.topLevelItem(0).text(0) == 'Alpha - Beta'
    assert json.loads(window.preferences.value('Workspaces/session'))['items'][0]['name'] == 'Alpha - Beta'
    left.set_remote(None)
    assert window.tabs.tabText(0) == 'Beta'


def test_failed_connection_does_not_rename_workspace(window, monkeypatch):
    pane = window.current_panes()[0]
    original = window.tabs.tabText(0)
    monkeypatch.setattr(window, 'error', lambda message: None)
    monkeypatch.setattr(window, 'run', lambda task, done, failed: failed('Connection refused'))
    pane.set_remote(Site(name='Unavailable', host='unavailable.test'), 'password')
    pane.refresh()
    assert window.tabs.tabText(0) == original


def test_connection_updates_own_workspace_and_refresh_keeps_manual_name(window, monkeypatch):
    first = window.current_panes()[0]
    window.add_workspace()
    other_name = window.tabs.tabText(1)
    connect(first, 'Alpha', monkeypatch)
    assert window.tabs.tabText(0) == 'Alpha'
    assert window.tabs.tabText(1) == other_name
    window.tabs.setTabText(0, 'Custom name')
    first.refresh()
    assert window.tabs.tabText(0) == 'Custom name'
    connect(first, 'Gamma', monkeypatch)
    assert window.tabs.tabText(0) == 'Gamma'
