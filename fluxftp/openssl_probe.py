"""Offline TLS 1.2/1.3 handshake and certificate-verification runtime self-test."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def self_test():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'fluxftp-runtime.invalid')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('fluxftp-runtime.invalid')]), False)
            .sign(key, hashes.SHA256()))
    with tempfile.TemporaryDirectory(prefix='fluxftp-tls-probe-') as temporary:
        directory = Path(temporary)
        certificate = directory / 'cert.pem'
        private = directory / 'key.pem'
        certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        for protocol in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
            server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server.load_cert_chain(certificate, private)
            client = ssl.create_default_context(cafile=str(certificate))
            client.minimum_version = client.maximum_version = protocol
            handshake(client, server, 'fluxftp-runtime.invalid')
            try:
                handshake(client, server, 'wrong-host.invalid')
            except ssl.SSLCertVerificationError:
                pass
            else:
                raise RuntimeError('TLS hostname verification failed')
            try:
                handshake(ssl.create_default_context(), server, 'fluxftp-runtime.invalid')
            except ssl.SSLCertVerificationError:
                pass
            else:
                raise RuntimeError('TLS rejected-certificate self-test failed')


def handshake(client_context, server_context, hostname):
    incoming, outgoing, server_in, server_out = [ssl.MemoryBIO() for _ in range(4)]
    client = client_context.wrap_bio(incoming, outgoing, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    completed = [False, False]
    for _ in range(30):
        for index, connection in enumerate((client, server)):
            try:
                connection.do_handshake()
                completed[index] = True
            except ssl.SSLWantReadError:
                pass
        server_in.write(outgoing.read())
        incoming.write(server_out.read())
        if all(completed):
            client.write(b'FluxFTP runtime check')
            server_in.write(outgoing.read())
            if server.read() != b'FluxFTP runtime check':
                raise RuntimeError('TLS payload check failed')
            return
    raise RuntimeError('TLS handshake did not complete')
