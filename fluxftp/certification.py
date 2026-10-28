"""Read-only evidence for the trust policy used by a saved connection."""
import base64
import hashlib
from datetime import datetime, timezone

from cryptography import x509

from .core import PROTOCOLS, Cancelled, CertificateTrustRequired, _site_pin_applies, normalize_host
from .diagnostics import redact
from .trust_service import certificate_details, fingerprint, tls_policy




def check_certification(connection, cancel):
    site = connection.site
    report = dict(version=1, checked_at=datetime.now(timezone.utc).isoformat(),
                  protocol=site.protocol, status='Failed', passed=False, checks=[],
                  scope='Connection identity and configured trust. No organizational certification or revocation check.')
    previous_trust = connection.ssh_trust
    if previous_trust:
        from types import SimpleNamespace
        def reject_unknown(host, key):
            report['presented_ssh_key'] = dict(algorithm=key.get_name(),
                sha256=fingerprint(key))
            return False
        connection.ssh_trust = SimpleNamespace(path=previous_trust.path, verify=reject_unknown)
    try:
        if cancel.is_set(): raise Cancelled('Certification check cancelled')
        if site.protocol == PROTOCOLS[4] or getattr(connection, 'is_cloud', False):
            report.update(status='Not applicable')
            report['checks'].append('Plain FTP has no TLS certificate; cloud TLS is managed by its provider backend.')
            return report
        connection.max_retries = 0
        connection.connect()
        if cancel.is_set(): raise Cancelled('Certification check cancelled')
        if connection.ssh:
            transport = connection.ssh.get_transport()
            key = transport.get_remote_server_key()
            report['ssh'] = dict(algorithm=key.get_name(), bits=key.get_bits(),
                sha256=fingerprint(key),
                cipher=transport.remote_cipher)
            report['checks'].append('SSH host key matched configured known-host trust; authentication succeeded.')
            report['checks'].append('SSH host keys are not X.509 certificates and have no certificate expiry date.')
        elif connection.ftp:
            sock = connection.ftp.sock
            report['certificate'] = certificate_details(sock.getpeercert(binary_form=True))
            pinned = bool(tls_policy(site, normalize_host(site.host)).pin)
            report['trust'] = tls_policy(site, normalize_host(site.host)).description
            report['tls'] = dict(version=sock.version(), cipher=sock.cipher()[0])
            report['checks'].append('Exact saved fingerprint and validity period checked; CA chain and hostname are bypassed by this saved pin.'
                                    if pinned else 'Certificate chain, hostname and validity passed the configured TLS trust policy.')
            if not report['certificate']['current']:
                raise ValueError('Certificate is outside its validity period.')
            if report['certificate']['days_remaining'] < 30:
                report['checks'].append('Warning: certificate expires in fewer than 30 days.')
        report.update(status='Passed', passed=True)
    except Cancelled as error:
        if cancel.is_set(): raise
        report['checks'].append('Connection identity could not be verified or authentication was cancelled. Trust was not changed.')
    except Exception as error:
        if isinstance(error, CertificateTrustRequired) and error.certificate:
            report['certificate'] = certificate_details(error.certificate_der)
        secrets = (connection.password, site.username, (connection.proxy or {}).get('password', ''))
        report['checks'].append(redact(f'{type(error).__name__}: {error}', secrets)[:2000])
        report['checks'].append('Trust was not changed. Authentication or subsystem failures can prevent a complete report.')
    finally:
        connection.close()
        connection.ssh_trust = previous_trust
    return report


def report_text(report):
    import json
    return '\n'.join(['Certification check: ' + report['status'], report['scope'],
                      'Protocol: ' + report['protocol'], '', *report['checks'], '',
                      *[name + ':\n' + json.dumps(report[name], indent=2, ensure_ascii=False)
                        for name in ('trust', 'certificate', 'tls', 'ssh', 'presented_ssh_key') if name in report]])


from .connection_diagnostics import DiagnosticsDialog


class CertificationDialog(DiagnosticsDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('Certification Checker')
        self.export_filename = 'certification-report.json'
        self.site.setAccessibleName('Certification saved site')
        self.run_button.setText('Check certification')
        self.output.setAccessibleName('Certification report')
        self.output.setPlainText('Checks TLS certificate or SSH host-key trust using the saved connection.\n'
            'Does not change trust, write files, or check certificate revocation or organizational certifications.')
        for control in (self.path, self.mode):
            control.setVisible(False)
            self.layout().itemAt(0).layout().labelForField(control).setVisible(False)

    def perform_check(self, connection, path, cancel):
        return check_certification(connection, cancel)

    def create_connection(self):
        from .automation import saved_connection
        return saved_connection(self.window, self.site.currentText(), unattended=False)

    def format_report(self, report):
        return report_text(report)



def show_certification(window):
    dialog = CertificationDialog(window)
    dialog.exec()
    dialog.deleteLater()
