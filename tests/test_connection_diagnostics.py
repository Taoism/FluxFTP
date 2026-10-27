import json
import socket
from dataclasses import replace
from threading import Event

import pytest
from fluxftp.connection_diagnostics import diagnose, DiagnosticsDialog
from fluxftp.core import Connection, Cancelled
from test_integration import servers
from test_queue import window


@pytest.mark.parametrize('tls', [False, True])
def test_read_only_diagnostics_passes_real_login_and_listing(servers, tls):
    root, site = servers(tls)
    (root / 'private-filename').write_bytes(b'unchanged')
    connection = Connection(site, 'password')
    report = diagnose(connection, '/', Event())
    assert report['passed']
    assert [r['stage'] for r in report['checks']] == ['DNS', 'TCP / proxy tunnel', 'Security / authentication', 'Directory / data connection']
    assert 'FTP authentication passed' in report['milestones']
    if tls: assert 'TLS handshake passed' in report['milestones']
    assert 'password' not in json.dumps(report) and 'private-filename' not in json.dumps(report)
    assert (root / 'private-filename').read_bytes() == b'unchanged'
    assert len(list(root.iterdir())) == 1


def test_dns_failure_stops_before_login(servers, monkeypatch):
    _, site = servers()
    connection = Connection(site, 'password')
    def fail(*args, **kwargs): raise socket.gaierror('name not found')
    monkeypatch.setattr(socket, 'getaddrinfo', fail)
    report = diagnose(connection, '/', Event())
    assert not report['passed'] and len(report['checks']) == 1
    assert report['checks'][0]['stage'] == 'DNS'


def test_bad_login_and_cancel(servers):
    _, site = servers()
    report = diagnose(Connection(site, 'private-secret'), '/', Event())
    assert not report['passed']
    assert report['checks'][-1]['stage'] == 'Security / authentication'
    assert 'private-secret' not in json.dumps(report)
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled): diagnose(Connection(site, 'password'), '/', cancel)


def test_missing_folder_reports_data_stage(servers):
    _, site = servers()
    report = diagnose(Connection(site, 'password'), '/missing', Event())
    assert not report['passed'] and report['checks'][-1]['stage'] == 'Directory / data connection'
    assert 'Login succeeded' in report['checks'][-1]['detail']


def test_diagnostic_controls(window):
    dialog = DiagnosticsDialog(window)
    assert dialog.output.isReadOnly() and dialog.output.tabChangesFocus()
    assert not dialog.export_button.isEnabled()
    dialog.reject()
    dialog.deleteLater()


def test_active_ftp_data_check(servers):
    _, site = servers()
    report = diagnose(Connection(replace(site, passive=False), 'password'), '/', Event())
    assert report['passed'] and report['data_mode'] == 'Active'


def test_tls_failure_preserves_verification(servers):
    _, site = servers(True)
    report = diagnose(Connection(replace(site, ca_file=''), 'password'), '/', Event())
    assert not report['passed']
    assert 'certificate' in report['checks'][-1]['detail'].lower()
    assert 'FTP authentication passed' not in report['milestones']
