import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from fluxftp.core import Connection, Cancelled, CertificateTrustRequired
from fluxftp.certification import check_certification
from fluxftp.transfer_control import pause_jobs
from test_integration import servers
from test_sftp import ssh_server
from test_queue import window, job_for
from test_ui import settle


def spin(predicate, seconds=5):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        QApplication.processEvents()
        if predicate(): return
        time.sleep(.01)
    assert predicate()


def test_active_pause_resume_and_stop(window):
    started, allow_report = threading.Event(), threading.Event()
    chunks = []
    def task(worker):
        started.set()
        assert allow_report.wait(3)
        for i in range(30):
            worker.report(i, 30)
            chunks.append(i)
            if worker.cancel.wait(.02): raise Cancelled('Cancelled')
    job = job_for(window, task, route='Relay')
    job['row'].setSelected(True)
    window.start_next()
    assert started.wait(3)
    window.queue_commands.pause_selected()
    allow_report.set()
    spin(lambda: job['row'].text(7).endswith('Paused'))
    assert not chunks and job['held'] and job['state'] == 'active'
    window.queue_commands.resume_selected()
    spin(lambda: len(chunks) > 2)
    window.queue_commands.pause_selected()
    spin(lambda: job['row'].text(7).endswith('Paused'))
    count = len(chunks)
    time.sleep(.1)
    assert len(chunks) == count
    window.cancel_selected()
    settle(window)
    assert job['state'] == 'cancelled' and not window.active


def test_hold_and_resume_selected_preserves_other_holds(window, monkeypatch):
    one = job_for(window, lambda _: None, name='one')
    two = job_for(window, lambda _: None, name='two')
    window.queue_commands.pause_all()
    assert window.paused and one['held'] and two['held']
    one['row'].setSelected(True)
    monkeypatch.setattr(window, 'start_next', lambda: None)
    window.queue_commands.resume_selected()
    assert not window.paused and not one['held'] and two['held']


def test_certificate_ca_pin_and_rejection(servers):
    _, site = servers(True)
    passed = check_certification(Connection(site, 'password'), threading.Event())
    assert passed['passed'] and passed['trust'] == 'Certificate authority and hostname'
    assert passed['certificate']['current'] and len(passed['certificate']['sha256']) == 64
    assert 'password' not in json.dumps(passed)
    untrusted = replace(site, ca_file='')
    failed = check_certification(Connection(untrusted, 'password'), threading.Event())
    assert not failed['passed'] and failed['certificate']['sha256'] == passed['certificate']['sha256']
    assert not untrusted.tls_cert_sha256
    pinned = replace(untrusted, tls_cert_sha256=passed['certificate']['sha256'],
                     tls_pin_host=site.host, tls_pin_port=site.port, tls_pin_protocol=site.protocol)
    report = check_certification(Connection(pinned, 'password'), threading.Event())
    assert report['passed'] and report['trust'] == 'Saved certificate SHA-256 pin'
    changed = replace(pinned, tls_cert_sha256='0' * 64)
    assert not check_certification(Connection(changed, 'password'), threading.Event())['passed']


def test_ssh_certification_and_plain_ftp(ssh_server, servers):
    _, site = ssh_server
    report = check_certification(Connection(site, 'password'), threading.Event())
    assert report['passed'] and report['ssh']['sha256'].startswith('SHA256:')
    assert not check_certification(Connection(replace(site, known_hosts=''), 'password'), threading.Event())['passed']
    _, ftp = servers()
    report = check_certification(Connection(ftp, 'password'), threading.Event())
    assert report['status'] == 'Not applicable' and not report['passed']


