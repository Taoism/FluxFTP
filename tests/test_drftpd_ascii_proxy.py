import socket
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QPlainTextEdit

from fluxftp import diagnostics
from fluxftp.command_menu import catalog_entries
from fluxftp.drftpd_commands import DRFTPD_COMMANDS
from fluxftp.server_commands import compile_commands
from fluxftp.core import Entry, LoggedFTP
from test_queue import window


def test_drftpd_catalog_compiles_and_is_in_designer(window):
    commands = dict(catalog_entries([DRFTPD_COMMANDS]))
    assert len(commands) == 158
    for key, lines in commands.items():
        assert compile_commands(lines, [Entry('file', False, user='owner')], lambda *_: 'argument').lines
        assert key in window.command_commands.defaults
        assert key in window.command_commands.actions
    for command in ('SITE ADDUSER', 'SITE SLAVES', 'SITE CHPASS', 'SITE HELP', 'SITE REQUEST', 'SITE RESCAN'):
        assert any(key.endswith('/' + command) for key in commands)


def test_ascii_banner_newlines_spacing_and_disk_safety(window, tmp_path):
    path = diagnostics.configure_debug(tmp_path / 'debug.log')
    try:
        trace = diagnostics.Trace('secret-value')
        trace.listener = lambda message, level: window.append_ftp_message(message, 1)
        banner = 'FTP < 230-   /\\   \n230-  (  )  \n230 secret-value logged in.'
        trace.event(banner)
        text = window.ftp_log.toPlainText()
        assert '[1] FTP < 230-   /\\   \n[1] FTP < 230-  (  )  ' in text
        assert 'secret-value' not in text
        assert '\\x0a' not in text
        assert '\\x0a' in path.read_text(encoding='utf-8')
        trace.event(r'FTP < literal \x0a is not a newline' + '\x1b[31m')
        assert r'literal \x0a is not a newline\x1b[31m' in window.ftp_log.toPlainText()
        assert window.ftp_log.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
        assert window.ftp_log.font().fixedPitch()
    finally:
        diagnostics.close_debug()


@pytest.mark.parametrize('override', [True, False])
def test_proxy_passive_route_and_actionable_failure(monkeypatch, override):
    import fluxftp.core as core
    ftp = LoggedFTP(diagnostics.Trace(), proxy={'type': 'SOCKS5'})
    ftp.af = socket.AF_INET
    ftp.host = '95.131.202.127'
    ftp.smart_pasv = override
    ftp.passiveserver = True
    ftp.sendcmd = lambda _: '227 Entering Passive Mode (205,147,201,254,198,46)'
    calls = []
    def refuse(host, port, *args):
        calls.append((host, port))
        raise OSError('SOCKS5 connection refused (reply 5)')
    monkeypatch.setattr(core, 'open_proxy_socket', refuse)
    with pytest.raises(OSError) as error:
        ftp._transfercmd_with_proxy('LIST')
    assert calls == [('95.131.202.127' if override else '205.147.201.254', 50734)]
    assert 'did not reach a TLS handshake' in str(error.value)
    if override:
        assert 'disable "Server use NAT/Non-routable/Masq IP"' in str(error.value)
        assert '205.147.201.254:50734' in str(error.value)
    else:
        assert 'forwarding/firewall' in str(error.value)
    assert len(calls) == 1  # Never silently bypass the proxy or change the route.
