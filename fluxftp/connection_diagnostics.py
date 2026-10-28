"""Read-only, staged checks using the same connection settings as transfers."""
import json
import socket
import ssl
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox,
    QLineEdit, QPushButton, QPlainTextEdit, QHBoxLayout, QFileDialog, QMessageBox)
from dataclasses import replace

from .core import Cancelled, open_proxy_socket, normalize_host
from .diagnostics import redact
from .automation import saved_connection, remote_path, list_folder


def diagnose(connection, path, cancel):
    report = dict(version=1, protocol=connection.site.protocol, checks=[], passed=False)
    report['data_mode'] = 'Backend managed' if getattr(connection, 'is_cloud', False) else 'Passive' if connection.site.passive else 'Active'
    if connection.site.protocol in ('SFTP · SSH', 'SCP · SSH'): report['data_mode'] = 'SFTP channel'
    secrets = (connection.password, (connection.proxy or {}).get('password', ''),
               (connection.proxy or {}).get('username', ''), connection.site.username)
    def check(name, operation):
        if cancel.is_set(): raise Cancelled('Connection diagnostics cancelled')
        started = time.monotonic()
        try:
            detail = operation()
        except Cancelled:
            raise
        except Exception as error:
            phase = getattr(connection, 'phase', '')
            if isinstance(error, socket.gaierror):
                hint = 'Check the host name and DNS configuration.'
            elif isinstance(error, ssl.SSLError) or 'Certificate' in type(error).__name__:
                hint = 'Check the certificate, host name and trust configuration; verification remains enabled.'
            elif name == 'Directory / data connection':
                hint = ('Check cloud account authorization, provider availability and the remote folder.' if getattr(connection, 'is_cloud', False) else
                        'Check SFTP folder permissions and the remote path.' if connection.site.protocol in ('SFTP · SSH', 'SCP · SSH') else
                        'Login succeeded. Check folder permissions and the FTP data route. For active FTP, '
                        'check the advertised address, inbound ports, router and firewall; a UPnP mapping alone does not prove reachability.')
            elif name == 'TCP / proxy tunnel':
                hint = 'Check the port, proxy settings and network/firewall route.'
            else:
                hint = 'Check the reported connection stage, saved credentials and server permissions.'
            description = type(error).__name__ if getattr(connection, 'is_cloud', False) else f'{type(error).__name__}: {error}'
            report['checks'].append(dict(stage=name, status='Failed', seconds=round(time.monotonic()-started, 3),
                detail=redact(f'{description}; phase: {phase}. {hint}', secrets)[:2000]))
            return False
        report['checks'].append(dict(stage=name, status='Passed', seconds=round(time.monotonic()-started, 3),
            detail=redact(detail or 'Successful', secrets)[:2000]))
        return True
    try:
        if not getattr(connection, 'is_cloud', False):
            proxy = connection.proxy or {}
            host = normalize_host(proxy.get('host') or connection.site.host)
            port = proxy.get('port') or connection.site.port
            def resolve():
                addresses = socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)
                return f'{len(addresses)} address result(s) for ' + ('proxy; target resolution follows proxy policy.' if proxy else 'server.')
            if not check('DNS', resolve): return report
            def transport():
                with open_proxy_socket(normalize_host(connection.site.host), connection.site.port,
                        connection.connect_timeout, connection.proxy,
                        (connection.local_bind_ip, 0) if connection.local_bind_ip else None):
                    return 'Proxy tunnel established.' if proxy else 'TCP connection established.'
            if not check('TCP / proxy tunnel', transport): return report
        else:
            report['checks'].append(dict(stage='DNS / transport', status='Backend managed', seconds=0,
                detail='Cloud provider transport and authentication are checked by the cloud backend during listing.'))
        # The production engine exposes handshake/login milestones. Record only
        # known messages, never raw replies, banners, paths or arbitrary tokens.
        milestones = []
        trace = connection.trace
        previous = getattr(trace, 'listener', None)
        def event(message, level):
            for prefix, description in (
                ('Explicit TLS established:', 'TLS handshake passed'),
                ('Implicit TLS established:', 'TLS handshake passed'),
                ('FTP authentication successful', 'FTP authentication passed'),
                ('SFTP connected:', 'SSH host key, authentication and SFTP subsystem passed'),
                ('Data TLS established:', 'Data TLS handshake passed')):
                if message.startswith(prefix) and description not in milestones: milestones.append(description)
        trace.listener = event
        try:
            if not check('Security / authentication', lambda: (connection.connect(), connection.security)[1]):
                return report
            if not check('Directory / data connection', lambda: f'Listed {len(list_folder(connection, path, cancel))} entries. Read access verified; write access not tested.'):
                return report
            report['passed'] = True
            if connection.ftp:
                report['data_mode'] = 'Passive' if connection.ftp.passiveserver else 'Active'
        finally:
            report['milestones'] = milestones
            trace.listener = previous
    finally:
        connection.close()
    return report


