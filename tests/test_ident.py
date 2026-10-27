import socket
import threading

import pytest

from fluxftp.ident import IdentService
from fluxftp.core import Connection, Site, PROTOCOLS


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def query(service, payload):
    with socket.create_connection(("127.0.0.1", service.port), timeout=2) as sock:
        sock.sendall(payload)
        return sock.recv(1024)


def test_ident_wire_responses_and_connection_scope():
    service = IdentService()
    try:
        service.configure(True, False, "tester", "UNIX", unused_port())
        owner = object()
        service.connections[owner] = (("127.0.0.1", 12345), ("127.0.0.1", 21))
        assert query(service, b"12345,21\r\n") == b"12345 , 21 : USERID : UNIX :tester\r\n"
        assert b"NO-USER" in query(service, b"12346,21\r\n")
        assert b"NO-USER" in service.response(b"12345,21\r\n", "127.0.0.1", "127.0.0.2")
        for invalid in (b"0,21\r\n", b"65536,21\r\n", b"-1,21\r\n", b"x,21\r\n"):
            assert b"INVALID-PORT" in query(service, invalid)
        service.release(owner)
        assert b"NO-USER" in query(service, b"12345,21\r\n")
    finally:
        service.close()
    assert service.listener is None


def test_connecting_lifecycle_handles_parallel_attempts():
    service = IdentService()
    try:
        service.configure(True, True, port=unused_port())
        assert service.listener is None
        first, second = object(), object()
        service.begin(first)
        service.begin(second)
        assert service.listener is not None
        service.ready(first)
        assert service.listener is not None
        service.release(second)
        assert service.listener is None
        service.begin(first)
        assert service.listener is not None
    finally:
        service.close()


def test_ident_validation_and_busy_port():
    service = IdentService()
    for settings in ({"name": "bad\r\ninjection"}, {"system": "UNIX:bad"}, {"port": 0}):
        with pytest.raises(ValueError):
            service.configure(**settings)
    with socket.socket() as occupied:
        occupied.bind(("0.0.0.0", 0))
        occupied.listen()
        with pytest.raises(OSError, match="Cannot start Ident"):
            service.configure(True, False, port=occupied.getsockname()[1])
    service.close()


def test_ftp_login_can_query_ident_before_sending_greeting():
    service = IdentService()
    service.configure(True, True, "login-test", "OTHER", unused_port())
    replies, failures = [], []
    with socket.socket() as ftp:
        ftp.bind(("127.0.0.1", 0))
        ftp.listen()
        ftp.settimeout(3)
        def serve():
            try:
                with ftp.accept()[0] as control:
                    control.settimeout(3)
                    local_port = control.getpeername()[1]
                    remote_port = control.getsockname()[1]
                    replies.append(query(service, f"{local_port},{remote_port}\r\n".encode()))
                    control.sendall(b"220 Test FTP\r\n")
                    with control.makefile("rb") as reader:
                        while True:
                            command = reader.readline()
                            if not command:
                                break
                            control.sendall(b"230 Logged in\r\n" if command.startswith(b"USER") else b"200 OK\r\n")
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            site = Site(host="127.0.0.1", port=ftp.getsockname()[1], protocol=PROTOCOLS[4], username="test")
            with Connection(site, ident=service):
                assert service.listener is None
            assert not service.connections and not service.connecting
        finally:
            service.close()
            thread.join(4)
    assert not failures
    assert replies and b"USERID : OTHER :login-test\r\n" in replies[0]


def test_failed_connection_stops_ident():
    service = IdentService()
    service.configure(True, True, port=unused_port())
    try:
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            site = Site(host="127.0.0.1", port=unavailable.getsockname()[1], protocol=PROTOCOLS[4])
            with pytest.raises(OSError):
                with Connection(site, ident=service, connect_timeout=1):
                    pass
        assert service.listener is None
        assert not service.connecting and not service.connections
    finally:
        service.close()


def test_ident_waits_for_control_socket_registration():
    service = IdentService()
    service.configure(True, True, port=unused_port())
    owner = object()
    service.begin(owner)
    class SocketEndpoints:
        def getsockname(self): return ("127.0.0.1", 12345)
        def getpeername(self): return ("127.0.0.1", 21)
    registration = threading.Timer(0.05, lambda: service.register(owner, SocketEndpoints()))
    try:
        registration.start()
        assert b"USERID" in query(service, b"12345,21\r\n")
    finally:
        registration.join()
        service.close()
