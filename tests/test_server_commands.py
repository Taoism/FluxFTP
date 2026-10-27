from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QTreeWidget, QPlainTextEdit, QPushButton, QInputDialog

from fluxftp.command_catalog import BUILTIN_COMMANDS
from fluxftp.command_menu import catalog_entries
from fluxftp.core import Entry, Cancelled, Site, PROTOCOLS, Connection
from fluxftp.diagnostics import Trace
from fluxftp.server_commands import compile_commands, execute_commands, public_text, CommandPlan
from test_queue import window
from test_integration import servers


def test_all_reference_templates_compile():
    assert [(g['name'], len(list(catalog_entries([g])))) for g in BUILTIN_COMMANDS] == [
        ('glFTPd', 48), ('glFTPd admin', 37), ('ioFTPD', 205), ('RaidenFTPD', 59), ('Wing FTP Server', 5)]
    for key, lines in catalog_entries(BUILTIN_COMMANDS):
        plan = compile_commands(lines, [Entry('selected.bin', False, user='owner')], lambda *_: 'argument')
        assert plan.lines, key


def test_expansion_references_selection_and_no_recursive_substitution():
    answers = iter(['admins', 'a%b', 'secret%f'])
    plan = compile_commands(['SITE GRPADD %d[Group]', 'SITE ADDUSER %d[User] %d[Password]',
                             'SITE CHANGE %2 ADMINGROUP %1', 'SITE CHOWN %o %f'],
                            [Entry('one%f', False, user='owner1'), Entry('two', False, user='owner2')],
                            lambda *_: next(answers))
    assert plan.lines[:4] == ('SITE GRPADD admins', 'SITE ADDUSER a%b secret%f',
                            'SITE CHANGE a%b ADMINGROUP admins', 'SITE CHOWN owner1 one%f')
    assert plan.lines[-1] == 'SITE CHOWN owner2 two'
    assert 'secret%f' not in public_text('\n'.join(plan.lines), plan.secrets)


@pytest.mark.parametrize('lines,entries,prompt', [
    (['SITE WHO\r\nSITE WIPE /'], [], lambda *_: ''),
    (['SITE USER %d[User]'], [], lambda *_: 'x\r\nSITE WIPE /'),
    (['SITE UINFO %o'], [Entry('x', False)], lambda *_: ''),
    (['SITE CAT %f'], [], lambda *_: ''),
    (['SITE USER %1'], [], lambda *_: ''),
    (['SITE USER %z'], [], lambda *_: ''),
    (['SITE CAT %f'], [Entry('x\nSITE WIPE /', False)], lambda *_: ''),
])
def test_invalid_arguments_do_not_compile(lines, entries, prompt):
    with pytest.raises(ValueError):
        compile_commands(lines, entries, prompt)


def test_cancelled_prompt_sends_nothing():
    with pytest.raises(Cancelled):
        compile_commands(['SITE WHO', 'SITE UINFO %d[User]'], [], lambda *_: None)
    with pytest.raises(ValueError, match='more than 1000'):
        compile_commands(['SITE CAT %f'], [Entry('file', False)] * 1001, lambda *_: '')


def fake_connection(fail=None):
    calls = []
    def send(line):
        calls.append(line)
        if fail and fail in line:
            raise RuntimeError('550 rejected secret-token')
        return '200 accepted secret-token'
    def listing(line, callback):
        calls.append(line)
        callback('file.txt')
        return '226 complete'
    return SimpleNamespace(sftp=None, ftp=SimpleNamespace(
        cwd=lambda path: calls.append(('cwd', path)), sendcmd=send, retrlines=listing), trace=Trace()), calls


def test_sequential_execution_listing_failure_and_secrets():
    connection, calls = fake_connection('FAIL')
    plan = CommandPlan(('&window', 'SITE PASSWD secret-token', 'LIST', 'SITE FAIL', 'SITE NEVER'), ('secret-token',))
    result = execute_commands(connection, '/folder', plan, Event())
    assert calls == [('cwd', '/folder'), 'SITE PASSWD secret-token', 'LIST', 'SITE FAIL']
    assert 'Stopped after 2 completed' in result and 'secret-token' not in result
    assert 'file.txt' in result
    assert 'secret-token' in connection.trace.secrets


