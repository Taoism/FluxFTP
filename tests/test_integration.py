import datetime
import ipaddress
import ftplib
import ssl
import threading
from dataclasses import replace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler, TLS_FTPHandler
from pyftpdlib.ioloop import IOLoop
from pyftpdlib.servers import FTPServer

from fluxftp.core import normalize_host, Connection, Site, PROTOCOLS, CertificateTrustRequired, Cancelled, fxp, safe_name, transfer_tree, verify_sfv


@pytest.fixture
def servers(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256()))
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    running = []
    def start(tls=False, no_mlsd=False, implicit=False, mode_z=False, atomic_rename=False, hash_support=False, fxp_tls=None, stall_retr=False, legacy_replies=False, xdupe=False, extra_account=None, reject_ascii_upload=False):
        root = tmp_path / f"root{len(running)}"
        root.mkdir()
        auth = DummyAuthorizer()
        auth.add_user("user", "password", str(root), perm="elradfmwMT")
        if extra_account:
            auth.add_user(extra_account[0], extra_account[1], str(root), perm='elradfmwMT')
        base = TLS_FTPHandler if tls else FTPHandler
        if mode_z:
            from mode_z_server import compressed_handler
            base = compressed_handler(base)
        class Handler(base):
            if reject_ascii_upload:
                def ftp_STOR(self, file, mode='w'):
                    if self._current_type == 'a':
                        self.respond('553 Error: upload in ASCII mode is not allowed!')
                    else:
                        return super().ftp_STOR(file, mode)
            if xdupe:
                proto_cmds = dict(base.proto_cmds, **{'SITE XDUPE': dict(perm=None, auth=True, arg=True, help='SITE XDUPE 3')})
                def ftp_SITE_XDUPE(self, line):
                    self.xdupe_enabled = line == '3'
                    self.respond('200 XDUPE mode set')
                def ftp_STOR(self, file, mode='w'):
                    from pathlib import Path
                    if getattr(self, 'xdupe_enabled', False) and Path(file).exists():
                        for entry in Path(file).parent.iterdir():
                            if entry.is_file():
                                self.push('553-X-DUPE: ' + entry.name + '\r\n')
                        self.respond('553 File already exists')
                    else:
                        return super().ftp_STOR(file, mode)
            def respond(self, response, *args, **kwargs):
                if legacy_replies and response[:3] in ('220', '230', '530'):
                    from pyftpdlib.handlers.ftp.control import asynchat
                    self._last_response = response
                    asynchat.async_chat.push(self, response.encode('ascii') + b' legacy \xb0 banner\r\n')
                else:
                    return super().respond(response, *args, **kwargs)
            authorizer = auth
            permit_foreign_addresses = True
            certfile = str(certfile_outer)
            keyfile = str(keyfile_outer)
            tls_control_required = tls
            tls_data_required = tls
            if hash_support:
                proto_cmds = dict(base.proto_cmds, HASH=dict(perm='r', auth=True, arg=True, help='HASH <path>'))
            def ftp_FEAT(self, line):
                if hash_support:
                    self._extra_feats.append('HASH SHA-256;SHA-1;MD5')
                super().ftp_FEAT(line)
            def ftp_OPTS(self, line):
                if hash_support and line.startswith('HASH '):
                    self.hash_algorithm = line[5:]
                    self.respond('200 ' + self.hash_algorithm)
                else:
                    super().ftp_OPTS(line)
            def ftp_HASH(self, path):
                import hashlib
                from pathlib import Path
                payload = Path(path).read_bytes()
                algorithm = getattr(self, 'hash_algorithm', 'SHA-256')
                value = hashlib.new(algorithm.replace('-', '').lower(), payload).hexdigest()
                self.respond(f'213 {algorithm} 0-{max(0, len(payload)-1)} {value} file')
            def handle(self):
                if implicit:
                    self.secure_connection(self.ssl_context)
                else:
                    super().handle()
            def handle_ssl_established(self):
                if implicit:
                    FTPHandler.handle(self)
            def ftp_MLSD(self, path):
                if no_mlsd:
                    self.respond("502 MLSD not supported")
                else:
                    super().ftp_MLSD(path)
            def ftp_RETR(self, path):
                if stall_retr:
                    self.respond('150 Waiting for test cancellation')
                else:
                    return super().ftp_RETR(path)
        if fxp_tls:
            from OpenSSL import SSL
            Handler.ssl_protocol = SSL.TLS_METHOD
            Handler.ssl_context = None
            from pyftpdlib.handlers.ftps.data import TLS_DTPHandler
            class FXPData(TLS_DTPHandler):
                def secure_connection(self, context):
                    super().secure_connection(context)
                    if getattr(self.cmd_channel, 'fxp_client', False):
                        self.socket.set_connect_state()
                        self._ssl_want_write = True
                        self.modify_ioloop_events(self.ioloop.READ | self.ioloop.WRITE)
                def _do_ssl_shutdown(self):
                    # The TLS client must read session tickets/close_notify before
                    # closing on Windows; pyftpdlib's server-only shortcut can RST.
                    if not getattr(self.cmd_channel, 'fxp_client', False):
                        return super()._do_ssl_shutdown()
                    self._ssl_closing = True
                    try:
                        done = self.socket.shutdown()
                    except SSL.WantReadError:
                        done = False
                    except SSL.WantWriteError:
                        self._ssl_want_write = True
                        self.modify_ioloop_events(self.ioloop.WRITE)
                        return
                    if done:
                        self._ssl_established = self._ssl_closing = False
                        self.handle_ssl_shutdown()
                    else:
                        self._ssl_want_read = True
                        self.modify_ioloop_events(self.ioloop.READ)
            Handler.dtp_handler = FXPData
            Handler.proto_cmds = dict(Handler.proto_cmds)
            if fxp_tls == 'SSCN':
                Handler.proto_cmds['SSCN'] = dict(perm=None, auth=True, arg=True, help='SSCN ON|OFF')
                def sscn(self, value):
                    self.fxp_client = value.upper() == 'ON'
                    self.respond('200 SSCN ' + value)
                Handler.ftp_SSCN = sscn
            else:
                Handler.proto_cmds['CPSV'] = dict(perm=None, auth=True, arg=False, help='CPSV')
                def cpsv(self, value):
                    self.fxp_client = True
                    self.ftp_PASV(value)
                Handler.ftp_CPSV = cpsv
        if atomic_rename:
            from pyftpdlib.filesystems import AbstractedFS
            class AtomicFS(AbstractedFS):
                def rename(self, src, dst):
                    import os
                    os.replace(src, dst)
            Handler.abstracted_fs = AtomicFS
        loop = IOLoop()
        server = FTPServer(("127.0.0.1", 0), Handler, ioloop=loop)
        thread = threading.Thread(target=server.serve_forever, kwargs={"timeout":.02, "handle_exit":False}, daemon=True)
        thread.start()
        running.append((server, loop, thread))
        return root, Site(host="127.0.0.1", port=server.socket.getsockname()[1], username="user",
                          protocol=PROTOCOLS[0] if tls else PROTOCOLS[4], ca_file=str(certfile))
    certfile_outer, keyfile_outer = certfile, keyfile
    yield start
    for server, loop, thread in running:
        server.close_all()
        thread.join(2)
        loop.close()


