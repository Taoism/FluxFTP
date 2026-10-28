"""TLS and SSH trust rules, persistence and evidence without desktop imports."""
import base64
import hashlib
import hmac
import os
import ssl
import tempfile
from pathlib import Path
from threading import RLock
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, replace
import paramiko
from cryptography import x509

_lock = RLock()


def certificate_current(cert, now=None):
    now = now or datetime.now(timezone.utc)
    return cert.not_valid_before_utc <= now <= cert.not_valid_after_utc

class PinnedCertificateChanged(ssl.SSLError):
    """A previously accepted server pin no longer matches the peer certificate."""


class CertificateTrustRequired(ssl.SSLCertVerificationError):
    """A verified TLS connection failed; UI may offer explicit per-site pinning."""

    def __init__(self, site, certificate_der, verification_error):
        self.site = site
        self.certificate_der = certificate_der
        self.fingerprint = hashlib.sha256(certificate_der).hexdigest()
        self.previous_fingerprint = getattr(verification_error, "previous_fingerprint", "")
        self.verify_code = getattr(verification_error, "verify_code", None)
        self.verify_message = getattr(verification_error, "verify_message", str(verification_error))
        try:
            self.certificate = x509.load_der_x509_certificate(certificate_der)
        except ValueError:
            self.certificate = None
        super().__init__(self.verify_code or 0, self.verify_message or "certificate is not trusted")


def _site_pin_applies(site, host):
    return bool(site.tls_cert_sha256 and
                site.tls_pin_host == host and
                int(site.tls_pin_port or 0) == int(site.port) and
                site.tls_pin_protocol == site.protocol)


def _verify_tls_pin(sock, expected_fingerprint):
    if not expected_fingerprint:
        return
    certificate_der = sock.getpeercert(binary_form=True)
    actual = hashlib.sha256(certificate_der or b"").hexdigest()
    if not hmac.compare_digest(actual.casefold(), expected_fingerprint.replace(":", "").casefold()):
        error = PinnedCertificateChanged("The FTPS server certificate changed after it was trusted for this site")
        error.previous_fingerprint = expected_fingerprint
        raise error
    try:
        cert = x509.load_der_x509_certificate(certificate_der)
        now = datetime.now(timezone.utc)
        if not certificate_current(cert, now):
            raise ssl.SSLError("The trusted FTPS server certificate is outside its validity period")
    except ValueError as error:
        raise ssl.SSLError("The trusted FTPS server certificate could not be parsed") from error


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def remember(path, host, key):
    with _lock:
        path = Path(path)
        keys = paramiko.HostKeys()
        if path.exists(): keys.load(str(path))
        previous = keys.lookup(host)
        if previous and (key.get_name() not in previous or previous[key.get_name()] != key):
            raise paramiko.SSHException('The SSH host key changed while confirming trust. Connection refused.')
        keys.add(host, key.get_name(), key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.known-hosts-', dir=path.parent)
        os.close(fd)
        try:
            keys.save(name)
            with open(name, 'r+b') as stream: os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name): os.unlink(name)


class VerifiedPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, path, verify):
        self.path, self.verify = Path(path), verify

    def missing_host_key(self, client, hostname, key):
        from .connection_model import Cancelled
        # Another worker may have confirmed this host since SSHClient loaded it.
        with _lock:
            keys = paramiko.HostKeys()
            if self.path.exists(): keys.load(str(self.path))
            old = keys.lookup(hostname)
            if old:
                if key.get_name() not in old or old[key.get_name()] != key:
                    raise paramiko.SSHException('SSH host key changed. Connection refused; verify it with the server administrator.')
                client.get_host_keys().add(hostname, key.get_name(), key)
                return
        if not self.verify(hostname, key):
            raise Cancelled('SSH fingerprint verification cancelled.')
        remember(self.path, hostname, key)
        client.get_host_keys().add(hostname, key.get_name(), key)


def certificate_details(der):
    cert = x509.load_der_x509_certificate(der)
    now = datetime.now(timezone.utc)
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(item.value) for item in names]
    except x509.ExtensionNotFound:
        names = []
    return dict(subject=cert.subject.rfc4514_string(), issuer=cert.issuer.rfc4514_string(),
                serial=hex(cert.serial_number), sha256=hashlib.sha256(der).hexdigest(),
                valid_from=cert.not_valid_before_utc.isoformat(), valid_until=cert.not_valid_after_utc.isoformat(),
                current=certificate_current(cert, now),
                days_remaining=int((cert.not_valid_after_utc-now).total_seconds() // 86400),
                alternative_names=names)


@dataclass(frozen=True)
class TLSPolicy:
    pin: str = ''

    @property
    def description(self):
        return 'Saved certificate SHA-256 pin' if self.pin else 'Certificate authority and hostname'


def tls_policy(site, host):
    return TLSPolicy(site.tls_cert_sha256 if _site_pin_applies(site, host) else '')


def tls_context(site, host):
    policy = tls_policy(site, host)
    if policy.pin:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context = ssl.create_default_context(cafile=site.ca_file or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context, policy.pin


def save_tls_pin(site, certificate_der, host, profiles, persist):
    """Persist an explicitly approved pin before updating the live site object."""
    details = certificate_details(certificate_der)
    if not details['current']: return False
    updates = dict(tls_cert_sha256=details['sha256'], tls_pin_host=host,
                   tls_pin_port=site.port, tls_pin_protocol=site.protocol)
    profile = asdict(replace(site, **updates))
    previous = profiles.get(site.name, {})
    if previous.get('_credential_id'): profile['_credential_id'] = previous['_credential_id']
    proposed = dict(profiles, **{site.name: profile})
    if not persist(proposed): return False
    for key, value in updates.items(): setattr(site, key, value)
    return True