def test_cancellation_no_command_and_output_limit():
    connection, calls = fake_connection()
    cancel = Event()
    cancel.set()
    result = execute_commands(connection, '/', CommandPlan(('SITE WHO',), ()), cancel)
    assert not calls and 'Cancelled' in result
    connection.ftp.sendcmd = lambda _: '200 ' + 'x' * 300_000
    result = execute_commands(connection, '/', CommandPlan(('SITE WHO',), ()), Event())
    assert 'Output truncated' in result and len(result) < 257_000


@pytest.mark.parametrize('tls', [False, True])
def test_real_ftp_and_ftps_control_and_listing(servers, tls):
    root, site = servers(tls)
    (root / 'folder').mkdir()
    (root / 'folder' / 'file.txt').write_text('contents')
    with Connection(site, 'password') as connection:
        result = execute_commands(connection, '/folder', CommandPlan(('PWD', 'LIST', 'NOOP'), ()), Event())
    assert 'Completed 3 command(s)' in result
    assert 'file.txt' in result and '/folder' in result


@pytest.mark.parametrize('tls', [False, True])
def test_real_site_commands_multiline_and_failure(servers, tls, monkeypatch):
    from pyftpdlib.handlers import FTPHandler
    def site_help(handler, line):
        handler.push('214-First line\r\nSecond line\r\n')
        handler.respond('214 End')
    monkeypatch.setattr(FTPHandler, 'ftp_SITE_HELP', site_help)
    _, site = servers(tls)
    with Connection(site, 'password') as connection:
        result = execute_commands(connection, '/', CommandPlan(('SITE HELP', 'SITE NOTSUPPORTED', 'NOOP'), ()), Event())
    assert 'First line\nSecond line\n214 End' in result
    assert 'Stopped after 1 completed' in result
    assert '> NOOP' not in result


def ready(window):
    pane = window.current_panes()[0]
    pane.set_remote(Site(name='test', host='example.test'), 'password', '/')
    pane.show_entries(([Entry('file.txt', False, user='owner')], ''))
    pane.parentWidget().mark_pane = pane
    return pane


def test_menu_groups_dispatch_and_gating(window, monkeypatch):
    menu = window.command_commands
    assert [a.text() for a in menu.menu.actions()] == [
        'glFTPd', 'glFTPd admin', 'ioFTPD', 'RaidenFTPD', 'Wing FTP Server', 'DrFTPD']
    assert len(menu.actions) == 512
    assert menu.designer_action.shortcut().toString() == 'F4'
    assert menu.raw_action.shortcut().toString() == 'Ctrl+R'
    menu.update()
    assert not menu.actions['glFTPd/site who'].isEnabled()

    pane = ready(window)
    menu.update()
    assert menu.actions['glFTPd/site who'].isEnabled()
    assert not menu.actions['Wing FTP Server/XCRC'].isEnabled()
    pane.tree.topLevelItem(0).setSelected(True)
    menu.update()
    assert menu.actions['Wing FTP Server/XCRC'].isEnabled()
    calls = []
    monkeypatch.setattr(menu, 'prepare', lambda snapshot, key, lines: calls.append((key, lines)))
    menu.actions['glFTPd/site who'].trigger()
    assert calls == [('glFTPd/site who', ['site who'])]
    pane.site.protocol = PROTOCOLS[3]
    menu.update()
    assert not menu.raw_action.isEnabled()
    assert not menu.actions['glFTPd/site who'].isEnabled()