@pytest.mark.parametrize("protocol", [PROTOCOLS[0], PROTOCOLS[1], PROTOCOLS[2], PROTOCOLS[4]])
def test_roundtrip_resume_and_operations(servers, tmp_path, protocol):
    root, site = servers(protocol != PROTOCOLS[4], implicit=protocol == PROTOCOLS[2])
    site.protocol = protocol
    data = bytes(range(256)) * 7000
    source = tmp_path / "source.bin"
    source.write_bytes(data)
    cancel = threading.Event()
    progress = []
    with Connection(site, "password") as c:
        c.mkdir("/folder")
        c.transfer(source, "/folder/file.bin", True, cancel, lambda d, t: progress.append((d, t)))
        assert (root / "folder/file.bin").read_bytes() == data
        assert c.list("/folder")[0].name == "file.bin"
        target = tmp_path / "download.bin"
        target.with_name("download.bin.fluxpart").write_bytes(data[:300000])
        c.transfer(target, "/folder/file.bin", False, cancel, lambda *_: None, resume=True)
        assert target.read_bytes() == data
        (root / "folder/file.bin").write_bytes(data[:200000])
        c.transfer(source, "/folder/file.bin", True, cancel, lambda *_: None, resume=True)
        assert (root / "folder/file.bin").read_bytes() == data
        c.rename("/folder/file.bin", "/folder/renamed.bin")
        c.remove("/folder/renamed.bin")
        c.remove("/folder", True)
        assert not c.list("/")
        assert progress[-1] == (len(data), len(data))
        if protocol != PROTOCOLS[4]:
            assert "TLSv1." in c.security
            assert c.ftp._prot_p


def test_untrusted_certificate_rejected(servers):
    _, site = servers(True)
    with pytest.raises(ssl.SSLCertVerificationError):
        with Connection(replace(site, ca_file=""), "password"):
            pass


@pytest.mark.parametrize("protocol", [PROTOCOLS[0], PROTOCOLS[1], PROTOCOLS[2]])
def test_untrusted_self_signed_certificate_can_be_trusted_by_site_pin(servers, protocol):
    _, site = servers(True, implicit=protocol == PROTOCOLS[2])
    site.protocol = protocol
    site.ca_file = ""
    with pytest.raises(CertificateTrustRequired) as rejected:
        with Connection(site, "password"):
            pass
    trust = rejected.value
    assert trust.certificate is not None
    assert len(trust.fingerprint) == 64

    pinned = replace(site, tls_cert_sha256=trust.fingerprint,
                     tls_pin_host=normalize_host(site.host), tls_pin_port=site.port,
                     tls_pin_protocol=site.protocol)
    with Connection(pinned, "password") as connection:
        assert connection.ftp._prot_p
        assert "TLSv1." in connection.security


