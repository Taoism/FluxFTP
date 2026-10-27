import ssl
import threading
from dataclasses import replace

import pytest

from fluxftp import diagnostics
from fluxftp.core import Connection
from test_integration import servers
from test_sftp import ssh_server


@pytest.fixture
def debug_log(tmp_path):
    path = diagnostics.configure_debug(tmp_path / "logs/debug.log")
    try:
        yield path
    finally:
        diagnostics.close_debug()


def test_tls_failure_log_has_stage_and_certificate_reason(servers, debug_log):
    _, site = servers(True)
    with pytest.raises(ssl.SSLCertVerificationError):
        with Connection(replace(site, ca_file=""), "private-login-secret"):
            pass
    text = debug_log.read_text("utf-8")
    assert "SSL runtime:" in text
    assert "FTP > AUTH TLS" in text
    assert "Starting explicit TLS handshake" in text
    assert "Certificate verification: code=" in text
    assert "self signed" in text.replace("-", " ")
    assert "private-login-secret" not in text


def test_verified_tls_control_and_data_logged_without_payload(servers, debug_log, tmp_path):
    _, site = servers(True)
    source = tmp_path / "payload.bin"
    source.write_bytes(b"PRIVATE-FILE-CONTENT")
    with Connection(site, "password") as connection:
        connection.list("/")
        connection.transfer(source, "/payload.bin", True, threading.Event(), lambda *_: None)
    text = debug_log.read_text("utf-8")
    assert "FTP > PASS [REDACTED]" in text
    assert "FTP > PASS password" not in text
    assert "FTP > USER [argument omitted]" in text
    assert "FTP authentication successful" in text
    assert "FTP > PROT P" in text
    assert "Data TLS established:" in text
    assert "session_reused=" in text
    assert "Transfer complete: bytes=20" in text
    assert "PRIVATE-FILE-CONTENT" not in text


def test_redact_echoed_passwords_private_keys_and_log_injection(debug_log):
    secret = 'some\\special"secret'
    trace = diagnostics.Trace(secret)
    trace.event(f"530 login failed for {secret}\nFORGED LOG LINE")
    trace.error("test", ValueError(repr(secret)))
    trace.event("SITE password=another-secret")
    trace.event("url=ftps://alice:third-secret@example.test/")
    trace.event("-----BEGIN PRIVATE KEY-----\nprivate-key-bytes\n-----END PRIVATE KEY-----")
    text = debug_log.read_text("utf-8")
    assert secret not in text
    assert repr(secret)[1:-1] not in text
    assert "another-secret" not in text
    assert "third-secret" not in text
    assert "private-key-bytes" not in text
    assert "\nFORGED LOG LINE" not in text
    assert "\\x0aFORGED LOG LINE" in text


def test_ssh_diagnostics_and_global_failure_do_not_dump_exception_secrets(ssh_server, debug_log):
    _, site = ssh_server
    with Connection(site, "password") as connection:
        connection.list("/")
    diagnostics.worker_failure(ValueError("unknown secret in arbitrary worker exception"))
    text = debug_log.read_text("utf-8")
    assert "SFTP connected:" in text
    assert "host key verified" in text
    assert "unknown secret" not in text


def test_missing_certificate_path_is_identified(servers, debug_log, tmp_path):
    _, site = servers(True)
    site.ca_file = str(tmp_path / "missing-ca.pem")
    with pytest.raises(FileNotFoundError):
        with Connection(site, "private-login-secret"):
            pass
    text = debug_log.read_text("utf-8")
    assert "Failure during loading TLS trust / local certificate files: FileNotFoundError" in text
    assert "missing-ca.pem" in text
    assert "private-login-secret" not in text
