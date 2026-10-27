"""Managed client identities, encrypted at rest using the OS-backed vault."""
import io
import json
import secrets
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization as ser
from cryptography.hazmat.primitives.asymmetric import rsa, ec, ed25519
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from .credentials import CredentialVault

FORMATS = ('X.509 certificate for FTPS (RSA)', 'X.509 certificate for FTPS (ECDSA)',
           'Ed25519 key for SFTP', 'ECDSA key for SFTP', 'RSA key for SFTP')
RSA_SIZES = (2048, 3072, 4096, 6144, 8192)
CURVES = {256: ec.SECP256R1, 384: ec.SECP384R1, 521: ec.SECP521R1}


def public_bytes(key):
    return key.public_bytes(ser.Encoding.DER, ser.PublicFormat.SubjectPublicKeyInfo)


def algorithm(key, kind):
    if isinstance(key, rsa.RSAPrivateKey) and key.key_size >= 2048:
        return 'RSA', key.key_size
    if isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name in ('secp256r1', 'secp384r1', 'secp521r1'):
        return 'ECDSA', key.key_size
    if kind == 'ssh' and isinstance(key, ed25519.Ed25519PrivateKey):
        return 'Ed25519', 256
    raise ValueError('Unsupported key. Use RSA 2048+ bits, NIST ECDSA, or Ed25519 for SFTP. DSA is not supported.')


def private_bytes(key, password=''):
    encryption = ser.BestAvailableEncryption(password.encode('utf-8')) if password else ser.NoEncryption()
    return key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8, encryption)


def read_file(path):
    with Path(path).open('rb') as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Key and certificate files must be smaller than 4 MB.')
    return data


def load_private(data, password):
    if data.startswith(b'PuTTY-User-Key-File-'):
        raise ValueError('Convert PuTTY PPK to an OpenSSH private key with PuTTYgen before importing.')
    loader = (ser.load_ssh_private_key if b'BEGIN OPENSSH PRIVATE KEY' in data else
              ser.load_pem_private_key if b'-----BEGIN' in data else ser.load_der_private_key)
    try:
        return loader(data, password.encode('utf-8') if password else None)
    except TypeError:
        # A supplied password may be for strong protection of an unencrypted input.
        return loader(data, None)


def generate(format_name, size, subject=None, expires=None):
    if format_name not in FORMATS:
        raise ValueError('Unsupported key format.')
    kind = 'tls' if format_name.startswith('X.509') else 'ssh'
    if 'RSA' in format_name:
        if size not in RSA_SIZES:
            raise ValueError('Choose an RSA size of at least 2048 bits.')
        key = rsa.generate_private_key(public_exponent=65537, key_size=size)
    elif 'ECDSA' in format_name:
        if size not in CURVES:
            raise ValueError('Choose a supported ECDSA curve.')
        key = ec.generate_private_key(CURVES[size]())
    else:
        key = ed25519.Ed25519PrivateKey.generate()
    certificate = b''
    if kind == 'tls':
        subject = subject or {}
        if not subject.get('Common Name', '').strip():
            raise ValueError('Common Name is required.')
        attributes = []
        for label, oid in (('Common Name', NameOID.COMMON_NAME), ('Organization', NameOID.ORGANIZATION_NAME),
                ('Organization Unit', NameOID.ORGANIZATIONAL_UNIT_NAME), ('E-Mail', NameOID.EMAIL_ADDRESS),
                ('State or Province', NameOID.STATE_OR_PROVINCE_NAME), ('City/Town', NameOID.LOCALITY_NAME),
                ('Country', NameOID.COUNTRY_NAME)):
            value = subject.get(label, '').strip()
            if value:
                if label == 'Country':
                    value = value.upper()
                    if len(value) != 2 or not value.isascii() or not value.isalpha():
                        raise ValueError('Country must be a two-letter code.')
                attributes.append(x509.NameAttribute(oid, value))
        now = datetime.now(timezone.utc)
        if expires is None or expires <= now:
            raise ValueError('Expiration must be in the future.')
        name = x509.Name(attributes)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(expires)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                key_encipherment=isinstance(key, rsa.RSAPrivateKey), data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()).public_bytes(ser.Encoding.PEM))
    return kind, key, certificate