def test_command_keyboard_shortcuts(window, monkeypatch):
    from PySide6.QtTest import QTest
    pane = ready(window)
    window.command_commands.update()
    calls = []
    monkeypatch.setattr(QDialog, 'exec', lambda dialog: calls.append(dialog.windowTitle()) or QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *_: calls.append('raw') or ('', False))
    window.show()
    window.activateWindow()
    pane.tree.setFocus()
    QTest.qWait(20)
    QTest.keyClick(pane.tree, Qt.Key.Key_F4)
    QTest.keyClick(pane.tree, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert calls == ['Command Designer', 'raw']


def test_review_cancel_stale_selection_and_dispatch(window, monkeypatch):
    pane = ready(window)
    menu = window.command_commands
    calls = []
    monkeypatch.setattr(window.ftp_commands, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(menu, 'review', lambda *_: False)
    menu.invoke('glFTPd/site who')
    assert not calls
    def stale(*_):
        pane.path = '/changed'
        return True
    monkeypatch.setattr(menu, 'review', stale)
    menu.invoke('glFTPd/site who')
    assert not calls
    monkeypatch.setattr(menu, 'review', lambda *_: True)
    menu.invoke('glFTPd/site who')
    assert len(calls) == 1
    connection, sent = fake_connection()
    result = calls[0][0][2](connection, Event())
    assert sent == [('cwd', '/changed'), 'site who']
    assert 'Completed 1' in result


def test_ui_command_runs_and_reports_real_server_response(window, servers, monkeypatch):
    from test_ui import settle
    root, site = servers(False)
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    pane.refresh()
    settle(window)
    pane.parentWidget().mark_pane = pane
    reports = []
    monkeypatch.setattr(window.command_commands, 'review', lambda *_: True)
    monkeypatch.setattr(window.ftp_commands, 'report', lambda title, text: reports.append((title, text)))
    window.command_commands.invoke('glFTPd/infrequent/site help')
    settle(window)
    assert not pane.busy
    assert reports and 'Completed 1 command(s)' in reports[0][1]


def test_raw_percent_is_literal_and_password_masked(window, monkeypatch):
    ready(window)
    menu = window.command_commands
    plans = []
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *_: ('SITE PASSWD abc%def', True))
    monkeypatch.setattr(menu, 'review', lambda snapshot, title, plan: plans.append(plan) or False)
    menu.raw()
    assert plans[0].lines == ('SITE PASSWD abc%def',)
    assert 'abc%def' not in public_text(plans[0].lines[0], plans[0].secrets)


def test_designer_edit_and_restore_persistence(window, monkeypatch):
    menu = window.command_commands
    def dialog_exec(dialog):
        tree = dialog.findChild(QTreeWidget)
        editor = dialog.findChild(QPlainTextEdit)
        def find(row):
            if row.data(0, Qt.ItemDataRole.UserRole) == 'glFTPd/site who':
                return row
            for i in range(row.childCount()):
                match = find(row.child(i))
                if match:
                    return match
        tree.setCurrentItem(find(tree.invisibleRootItem()))
        editor.setPlainText('SITE WHO\nSITE HELP')
        next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Save changes').click()
        assert menu.lines('glFTPd/site who') == ['SITE WHO', 'SITE HELP']
        assert 'SITE HELP' in window.preferences.value('Commands/templates')
        next(b for b in dialog.findChildren(QPushButton) if b.text().startswith('Restore default')).click()
        assert menu.lines('glFTPd/site who') == ['site who']
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, 'exec', dialog_exec)
    menu.designer()


def test_custom_commands_reload_and_literal_credentials_refused(window, monkeypatch):
    from fluxftp.command_menu import CommandMenu
    from PySide6.QtWidgets import QMenu
    menu = window.command_commands
    menu.custom['My command'] = ['SITE HELP']
    menu.save()
    restored = CommandMenu(window, QMenu(window))
    assert restored.custom == {'My command': ['SITE HELP']}
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    def dialog_exec(dialog):
        tree = dialog.findChild(QTreeWidget)
        custom_group = tree.topLevelItem(tree.topLevelItemCount()-1)
        tree.setCurrentItem(custom_group.child(0))
        dialog.findChild(QPlainTextEdit).setPlainText('SITE ADDUSER %d[User] literal-secret')
        next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Save changes').click()
        assert errors and 'literal-secret' not in window.preferences.value('Commands/templates')
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, 'exec', dialog_exec)
    menu.designer()
