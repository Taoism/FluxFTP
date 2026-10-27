import os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QMimeData, QUrl, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTreeWidgetItem, QPlainTextEdit

from fluxftp.core import Entry, Site
from fluxftp.mouse_options import CHOICES, DOUBLE_CLICK_DEFAULTS
from fluxftp.preferences import PreferencesDialog
from fluxftp.queue_manager import read_queue
from fluxftp.remote_editor import text_payload, open_remote_text, MAX_TEXT_BYTES
from fluxftp.site_manager import SitesDialog
from test_queue import window
from test_integration import servers
from test_ui import settle


def local_pair(window, tmp_path):
    window.initialize_queue_storage(tmp_path / 'queue.json')
    window.preferences.setValue('Confirm/queue', False)
    left, right = window.current_panes()
    a, b = tmp_path / 'source', tmp_path / 'target'
    a.mkdir()
    b.mkdir()
    for pane, path in ((left, a), (right, b)):
        pane.local_path = str(path)
        pane.change_mode('Local')
    return left, right, a, b


def test_mouse_choices_defaults_and_persistence(window):
    dialog = PreferencesDialog(window)
    assert {key: dialog.mouse_combos[key].currentText() for key in DOUBLE_CLICK_DEFAULTS} == DOUBLE_CLICK_DEFAULTS
    for key, choices in CHOICES.items():
        control = dialog.mouse_combos[key]
        assert [control.itemText(i) for i in range(control.count())] == choices
        control.setCurrentIndex(len(choices) - 1)
    assert dialog.mouse_paste.isChecked()
    dialog.mouse_paste.setChecked(False)
    dialog.close()
    again = PreferencesDialog(window)
    assert not again.mouse_paste.isChecked()
    assert all(again.mouse_combos[key].currentText() == choices[-1] for key, choices in CHOICES.items())
    again.close()


def test_hold_survives_other_transfers_until_play(window, tmp_path):
    left, right, a, b = local_pair(window, tmp_path)
    (a / 'held.txt').write_text('held')
    (a / 'now.txt').write_text('now')
    window.enqueue_items(left, right, [Entry('held.txt', False, 4)], start=False)
    held = window.pending[0]
    assert held['held'] and not window.active and not (b / 'held.txt').exists()
    assert read_queue(window.queue_file)[0]['held']
    window.enqueue_items(left, right, [Entry('now.txt', False, 3)], start=True)
    for _ in range(200):
        QTest.qWait(10)
        if not window.workers and not window.active:
            break
    assert (b / 'now.txt').read_text() == 'now'
    assert not (b / 'held.txt').exists() and held in window.pending
    window.play_queue()
    settle(window)
    assert (b / 'held.txt').read_text() == 'held'


def test_file_double_click_dispatch_and_folder_navigation(window, tmp_path, monkeypatch):
    left, right, a, b = local_pair(window, tmp_path)
    (a / 'data.txt').write_text('hello')
    (a / 'folder').mkdir()
    calls = []
    monkeypatch.setattr(window, 'enqueue_items', lambda *args, **kwargs: calls.append((args, kwargs)))
    entry = Entry('data.txt', False, 5)
    for action, start in [('Begin Transfer', True), ('Add to Queue', False)]:
        window.preferences.setValue('Mouse/local_file', action)
        window.mouse_file(left, entry)
        assert calls[-1][0] == (left, right, [entry]) and calls[-1][1] == {'start': start}
    opened = []
    monkeypatch.setattr('fluxftp.mouse_actions.QDesktopServices.openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    window.preferences.setValue('Mouse/local_file', 'Execute File')
    window.mouse_file(left, entry)
    assert [Path(path) for path in opened] == [a / 'data.txt']
    window.preferences.setValue('Mouse/local_file', '(None)')
    row = QTreeWidgetItem(['folder'])
    row.setData(0, Qt.ItemDataRole.UserRole, Entry('folder', True, 0))
    left.open_item(row)
    assert Path(left.path) == a / 'folder'
    left.busy = True
    window.mouse_file(left, entry, 'Begin Transfer')
    assert len(calls) == 2
    left.busy = False


def test_paste_gate_and_local_urls(window, tmp_path, monkeypatch):
    pane = window.current_panes()[0]
    pane.site = Site(host='example.test')
    calls = []
    monkeypatch.setattr(window, 'enqueue_local_paths', lambda *a, **kw: calls.append((a, kw)))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / 'source.txt'))])
    QApplication.clipboard().setMimeData(mime)
    assert window.paste_local_files(pane)
    assert len(calls) == 1 and calls[0][1] == {'start': True}
    window.preferences.setValue('Mouse/allow_paste', False)
    window.paste_local_files(pane)
    assert len(calls) == 1
    window.preferences.setValue('Mouse/allow_paste', True)
    bad = QMimeData()
    bad.setUrls([QUrl('https://example.test/file')])
    QApplication.clipboard().setMimeData(bad)
    window.paste_local_files(pane)
    assert len(calls) == 1
    QApplication.clipboard().clear()