@pytest.fixture
def scp_server(request, ssh_server):
    executable = 'C:/Windows/System32/OpenSSH/scp.exe' if os.name == 'nt' else shutil.which('scp')
    if not Path(executable).exists(): pytest.skip('OpenSSH scp executable required')
    processes = []
    def handler(channel, command, root):
        try:
            args = shlex.split(command.decode('utf-8'))
            assert args[:1] == ['scp'] and args[1] in ('-t', '-f') and args[2] == '--' and len(args) == 4
            target = (root / args[3].lstrip('/')).resolve()
            assert target.is_relative_to(root)
            process = subprocess.Popen([executable, args[1], '--', str(target)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(process)
            def feed():
                try:
                    while data := channel.recv(65536):
                        process.stdin.write(data)
                        process.stdin.flush()
                except (OSError, EOFError): pass
                finally:
                    try: process.stdin.close()
                    except OSError: pass
            threading.Thread(target=feed, daemon=True).start()
            while data := process.stdout.read1(65536): channel.sendall(data)
            code = process.wait(timeout=10)
            channel.send_exit_status(code)
        except Exception:
            try: channel.send_exit_status(1)
            except Exception: pass
        finally:
            try: channel.close()
            except (EOFError, OSError): pass
    request.node.scp_handler = handler
    root, site = ssh_server
    yield root, replace(site, protocol='SCP · SSH')
    for process in processes:
        if process.poll() is None: process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize('name', ['plain.bin', "space and 'quote.bin", 'empty.bin'])
def test_scp_openssh_roundtrip(scp_server, tmp_path, name):
    root, site = scp_server
    data = b'' if name == 'empty.bin' else bytes(range(256))*600
    source, target = tmp_path/'source', tmp_path/'target'
    source.write_bytes(data)
    with Connection(site, 'password') as connection:
        connection.transfer(source, '/' + name, True, threading.Event(), lambda *_: None)
        assert (root/name).read_bytes() == data
        connection.transfer(target, '/' + name, False, threading.Event(), lambda *_: None)
        assert target.read_bytes() == data
        with pytest.raises(ValueError, match='byte-offset resume'):
            connection.transfer(source, '/' + name, True, threading.Event(), lambda *_: None, resume=True)


def test_scp_cancel_preserves_destination(scp_server, tmp_path):
    root, site = scp_server
    (root/'file').write_bytes(b'x' * 1000000)
    target = tmp_path/'target'
    target.write_bytes(b'original')
    cancel = threading.Event()
    with Connection(site, 'password') as connection:
        with pytest.raises(Cancelled):
            connection.transfer(target, '/file', False, cancel, lambda *_: cancel.set())
    assert target.read_bytes() == b'original'


def test_scp_ui_protocol_and_ssh_key_kind(window):
    from fluxftp.app import SiteDialog
    from fluxftp.site_import import port_for
    dialog = SiteDialog(window, {})
    dialog.protocol.setCurrentText('SCP · SSH')
    assert dialog.port.value() == 22 and dialog.fields['host'].isEnabled()
    assert not dialog.cloud_account.isVisible()
    assert port_for('SCP · SSH') == 22
    window.quick_protocol.setCurrentText('SCP · SSH')
    assert window.quick_port.value() == 22 and window.quick_host.isEnabled()
    dialog.reject()


def test_checker_dialog_and_unknown_trust_does_not_write(window, ssh_server, tmp_path):
    from types import SimpleNamespace
    from fluxftp.certification import CertificationDialog
    _, site = ssh_server
    known = tmp_path/'new-known-hosts'
    trust = SimpleNamespace(path=known, verify=lambda *_: pytest.fail('Must not prompt to trust'))
    report = check_certification(Connection(replace(site, known_hosts=''), 'password', ssh_trust=trust), threading.Event())
    assert not report['passed'] and report['presented_ssh_key']['sha256'].startswith('SHA256:')
    assert not known.exists()
    dialog = CertificationDialog(window)
    assert dialog.path.isHidden() and dialog.mode.isHidden()
    assert dialog.run_button.text() == 'Check certification'
    dialog.output.setPlainText(dialog.format_report(report))
    assert 'Trust was not changed' in dialog.output.toPlainText()
    dialog.reject()


def test_backend_owned_transfers_cannot_claim_payload_pause(window):
    from fluxftp.transfer_control import can_pause
    from fluxftp.core import Site
    job = job_for(window, lambda _: None, route='FXP')
    job['state'] = 'active'
    assert not can_pause(job)
    job.update(route='Upload', destination_site=Site(cloud_account='test'))
    assert not can_pause(job)
    job['state'] = 'queued'
    assert can_pause(job)


def test_scp_tree_and_rejected_exec(scp_server, tmp_path, request):
    from fluxftp.core import transfer_tree
    root, site = scp_server
    local = tmp_path/'tree'
    (local/'nested').mkdir(parents=True)
    (local/'nested'/'file').write_bytes(b'nested contents')
    with Connection(site, 'password') as connection:
        transfer_tree(None, connection, str(local), '/tree', True, threading.Event(), lambda *_: None)
        assert (root/'tree/nested/file').read_bytes() == b'nested contents'
        request.node.scp_handler = None
        with pytest.raises(Exception, match='Channel closed|SCP'):
            connection.transfer(local/'nested'/'file', '/rejected', True, threading.Event(), lambda *_: None)
        assert not (root/'rejected').exists()


@pytest.mark.parametrize('header', [b'C0600 1 ../escape\n', b'C0600 9 file\n', b'D0755 0 file\n'])
def test_scp_rejects_unexpected_records(ssh_server, tmp_path, request, header):
    root, site = ssh_server
    (root/'file').write_bytes(b'x')
    target = tmp_path/'target'
    target.write_bytes(b'keep')
    def handler(channel, command, root):
        try:
            channel.recv(1)
            channel.sendall(header)
            channel.recv(1)
        finally:
            try: channel.close()
            except (EOFError, OSError): pass
    request.node.scp_handler = handler
    with Connection(replace(site, protocol='SCP · SSH'), 'password') as connection:
        with pytest.raises(OSError, match='unexpected filename'):
            connection.transfer(target, '/file', False, threading.Event(), lambda *_: None)
    assert target.read_bytes() == b'keep'


def test_scp_exec_ack_timeout():
    from fluxftp.scp import execute
    class SilentChannel:
        closed = threading.Event()
        def exec_command(self, command):
            assert self.closed.wait(3)
            raise OSError('closed')
        def close(self): self.closed.set()
    channel = SilentChannel()
    with pytest.raises(TimeoutError, match='acknowledgement'):
        execute(channel, 'scp -t -- /file', threading.Event(), .15)
    assert channel.closed.is_set()


def test_real_ftp_payload_pause_and_play(window, servers, tmp_path):
    window.initialize_queue_storage(tmp_path/'paused-queue.json')
    root, site = servers()
    source = tmp_path/'payload'
    data = bytes(range(256))*8192
    source.write_bytes(data)
    go = threading.Event()
    def task(worker):
        assert go.wait(3)
        with Connection(site, 'password') as connection:
            connection.transfer(source, '/file', True, worker.cancel, worker.report)
    job = job_for(window, task, route='Upload')
    job.update(src=str(source), dst='/file', name='file', directory=False, resume=False,
               source_password='', destination_password='password', destination_site=site)
    window.start_next()
    pause_jobs(window, [job])
    go.set()
    spin(lambda: job['row'].text(7).endswith('Paused'))
    # A checkpoint follows the first payload block, with no subsequent blocks sent.
    time.sleep(.15)
    assert (root/'file').stat().st_size < len(data)
    assert window.save_queue_state()
    saved = json.loads(window.queue_file.read_text())['jobs']
    assert saved[0]['held'] and saved[0]['state'] == 'active'
    window.play_queue()
    settle(window)
    assert (root/'file').read_bytes() == data and not window.active


def test_feature_dialog_previews(window, tmp_path):
    from fluxftp.certification import CertificationDialog
    from fluxftp.app import SiteDialog
    from fluxftp.themes import apply_application
    from fluxftp.app import STYLE
    from PySide6.QtGui import QFontDatabase, QFont
    for name in ('tahoma.ttf', 'tahomabd.ttf', 'segoeui.ttf', 'consola.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/name))
    QApplication.instance().setFont(QFont('Tahoma', 10))
    apply_application(QApplication.instance(), window.preferences, STYLE)
    folder = Path(__file__).resolve().parents[1]/'logs'
    dialog = CertificationDialog(window)
    dialog.site.addItem('Example saved connection')
    dialog.show()
    QApplication.processEvents()
    assert dialog.grab().save(str(folder/'certification-0117.png'))
    dialog.reject()
    site = SiteDialog(window, {})
    site.protocol.setCurrentText('SCP · SSH')
    site.show()
    QApplication.processEvents()
    assert site.grab().save(str(folder/'scp-site-0117.png'))
    site.reject()
    job = job_for(window, lambda _: None)
    job['row'].setSelected(True)
    menu = window.queue_commands.context_menu()
    menu.show()
    QApplication.processEvents()
    assert menu.grab().save(str(folder/'queue-controls-0117.png'))
    menu.close()
