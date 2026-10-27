import os
import socket
import threading
from pathlib import Path

import paramiko
import pytest

from fluxftp.core import Site, Connection, PROTOCOLS


@pytest.fixture
def ssh_server(tmp_path, request):
    root = tmp_path / "remote"
    root.mkdir()
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(.1)
    port = listener.getsockname()[1]
    stop = threading.Event()
    transports = []

    class Auth(paramiko.ServerInterface):
        def check_auth_publickey(self, username, public_key):
            allowed = root / 'authorized_keys'
            return (paramiko.AUTH_SUCCESSFUL if username == 'user' and allowed.exists()
                    and public_key.get_base64() in allowed.read_text().split() else paramiko.AUTH_FAILED)
        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ("user", "password") else paramiko.AUTH_FAILED
        def get_allowed_auths(self, username):
            return "publickey,password"
        def check_channel_request(self, kind, channel):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    class Files(paramiko.SFTPServerInterface):
        def path(self, name):
            p = (root / name.lstrip("/")).resolve()
            assert p.is_relative_to(root)
            return p
        def list_folder(self, path):
            result = []
            for p in self.path(path).iterdir():
                attr = paramiko.SFTPAttributes.from_stat(p.stat())
                attr.filename = p.name
                result.append(attr)
            return result
        def stat(self, path):
            try:
                return paramiko.SFTPAttributes.from_stat(self.path(path).stat())
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)
        lstat = stat
        def open(self, path, flags, attr):
            try:
                fd = os.open(self.path(path), flags | getattr(os, "O_BINARY", 0), 0o600)
                mode = "r+b" if flags & os.O_RDWR else "wb" if flags & os.O_WRONLY else "rb"
                stream = os.fdopen(fd, mode)
                class Handle(paramiko.SFTPHandle):
                    def stat(self):
                        return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
                handle = Handle(flags)
                handle.readfile = handle.writefile = stream
                return handle
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)
        def mkdir(self, path, attr):
            self.path(path).mkdir()
            return paramiko.SFTP_OK
        def rename(self, old, new):
            self.path(old).rename(self.path(new))
            return paramiko.SFTP_OK
        def posix_rename(self, old, new):
            if not getattr(request, 'param', False):
                return paramiko.SFTP_OP_UNSUPPORTED
            os.replace(self.path(old), self.path(new))
            return paramiko.SFTP_OK
        def remove(self, path):
            self.path(path).unlink()
            return paramiko.SFTP_OK
        def rmdir(self, path):
            self.path(path).rmdir()
            return paramiko.SFTP_OK

    def serve():
        while not stop.is_set():
            try:
                sock, _ = listener.accept()
            except socket.timeout:
                continue
            transport = paramiko.Transport(sock)
            transports.append(transport)
            transport.add_server_key(key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Files)
            try:
                transport.start_server(server=Auth())
            except (EOFError, OSError, paramiko.SSHException):
                transport.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    known = tmp_path / "known_hosts"
    known.write_text(f"[127.0.0.1]:{port} {key.get_name()} {key.get_base64()}\n", "utf-8")
    yield root, Site(host="127.0.0.1", port=port, username="user", protocol=PROTOCOLS[3], known_hosts=str(known))
    stop.set()
    thread.join(2)
    listener.close()
    for transport in transports:
        transport.close()


def test_sftp_roundtrip(ssh_server, tmp_path):
    root, site = ssh_server
    data = b"SFTP payload\r\n" * 100000
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(data)
    with Connection(site, "password") as c:
        c.mkdir("/folder")
        c.transfer(source, "/folder/file", True, threading.Event(), lambda *_: None)
        assert c.list("/folder")[0].name == "file"
        assert (root / "folder/file").read_bytes() == data
        target.with_name("target.fluxpart").write_bytes(data[:10000])
        c.transfer(target, "/folder/file", False, threading.Event(), lambda *_: None, resume=True)
        assert target.read_bytes() == data
        (root / "folder/file").write_bytes(data[:20000])
        c.transfer(source, "/folder/file", True, threading.Event(), lambda *_: None, resume=True)
        assert (root / "folder/file").read_bytes() == data
        c.rename("/folder/file", "/folder/new")
        c.remove("/folder/new")
        c.remove("/folder", True)


def test_unknown_ssh_host_rejected(ssh_server):
    _, site = ssh_server
    site.known_hosts = ""
    with pytest.raises(paramiko.SSHException, match="known_hosts"):
        with Connection(site, "password"):
            pass