def test_site_double_click_cancels_single_click(window, monkeypatch):
    window.sites = {'test': asdict(Site(name='test', host='example.test'))}
    manager = SitesDialog(window)
    row = manager.tree.topLevelItem(0)
    connections, properties = [], []
    monkeypatch.setattr(manager, 'connect_to', lambda *a: connections.append(a))
    monkeypatch.setattr(manager, 'edit', lambda: properties.append(True))
    manager.tree.itemClicked.emit(row, 0)
    manager.tree.itemDoubleClicked.emit(row, 0)
    QTest.qWait(QApplication.doubleClickInterval() + 30)
    assert properties == [True] and not connections
    window.preferences.setValue('Mouse/site', 'Connect to Site')
    manager.tree.itemClicked.emit(row, 0)
    manager.tree.itemDoubleClicked.emit(row, 0)
    QTest.qWait(QApplication.doubleClickInterval() + 30)
    assert connections == [(0, 'test')]
    manager.close()


def test_log_actions_and_empty_tabs(window):
    window.show()
    QTest.qWait(30)
    sizes = window.work_area.sizes()
    QTest.mouseDClick(window.log.viewport(), Qt.MouseButton.LeftButton)
    assert window.work_area.sizes()[0] == 0 and window.work_area.sizes()[2] == 0
    window.mouse_log(window.log)
    assert all(value > 0 for value in window.work_area.sizes())
    assert all(abs(a - b) < 30 for a, b in zip(window.work_area.sizes(), sizes))
    window.preferences.setValue('Mouse/log', 'View log at new window')
    window.log.setPlainText('test log')
    window.mouse_log(window.log)
    dialog = window.mouse_windows[-1]
    assert dialog.findChild(QPlainTextEdit).toPlainText() == 'test log'
    assert dialog.findChild(QPlainTextEdit).isReadOnly()
    dialog.close()
    QTest.qWait(30)
    window.preferences.setValue('Mouse/empty_tabs', 'Create New Window')
    before = window.tabs.count()
    QTest.mouseDClick(window.workspace_navigation, Qt.MouseButton.LeftButton, pos=QPoint(window.workspace_navigation.width() - 2, 2))
    QTest.qWait(30)
    assert window.tabs.count() == before + 1
    window.preferences.setValue('Layout/multiple_lines', False)
    window.apply_layout_settings()
    bar = window.tabs.tabBar()
    bar.resize(1000, bar.height())
    QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(999, 5))
    QTest.qWait(30)
    assert window.tabs.count() == before + 2


def test_edit_queue_validates_and_source_navigation(window, tmp_path):
    left, right, a, b = local_pair(window, tmp_path)
    (a / 'one.txt').write_text('one')
    (a / 'two.txt').write_text('two')
    window.enqueue_items(left, right, [Entry('one.txt', False, 3)], start=False)
    job = window.pending[0]
    with pytest.raises(ValueError):
        window.update_mouse_queue(job, str(a / 'one.txt'), str(a / 'one.txt'), False)
    with pytest.raises(ValueError):
        window.update_mouse_queue(job, 'relative', str(b / 'two.txt'), False)
    window.update_mouse_queue(job, str(a / 'two.txt'), str(b / 'renamed.txt'), False)
    assert read_queue(window.queue_file)[0]['dst'] == str(b / 'renamed.txt')
    left.navigate(str(tmp_path))
    window.preferences.setValue('Mouse/queue_item', 'Enter Source Folder')
    window.mouse_queue(job['row'])
    assert Path(left.path) == a
    window.play_queue()
    settle(window)
    assert (b / 'renamed.txt').read_text() == 'two'
    with pytest.raises(ValueError):
        window.update_mouse_queue(job, str(a / 'one.txt'), str(b / 'one.txt'), False)


