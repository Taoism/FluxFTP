"""Window-facing background task execution and trust prompts."""
from PySide6.QtWidgets import QFileDialog, QMessageBox
from cryptography import x509

from .core import Cancelled, CertificateTrustRequired, normalize_host, verify_sfv
from .worker import Worker


class WindowWorkers:
    """Run background operations and mediate recoverable trust failures."""
    def error(self, message):
        self.log.appendPlainText(message)
        QMessageBox.warning(self, "Operation failed", message)

    def sfv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Verify SFV manifest", "", "SFV files (*.sfv)")
        if path:
            def done(results):
                text = "\n".join(f"{state}: {name}" for name, state in results) or "No entries in manifest"
                self.log.appendPlainText(text)
                QMessageBox.information(self, "SFV results", text[:4000])
            self.run(lambda worker: verify_sfv(path), done)

    def run(self, function, result, failed=None, raw_failure=False, autostart=True):
        failure_handler = failed or self.error
        worker = Worker(function, self)
        self.workers.add(worker)
        worker.result.connect(result)
        worker.failed.connect(failure_handler if raw_failure else lambda error: self._handle_worker_failure(error, function, result, failure_handler))
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.finished.connect(worker.deleteLater)
        if autostart:
            worker.start()
        return worker

    def _handle_worker_failure(self, error, function, result, failure_handler):
        if not isinstance(error, CertificateTrustRequired):
            failure_handler(f"Cancelled: {error}" if isinstance(error, Cancelled) else str(error))
            return
        if not self._trust_server_certificate(error):
            failure_handler("Connection canceled because the server certificate was not trusted.")
            return
        self.run(function, result, failure_handler)

    def _trust_server_certificate(self, error):
        site = error.site
        cert = error.certificate
        if cert is None:
            QMessageBox.warning(self, "Certificate unavailable", "FluxFTP could not read the certificate presented by the server.")
            return False
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            names = ", ".join(san.get_values_for_type(x509.DNSName) +
                               [str(value) for value in san.get_values_for_type(x509.IPAddress)]) or "none"
        except Exception:
            names = "none"
        fingerprint = ":".join(error.fingerprint[i:i + 2] for i in range(0, len(error.fingerprint), 2)).upper()
        not_before = cert.not_valid_before_utc.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        not_after = cert.not_valid_after_utc.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        details = (
            f"Site: {site.name}\nServer: {normalize_host(site.host)}:{site.port}\nProtocol: {site.protocol}\n"
            f"Verification failure: {error.verify_message}\nSubject: {cert.subject.rfc4514_string()}\n"
            f"Issuer: {cert.issuer.rfc4514_string()}\nSubject alternative names: {names}\n"
            f"Valid from: {not_before}\nValid until: {not_after}\nSHA-256 fingerprint: {fingerprint}"
        )
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Icon.Warning)
        prompt.setWindowTitle("Untrusted server certificate")
        prompt.setText("The server presented a certificate that FluxFTP does not currently trust.")
        prompt.setInformativeText(
            "Review the certificate details. Trusting it saves this exact certificate's SHA-256 fingerprint "
            "for this site and requires that same certificate on future connections. Verify the fingerprint "
            "with the server administrator through a separate trusted channel before accepting."
            + ("\n\nPreviously trusted fingerprint: " + error.previous_fingerprint
               if error.previous_fingerprint else "")
        )
        prompt.setDetailedText(details)
        trust = prompt.addButton("Trust certificate and connect", QMessageBox.ButtonRole.AcceptRole)
        prompt.addButton("Reject", QMessageBox.ButtonRole.RejectRole)
        from .trust_service import certificate_current
        certificate_is_current = certificate_current(cert)
        if not certificate_is_current:
            trust.setEnabled(False)
            prompt.setInformativeText(
                prompt.informativeText() + "\n\nThis certificate is outside its validity period and cannot be trusted."
            )
        prompt.exec()
        if prompt.clickedButton() is not trust:
            return False
        from .trust_service import save_tls_pin
        return save_tls_pin(site, error.certificate_der, normalize_host(site.host), self.sites, self.save_sites)

