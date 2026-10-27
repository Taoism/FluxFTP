from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import pytest
from fluxftp.core import Site, Entry
from test_queue import window
from test_integration import servers
from test_ui import settle


@pytest.mark.parametrize('first_click', [False, True])
def test_remote_folder_double_click_without_prior_selection(window, monkeypatch, first_click):
    pane = window.current_panes()[0]
    pane.set_remote(Site(name='remote', host='example.test'), '', '/')
    pane.show_entries(([Entry('folder', True)], ''))
    window.show()
    window.activateWindow()
    QTest.qWait(20)
    paths = []
    monkeypatch.setattr(pane, 'navigate', paths.append)
    row = pane.tree.topLevelItem(0)
    position = pane.tree.visualItemRect(row).center()
    if first_click:
        QTest.mouseClick(pane.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(pane.tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert paths == ['/folder']


@pytest.mark.parametrize('tls', [False, True])
def test_double_click_enters_real_remote_folder(window, servers, tls):
    root, site = servers(tls)
    (root / 'folder').mkdir()
    (root / 'folder' / 'inside.txt').write_text('inside')
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    pane.refresh()
    settle(window)
    connection = pane.browse_session.connection
    assert connection is not None
    window.show()
    window.activateWindow()
    QTest.qWait(20)
    row = pane.tree.topLevelItem(0)
    QTest.mouseClick(pane.tree.viewport(), Qt.MouseButton.LeftButton, pos=pane.tree.visualItemRect(row).center())
    QTest.mouseDClick(pane.tree.viewport(), Qt.MouseButton.LeftButton, pos=pane.tree.visualItemRect(row).center())
    settle(window)
    assert pane.path == '/folder'
    assert pane.browse_session.connection is connection
    assert pane.address.text() == '/folder'
    assert pane.tree.topLevelItemCount() == 1
    assert pane.tree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole).name == 'inside.txt'
    assert not window.queue_jobs


def test_folder_double_click_busy_guard(window, monkeypatch):
    pane = window.current_panes()[0]
    pane.set_remote(Site(name='remote', host='example.test'), '', '/')
    pane.show_entries(([Entry('folder', True)], ''))
    window.show()
    QTest.qWait(20)
    paths = []
    monkeypatch.setattr(pane, 'navigate', paths.append)
    pane.busy = True
    row = pane.tree.topLevelItem(0)
    QTest.mouseDClick(pane.tree.viewport(), Qt.MouseButton.LeftButton, pos=pane.tree.visualItemRect(row).center())
    pane.busy = False
    assert not paths