@pytest.mark.parametrize('tls', [False, True])
def test_remote_view_edit_and_changed_file_refusal(window, servers, tls, monkeypatch):
    root, site = servers(tls)
    file = root / 'test.txt'
    file.write_bytes(b'\xef\xbb\xbforiginal\r\n')
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    open_remote_text(window, pane, Entry('test.txt', False, file.stat().st_size), True)
    settle(window)
    dialog = window.mouse_windows[-1]
    dialog.editor.setPlainText('changed\n')
    dialog.editor.document().setModified(True)
    dialog.save_remote()
    settle(window)
    assert file.read_bytes() == b'\xef\xbb\xbfchanged\r\n'
    assert not dialog.editor.document().isModified()
    file.write_bytes(b'outside change')
    dialog.editor.setPlainText('my edit')
    dialog.editor.document().setModified(True)
    dialog.save_remote()
    settle(window)
    assert file.read_bytes() == b'outside change'
    assert 'changed since' in dialog.status.text()
    assert dialog.editor.document().isModified()
    dialog.editor.document().setModified(False)
    dialog.close()
    open_remote_text(window, pane, Entry('test.txt', False, file.stat().st_size), False)
    settle(window)
    viewer = window.mouse_windows[-1]
    assert viewer.editor.isReadOnly() and viewer.editor.toPlainText() == 'outside change'
    viewer.close()


@pytest.mark.parametrize('data', [b'\x00binary', b'\xffinvalid', b'a' * (MAX_TEXT_BYTES + 1)], ids=['binary', 'invalid_utf8', 'oversize'])
def test_text_viewer_rejects_binary_invalid_and_large(data):
    with pytest.raises(ValueError):
        text_payload(data)


@pytest.mark.skipif(os.name != 'nt', reason='Windows Shell integration')
def test_real_explorer_menu_creation_and_cleanup(window, tmp_path):
    from fluxftp.explorer_menu import ExplorerMenu
    paths = [tmp_path / 'one.txt', tmp_path / 'two.txt']
    for path in paths:
        path.write_text('text')
    menu = ExplorerMenu(paths, int(window.winId()))
    try:
        assert any(label for label in menu.labels())
        assert menu.context and menu.folder and menu.pidls
    finally:
        menu.close()
    assert not menu.context and not menu.folder and not menu.pidls and not menu.menu


def test_explorer_context_dispatch(window, tmp_path, monkeypatch):
    from fluxftp import explorer_menu
    left, right, a, b = local_pair(window, tmp_path)
    (a / 'one.txt').write_text('one')
    left.refresh()
    calls = []
    monkeypatch.setattr(explorer_menu, 'show_explorer_menu', lambda *args: calls.append(args))
    window.preferences.setValue('Mouse/local_context', 'Explorer Menu')
    window.mouse_context(left, QPoint(-1, -1), QPoint(1, 1))
    assert calls[0][0] == [str(a)]
    window.preferences.setValue('Mouse/local_context', 'Default popup menu')
    assert 'Refresh' in [action.text() for action in window.default_file_menu(left).actions()]


def test_external_drop_holds_and_recovery_retains_it(window, tmp_path):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QDropEvent
    left, right, a, b = local_pair(window, tmp_path)
    file = a / 'drop.txt'
    file.write_text('drop')
    window.preferences.setValue('Mouse/explorer_drop', 'Add to Queue')
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file))])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    right.tree.dropEvent(event)
    assert event.isAccepted() and len(window.pending) == 1 and window.pending[0]['held']
    assert not (b / file.name).exists()
    window.pending.clear()
    window.queue_jobs.clear()
    window.queue.clear()
    window.initialize_queue_storage(tmp_path / 'queue.json')
    assert window.paused and window.pending[0]['held']
    window.play_queue()
    settle(window)
    assert (b / file.name).read_text() == 'drop'


def test_paste_key_in_new_workspace_and_context_event(window, tmp_path, monkeypatch):
    from PySide6.QtGui import QContextMenuEvent
    window.add_workspace()
    pane = window.current_panes()[0]
    pane.site = Site(host='example.test')
    calls = []
    monkeypatch.setattr(window, 'enqueue_local_paths', lambda *args, **kw: calls.append((args, kw)))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / 'file.txt'))])
    QApplication.clipboard().setMimeData(mime)
    QTest.keyClick(pane.tree, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert len(calls) == 1
    contexts = []
    monkeypatch.setattr(window, 'mouse_context', lambda *args: contexts.append(args))
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(3, 3), QPoint(30, 30))
    QApplication.sendEvent(pane.tree.viewport(), event)
    assert len(contexts) == 1 and contexts[0][0] is pane
    QApplication.clipboard().clear()


def test_source_folder_after_original_workspace_closed(window, tmp_path):
    left, right, a, b = local_pair(window, tmp_path)
    file = a / 'file.txt'
    file.write_text('text')
    window.enqueue_items(left, right, [Entry(file.name, False, 4)], start=True)
    settle(window)
    job = window.queue_jobs[-1]
    original = job['workspace']
    window.add_workspace()
    window.close_tab(window.tabs.indexOf(original))
    QTest.qWait(30)
    window.enter_queue_source(job)
    assert Path(window.current_panes()[0].path) == a
