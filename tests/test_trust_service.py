from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl
import subprocess
import sys
from types import SimpleNamespace
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fluxftp.core import Site
from fluxftp.trust_service import (certificate_details, tls_context, tls_policy,
    save_tls_pin, _verify_tls_pin, PinnedCertificateChanged)


@pytest.fixture
def der():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test.invalid')])
    now = datetime.now(timezone.utc)
    return (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=2))
        .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER))


def test_service_import_is_headless():
    result = subprocess.run([sys.executable, '-c', "import fluxftp.trust_service; import sys; assert not any(n.startswith('PySide') for n in sys.modules)"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_tls_pin_scope_context_and_mismatch(der):
    sha = certificate_details(der)['sha256']
    site = Site(host='test.invalid', tls_cert_sha256=sha, tls_pin_host='test.invalid', tls_pin_port=21, tls_pin_protocol='FTPS · AUTH TLS')
    context, pin = tls_context(site, site.host)
    assert pin == sha and not context.check_hostname and context.verify_mode == ssl.CERT_NONE
    for other in (replace(site, port=990), replace(site, protocol='FTPS · Implicit TLS'), replace(site, host='other.invalid')):
        context, pin = tls_context(other, other.host)
        assert not pin and context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    sock = SimpleNamespace(getpeercert=lambda **_: der)
    _verify_tls_pin(sock, sha)
    with pytest.raises(PinnedCertificateChanged): _verify_tls_pin(sock, '0'*64)


def test_failed_persistence_does_not_mutate_live_trust(der):
    site = Site(name='test', host='test.invalid')
    profiles = {'test': dict(asdict(site), _credential_id='vault-reference')}
    assert not save_tls_pin(site, der, site.host, profiles, lambda _: False)
    assert not site.tls_cert_sha256 and not profiles['test']['tls_cert_sha256']
    saved = []
    assert save_tls_pin(site, der, site.host, profiles, lambda value: saved.append(value) or True)
    assert saved[0]['test']['_credential_id'] == 'vault-reference'
    assert tls_policy(site, site.host).pin == certificate_details(der)['sha256']


def test_persistence_exception_leaves_site_unchanged(der):
    site = Site(host='test.invalid')
    def fail(_): raise OSError('disk unavailable')
    with pytest.raises(OSError): save_tls_pin(site, der, site.host, {}, fail)
    assert not site.tls_cert_sha256