def test_cancel_preserves_original(servers, tmp_path):
    root, site = servers()
    (root / "file").write_bytes(b"new" * 100000)
    target = tmp_path / "file"
    target.write_bytes(b"original")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled), Connection(site, "password") as c:
        c.transfer(target, "/file", False, cancel, lambda *_: None)
    assert target.read_bytes() == b"original"


def test_fxp(servers):
    root1, site1 = servers()
    root2, site2 = servers()
    data = b"FXP payload" * 10000
    (root1 / "source").write_bytes(data)
    with Connection(site1, "password") as a, Connection(site2, "password") as b:
        fxp(a, b, "/source", "/destination")
    assert (root2 / "destination").read_bytes() == data


def test_protected_fxp_does_not_downgrade(servers):
    root1, site1 = servers(True)
    root2, site2 = servers(True)
    (root1 / "source").write_bytes(b"protected data")
    with Connection(site1, "password") as a, Connection(site2, "password") as b:
        with pytest.raises(ftplib.error_perm, match="SSCN"):
            fxp(a, b, "/source", "/destination")
        assert a.ftp._prot_p and b.ftp._prot_p
    assert not (root2 / "destination").exists()


def test_legacy_listing(servers):
    root, site = servers(no_mlsd=True)
    (root / "folder with spaces").mkdir()
    (root / "file with spaces").write_bytes(b"test")
    with Connection(site, "password") as c:
        entries = {e.name: e for e in c.list("/")}
    assert entries["folder with spaces"].is_dir
    assert entries["file with spaces"].size == 4


@pytest.mark.parametrize("name", ["../file", "..", "a/b", "a\\b", "bad\r\nDELE x", ""])
def test_unsafe_names(name):
    with pytest.raises(ValueError):
        safe_name(name)


def test_recursive_transfer(servers, tmp_path):
    root, site = servers(True)
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "nested/payload").write_bytes(b"folder transfer")
    target = tmp_path / "target"
    with Connection(site, "password") as c:
        transfer_tree(None, c, str(source), "/uploaded", True, threading.Event(), lambda *_: None, resume=True)
        transfer_tree(c, None, "/uploaded", str(target), True, threading.Event(), lambda *_: None)
    assert (target / "nested/payload").read_bytes() == b"folder transfer"


def test_local_to_local_recursive_transfer_and_resume(tmp_path):
    source = tmp_path / "source-local"
    (source / "nested").mkdir(parents=True)
    payload = b"local transfer payload" * 40000
    (source / "nested" / "payload.bin").write_bytes(payload)
    target = tmp_path / "target-local"
    progress = []
    transfer_tree(None, None, str(source), str(target), True, threading.Event(), lambda done, total: progress.append((done, total)), resume=True)
    assert (target / "nested" / "payload.bin").read_bytes() == payload
    assert progress[-1] == (len(payload), len(payload))


def test_sfv(tmp_path):
    (tmp_path / "file").write_bytes(b"123456789")
    manifest = tmp_path / "files.sfv"
    manifest.write_text("; comment\nfile CBF43926\nmissing 00000000\n", "utf-8")
    assert verify_sfv(manifest) == [("file", "OK"), ("missing", "Missing")]
    manifest.write_text("../outside 00000000\n", "utf-8")
    with pytest.raises(ValueError):
        verify_sfv(manifest)


@pytest.mark.parametrize("host,expected", [
    ("0x5f83ca7f", "95.131.202.127"),
    ("0X00000001", "0.0.0.1"),
    ("ftp.example.test", "ftp.example.test"),
    ("192.0.2.12", "192.0.2.12"),
])
def test_normalize_hexadecimal_ipv4_host(host, expected):
    assert normalize_host(host) == expected


def test_connection_resolves_hexadecimal_ipv4_host(servers):
    _, site = servers(False)
    with Connection(replace(site, host="0x7f000001"), "password") as connection:
        assert connection.ftp.sock.getpeername()[0] == "127.0.0.1"


def test_connection_can_force_list_when_mlsd_is_available(servers):
    _, site = servers(False)
    with Connection(site, "password", list_mode="LIST only") as connection:
        assert connection.list("/") == []


def test_forced_utf8_negotiates_after_ftp_login(servers):
    _root, site = servers(False)
    site.force_utf8 = True
    site.utf8_username = True
    with Connection(site, "password") as connection:
        assert connection.ftp.encoding == "utf-8"
        assert connection.list("/") == []