def import_identity(kind, private_path, public_path='', password='', bundle=False):
    data = read_file(private_path)
    certificate = b''
    if bundle:
        key, cert, chain = pkcs12.load_key_and_certificates(data, password.encode() if password else None)
        if key is None or cert is None:
            raise ValueError('PKCS#12 must contain a private key and its certificate.')
        certificate = b''.join(c.public_bytes(ser.Encoding.PEM) for c in [cert, *(chain or [])])
    else:
        key = load_private(data, password)
        if kind == 'tls':
            cert_data = read_file(public_path)
            certs = (x509.load_pem_x509_certificates(cert_data) if b'-----BEGIN' in cert_data
                     else [x509.load_der_x509_certificate(cert_data)])
            if not certs:
                raise ValueError('Select an X.509 certificate.')
            certificate = b''.join(c.public_bytes(ser.Encoding.PEM) for c in certs)
        elif public_path:
            supplied = read_file(public_path)
            loader = ser.load_pem_public_key if b'-----BEGIN' in supplied else ser.load_ssh_public_key
            if public_bytes(loader(supplied)) != public_bytes(key.public_key()):
                raise ValueError('The public and private keys do not match.')
    algorithm(key, kind)
    if kind == 'tls':
        cert = x509.load_pem_x509_certificate(certificate)
        if public_bytes(cert.public_key()) != public_bytes(key.public_key()):
            raise ValueError('The certificate and private key do not match.')
        now = datetime.now(timezone.utc)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError('The certificate is expired or not yet valid.')
        try:
            usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if ExtendedKeyUsageOID.CLIENT_AUTH not in usage and x509.ObjectIdentifier('2.5.29.37.0') not in usage:
                raise ValueError('This certificate does not permit TLS client authentication.')
        except x509.ExtensionNotFound:
            pass
    return kind, key, certificate


class KeyStore:
    def __init__(self, path):
        self.vault = CredentialVault(path)
        self.lock = RLock()

    def records(self):
        with self.lock:
            records = {key: json.loads(value) for key, value in self.vault._load().items()}
        return records

    def metadata(self):
        return {key: {k: v for k, v in record.items() if k != 'private'}
                for key, record in self.records().items()}

    def add(self, name, material, strong=False, password='', exportable=False):
        name = name.strip()
        if not name or len(name) > 128 or any(ord(c) < 32 for c in name):
            raise ValueError('Enter a name of 1–128 printable characters.')
        if strong and not password:
            raise ValueError('Strong private key protection requires a password.')
        kind, key, certificate = material
        alg, bits = algorithm(key, kind)
        public = (certificate if kind == 'tls' else
                  key.public_key().public_bytes(ser.Encoding.OpenSSH, ser.PublicFormat.OpenSSH) + b'\n')
        expiry = x509.load_pem_x509_certificate(certificate).not_valid_after_utc.isoformat() if certificate else ''
        record = dict(name=name, kind=kind, algorithm=alg, bits=bits, expires=expiry,
                      public=public.decode('ascii'), strong=bool(strong), exportable=bool(exportable),
                      private=private_bytes(key, password if strong else '').decode('ascii'))
        with self.lock:
            records = self.records()
            if any(r['name'].casefold() == name.casefold() for r in records.values()):
                raise ValueError('A key with that name already exists.')
            identity = uuid.uuid4().hex
            self.vault.set(identity, json.dumps(record))
        return identity

    def unlock(self, identity, password=''):
        record = self.records().get(identity)
        if record is None:
            raise ValueError('The selected managed key is missing. Choose another key in site properties.')
        try:
            key = load_private(record['private'].encode(), password if record['strong'] else '')
        except (ValueError, TypeError) as error:
            raise ValueError('Unable to unlock this key. Check its password.') from error
        algorithm(key, record['kind'])
        return record, key

    def export_private(self, identity, password, export_password):
        record = self.records().get(identity)
        if record is None or not record['exportable']:
            raise ValueError('Private key export is not allowed for this identity.')
        if not export_password:
            raise ValueError('Set a password for the exported private key.')
        record, key = self.unlock(identity, password)
        if record['kind'] == 'ssh':
            return key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.OpenSSH,
                                     ser.BestAvailableEncryption(export_password.encode()))
        return private_bytes(key, export_password)

    def remove(self, identity):
        with self.lock:
            self.vault.set(identity, '')


def ssh_identity(key):
    import paramiko
    algorithm_name, _ = algorithm(key, 'ssh')
    key_class = {'RSA': paramiko.RSAKey, 'ECDSA': paramiko.ECDSAKey, 'Ed25519': paramiko.Ed25519Key}[algorithm_name]
    encoded = key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.OpenSSH, ser.NoEncryption())
    return key_class.from_private_key(io.StringIO(encoded.decode('ascii')))


def load_tls_identity(context, record, key):
    # ssl accepts paths only. Temporary private material stays encrypted even on disk.
    password = secrets.token_urlsafe(40)
    with tempfile.TemporaryDirectory(prefix='fluxftp-client-') as directory:
        cert_path, key_path = Path(directory) / 'client.pem', Path(directory) / 'key.pem'
        cert_path.write_text(record['public'], encoding='ascii')
        key_path.write_bytes(private_bytes(key, password))
        context.load_cert_chain(str(cert_path), str(key_path), password=password)
