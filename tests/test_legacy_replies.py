import ftplib
import pytest
from fluxftp.core import Connection, PROTOCOLS
from fluxftp.diagnostics import Trace, redact
from test_integration import servers


@pytest.mark.parametrize('protocol', [PROTOCOLS[4], PROTOCOLS[0], PROTOCOLS[1], PROTOCOLS[2]])
def test_non_utf8_banner_and_login_reply(servers, protocol):
    root, site = servers(tls=protocol != PROTOCOLS[4], implicit=protocol == PROTOCOLS[2], legacy_replies=True)
    site.protocol = protocol
    (root / 'normal.txt').write_bytes(b'ok')
    with Connection(site, 'password') as connection:
        assert connection.list('/')[0].name == 'normal.txt'
        assert connection.ftp.file.errors == 'surrogateescape'
        assert '\\xdcb0' in redact(connection.ftp.welcome)


def test_legacy_error_reply_remains_authentication_error(servers):
    _, site = servers(tls=True, legacy_replies=True)
    with pytest.raises(ftplib.error_perm) as error:
        with Connection(site, 'wrong-password'):
            pass
    assert str(error.value).startswith('530')


def test_reply_reader_restored_after_utf8_login(servers):
    _, site = servers(tls=True, legacy_replies=True)
    with Connection(site, 'password', filename_encoding='cp1252') as connection:
        assert connection.ftp.encoding == 'cp1252'
        assert connection.ftp.file.encoding == 'cp1252'
