from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTabWidget
from fluxftp.core import PROTOCOLS
from test_queue import window
from test_server_commands import ready
from test_integration import servers
from test_ui import settle


def test_command_bar_layout_and_connection_state(window):
    window.show()
    window.command_commands.update()
    assert not window.ftp_command_input.isEnabled()
    pane = ready(window)
    window.command_commands.update()
    assert window.ftp_command_input.isEnabled()
    assert window.ftp_command_input.lineEdit().placeholderText() == 'FTP Command'
    assert window.ftp_command_input.parentWidget() is window.ftp_log.parentWidget()
    assert window.log_tabs.tabPosition() == QTabWidget.TabPosition.South
    assert window.ftp_command_input.geometry().bottom() < window.ftp_log.geometry().top()
    pane.site.protocol = PROTOCOLS[3]
    window.command_commands.update()
    assert not window.ftp_command_send.isEnabled()


def test_enter_cancel_and_no_history(window, monkeypatch):
    ready(window)
    window.command_commands.update()
    window.show()
    window.activateWindow()
    combo = window.ftp_command_input
    calls = []
    monkeypatch.setattr(window.command_commands, 'prepare', lambda *args, **kwargs: calls.append(args) or True)
    combo.setEditText('SITE PASSWD test%secret')
    combo.lineEdit().setFocus()
    QTest.keyClick(combo.lineEdit(), Qt.Key.Key_Return)
    assert len(calls) == 1 and calls[0][2] == ['SITE PASSWD test%secret']
    assert combo.currentText() == ''
    assert combo.findText('SITE PASSWD test%secret') == -1
    monkeypatch.setattr(window.command_commands, 'prepare', lambda *args, **kwargs: None)
    combo.setEditText('NOOP')
    window.ftp_command_send.click()
    assert combo.currentText() == 'NOOP'


def test_inline_command_real_response_in_log(window, servers, monkeypatch):
    _, site = servers(False)
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    pane.refresh()
    settle(window)
    pane.parentWidget().mark_pane = pane
    window.command_commands.update()
    monkeypatch.setattr(window.command_commands, 'review', lambda *_: True)
    reports = []
    monkeypatch.setattr(window.ftp_commands, 'report', lambda *args: reports.append(args))
    window.ftp_command_input.setEditText('NOOP')
    window.ftp_command_send.click()
    settle(window)
    assert 'FTP > NOOP' in window.ftp_log.toPlainText()
    assert 'Completed 1 command(s)' in window.ftp_log.toPlainText()
    assert not reports
    assert window.ftp_command_input.currentText() == ''


def test_main_toolbar_has_ftp_rush_style_actions_and_terminal_focus(window, monkeypatch):
    actions = {action.text(): action for action in window.main_toolbar.actions() if action.text()}
    expected = {'Sites', 'Options', 'Refresh Both', 'Start Queue', 'Stop Queue',
                'Log Window', 'Transfer Window', 'FTP Terminal', 'New workspace', 'Verify SFV'}
    assert expected <= actions.keys()
    assert all(not actions[name].icon().isNull() for name in expected)

    refreshed = []
    for pane in window.current_panes():
        monkeypatch.setattr(pane, 'refresh', lambda pane=pane: refreshed.append(pane))
    actions['Refresh Both'].trigger()
    assert refreshed == window.current_panes()

    window.show()
    QTest.qWait(20)
    ready(window)
    window.command_commands.update()
    window.view_commands.open_ftp_terminal()
    assert window.log_tabs.currentWidget() is window.log_panels['ftp']
    assert window.ftp_command_input.lineEdit().hasFocus()
