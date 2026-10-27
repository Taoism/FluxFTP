import ftplib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fluxftp.browse_session import BrowseSession
from fluxftp.core import Connection
from test_integration import servers
from test_sftp import ssh_server


@pytest.mark.parametrize('tls', [False, True])
def test_reuse_refresh_disconnect_and_expired_connection(servers, tls):
    root, site = servers(tls)
    (root / 'folder').mkdir()
    created = []
    def factory():
        c = Connection(site, 'password')
        created.append(c)
        return c
    session = BrowseSession()
    try:
        session.list('site', factory, '/')
        session.list('site', factory, '/folder')
        assert len(created) == 1
        (root / 'folder' / 'new.txt').write_text('fresh')
        assert session.list('site', factory, '/folder')[0][0].name == 'new.txt'
        created[0].ftp.sock.shutdown(2)
        assert session.list('site', factory, '/folder')[0][0].name == 'new.txt'
        assert len(created) == 2
        session.list('changed settings', factory, '/')
        assert len(created) == 3
    finally:
        session.close()
    assert session.connection is None
    assert all(c.ftp.sock is None for c in created)


def test_permission_failure_is_not_retried():
    c = SimpleNamespace(connect=Mock(), list=Mock(return_value=[]), security='',
                        close=Mock(), trace=SimpleNamespace(error=Mock()), phase='listing')
    factory = Mock(return_value=c)
    session = BrowseSession()
    session.list('site', factory, '/')
    c.list.side_effect = ftplib.error_perm('550 Access denied')
    with pytest.raises(ftplib.error_perm):
        session.list('site', factory, '/private')
    assert factory.call_count == 1
    c.close.assert_called_once()


@pytest.mark.parametrize('tls', [False, True])
def test_idle_noop_reuses_connection_and_reconnects_lazily(servers, tls):
    root, site = servers(tls)
    created = []
    def factory():
        connection = Connection(site, 'password')
        created.append(connection)
        return connection
    session = BrowseSession()
    try:
        session.list('site', factory, '/')
        assert not session.due(60)
        session.last_activity -= 61
        assert session.keepalive(60)
        assert len(created) == 1 and not session.due(60)
        created[0].ftp.sock.shutdown(2)
        session.last_activity -= 61
        assert not session.keepalive(60)
        assert session.connection is None and len(created) == 1
        session.list('site', factory, '/')
        assert len(created) == 2
    finally:
        session.close()


def fake_connection():
    return SimpleNamespace(connect=Mock(), list=Mock(return_value=[]), security='', close=Mock(),
        trace=SimpleNamespace(error=Mock(), event=Mock()), phase='listing',
        ftp=SimpleNamespace(sock=Mock(gettimeout=Mock(return_value=200)), voidcmd=Mock(return_value='200 OK')))


def test_unsupported_noop_is_disabled_and_timeout_restored():
    c = fake_connection()
    session = BrowseSession()
    session.list('site', lambda: c, '/')
    c.ftp.voidcmd.side_effect = ftplib.error_perm('502 Unsupported')
    session.last_activity -= 61
    assert not session.keepalive(60)
    assert session.connection is c and not session.due(0)
    assert c.ftp.sock.settimeout.call_args_list[0].args == (5,)
    assert c.ftp.sock.settimeout.call_args_list[-1].args == (200,)
    session.close()


@pytest.mark.parametrize('error', [TimeoutError('idle'), ConnectionResetError('closed'),
                                  EOFError(), ftplib.error_temp('421 Idle timeout')])
def test_read_reconnect_is_bounded(error):
    first, second = fake_connection(), fake_connection()
    factory = Mock(side_effect=[first, second])
    session = BrowseSession()
    session.list('site', factory, '/')
    first.list.side_effect = second.list.side_effect = error
    with pytest.raises(type(error)):
        session.list('site', factory, '/')
    assert factory.call_count == 2 and session.connection is None


def test_keepalive_does_not_overlap_listing_and_close_discards_late_result():
    from threading import Event, Thread
    from fluxftp.core import Cancelled
    entered, release = Event(), Event()
    c = fake_connection()
    session = BrowseSession()
    session.list('site', lambda: c, '/')
    def blocked(_):
        entered.set()
        assert release.wait(5)
        return []
    c.list.side_effect = blocked
    errors = []
    def list_folder():
        try: session.list('site', lambda: c, '/')
        except Exception as error: errors.append(error)
    thread = Thread(target=list_folder)
    thread.start()
    try:
        assert entered.wait(2)
        assert not session.due(0) and not session.keepalive(0)
        session.close()
        c.close.assert_not_called()
    finally:
        release.set(); thread.join(5)
    assert isinstance(errors[0], Cancelled)
    assert session.connection is None
    c.close.assert_called_once()
    c.ftp.voidcmd.assert_not_called()


def test_sftp_persistent_listing_reconnects(ssh_server):
    root, site = ssh_server
    created = []
    def factory():
        c = Connection(site, 'password'); created.append(c); return c
    session = BrowseSession()
    try:
        session.list('site', factory, '/')
        session.list('site', factory, '/')
        assert len(created) == 1 and not session.due(0)
        created[0].sftp.close()
        session.list('site', factory, '/')
        assert len(created) == 2
    finally:
        session.close()


def test_old_scheduled_request_cannot_reopen_disconnected_site():
    from fluxftp.core import Cancelled
    session = BrowseSession()
    generation = session.generation
    session.close()
    current = fake_connection()
    session.list('new site', lambda: current, '/')
    old_factory = Mock()
    with pytest.raises(Cancelled):
        session.list('old site', old_factory, '/', expected_generation=generation)
    old_factory.assert_not_called()
    assert session.connection is current
    session.close()


def test_tls_eof_reconnectable_but_certificate_failure_is_not():
    import ssl
    from fluxftp.browse_session import disconnected
    assert disconnected(ssl.SSLEOFError(8, 'closed'))
    assert not disconnected(ssl.SSLCertVerificationError(1, 'untrusted'))