def report_text(report):
    lines = ['Connection checks passed' if report['passed'] else 'Connection check failed',
             f'Protocol: {report["protocol"]} · Data mode: {report.get("data_mode", "—")}', '']
    for item in report['checks']:
        lines.extend([f'{item["stage"]}: {item["status"]} ({item["seconds"]:.3f} s)', item['detail'], ''])
    lines.extend(report.get('milestones', []))
    return '\n'.join(lines)


class DiagnosticsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.worker, self.report, self.closing = window, None, None, False
        self.setWindowTitle('Connection Diagnostics')
        self.resize(780, 540)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.site = QComboBox()
        self.site.addItems(sorted(n for n, s in window.sites.items() if not s.get('_disabled')))
        self.path = QLineEdit('/')
        self.mode = QComboBox()
        self.mode.addItems(['Configured mode', 'Passive FTP', 'Active FTP'])
        self.site.setAccessibleName('Diagnostic saved site')
        self.path.setAccessibleName('Diagnostic remote folder')
        self.mode.setAccessibleName('Diagnostic FTP data mode')
        form.addRow('Saved site', self.site)
        form.addRow('Remote folder', self.path)
        form.addRow('Data connection test', self.mode)
        layout.addLayout(form)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setTabChangesFocus(True)
        self.output.setAccessibleName('Connection diagnostic report')
        self.output.setPlainText('Checks DNS, TCP/proxy, security/login and directory data access. No files are written.\n'
            'The TCP probe opens a separate connection. System DNS and network cancellation wait for their current request to return.')
        layout.addWidget(self.output)
        buttons = QHBoxLayout()
        self.run_button, self.export_button, self.close_button = [QPushButton(t) for t in ('Run diagnostics', 'Export report…', 'Close')]
        for button in (self.run_button, self.export_button, self.close_button): buttons.addWidget(button)
        layout.addLayout(buttons)
        self.run_button.clicked.connect(self.start)
        self.export_button.clicked.connect(self.export)
        self.close_button.clicked.connect(self.reject)
        self.export_button.setEnabled(False)
        self.site.currentTextChanged.connect(self.site_changed)
        self.site_changed()
        from .accessibility import fit_dialog
        fit_dialog(self)

    def site_changed(self):
        self.path.setText(self.window.sites.get(self.site.currentText(), {}).get('root') or '/')

    def perform_check(self, connection, path, cancel):
        return diagnose(connection, path, cancel)

    def create_connection(self):
        return saved_connection(self.window, self.site.currentText(), unattended=True)

    def format_report(self, report):
        return report_text(report)

    def start(self):
        if self.worker: return
        try:
            path = remote_path(self.path.text().strip())
            connection = self.create_connection()
            from .core import PROTOCOLS
            if self.mode.currentIndex():
                if connection.site.protocol in (PROTOCOLS[3], 'SCP · SSH') or getattr(connection, 'is_cloud', False):
                    raise ValueError('Choose Configured mode for SFTP and cloud connections.')
                passive = self.mode.currentIndex() == 1
                if connection.proxy and not passive: raise ValueError('Active FTP callbacks cannot traverse the configured proxy. Test passive mode or a direct saved site.')
                connection.site = replace(connection.site, passive=passive)
                connection.passive_fallback = False
            connection.max_retries = 0
            if connection.site.protocol == PROTOCOLS[4] and self.window.preferences.value('Confirm/unencrypted', True, type=bool):
                if QMessageBox.question(self, 'Unencrypted connection', 'Plain FTP sends your password without encryption. Continue?') != QMessageBox.StandardButton.Yes:
                    return
        except Exception as error:
            self.output.setPlainText(str(error))
            return
        self.report = None
        self.export_button.setEnabled(False)
        for control in (self.site, self.path, self.mode, self.run_button): control.setEnabled(False)
        self.output.setPlainText('Checking connection…')
        def done(report):
            self.report = report
            self.output.setPlainText(self.format_report(report))
            self.export_button.setEnabled(True)
            self.finished()
        def failed(error):
            self.output.setPlainText('Diagnostics stopped: ' + type(error).__name__)
            self.finished()
        self.worker = self.window.run(lambda worker: self.perform_check(connection, path, worker.cancel), done, failed,
                                      raw_failure=True, autostart=False)
        self.worker.start()

    def finished(self):
        self.worker = None
        for control in (self.site, self.path, self.mode, self.run_button): control.setEnabled(True)
        if self.closing: super().reject()

    def export(self):
        if not self.report: return
        path, _ = QFileDialog.getSaveFileName(self, 'Export report', getattr(self, 'export_filename', 'connection-diagnostics.json'), 'JSON (*.json)')
        if path:
            try: Path(path).write_text(json.dumps(self.report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            except OSError as error: self.window.error(str(error))

    def reject(self):
        if self.worker:
            self.closing = True
            self.worker.cancel.set()
            self.output.appendPlainText('Cancelling; waiting for the current network request…')
        else: super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else: super().closeEvent(event)


def show_diagnostics(window):
    dialog = DiagnosticsDialog(window)
    dialog.exec()
    dialog.deleteLater()
