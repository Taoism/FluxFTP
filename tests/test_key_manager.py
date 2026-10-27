import ssl
import socket
import threading
from datetime import datetime, timedelta, timezone
from dataclasses import asdict

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization as ser
from cryptography.hazmat.primitives.serialization import pkcs12
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog

from fluxftp.key_store import (KeyStore, FORMATS, generate, import_identity, private_bytes,
                               ssh_identity, load_tls_identity, public_bytes)
from fluxftp.key_manager import KeyManager, KeyForm
from fluxftp.core import Site, Connection, PROTOCOLS, Cancelled
from fluxftp.app import SiteDialog
from fluxftp.preferences import PreferencesDialog
from test_queue import window
from test_sftp import ssh_server
from test_integration import servers
from test_ui import settle


def material(format_name=FORMATS[2], size=256):
    return generate(format_name, size, {'Common Name': 'FluxFTP client', 'Country': 'US'},
                    datetime.now(timezone.utc) + timedelta(days=30))


@pytest.mark.parametrize('format_name,size', [(FORMATS[0], 2048), (FORMATS[1], 384),
    (FORMATS[2], 256), (FORMATS[3], 521), (FORMATS[4], 2048)])
def test_generate_store_protection_and_export(tmp_path, format_name, size):
    store = KeyStore(tmp_path / 'keys.enc')
    made = material(format_name, size)
    identity = store.add('test', made, strong=True, password='private password', exportable=True)
    disk = store.vault.path.read_text()
    assert 'PRIVATE KEY' not in disk and 'private password' not in disk and 'test' not in disk
    with pytest.raises(ValueError, match='unlock'):
        store.unlock(identity, 'wrong')
    record, key = store.unlock(identity, 'private password')
    assert public_bytes(key.public_key()) == public_bytes(made[1].public_key())
    exported = store.export_private(identity, 'private password', 'export password')
    loader = ser.load_ssh_private_key if record['kind'] == 'ssh' else ser.load_pem_private_key
    restored = loader(exported, b'export password')
    assert public_bytes(restored.public_key()) == public_bytes(key.public_key())
    with pytest.raises(ValueError, match='already exists'):
        store.add('TEST', made)
    with pytest.raises(ValueError):
        store.export_private(identity, 'private password', '')
    second = store.add('nonexportable', made)
    with pytest.raises(ValueError, match='not allowed'):
        store.export_private(second, '', 'export password')
    store.remove(second)
    assert second not in store.metadata()


def test_import_formats_matching_and_weak_rejection(tmp_path):
    kind, key, certificate = material(FORMATS[0], 2048)
    private, public = tmp_path / 'private.pem', tmp_path / 'cert.der'
    private.write_bytes(private_bytes(key, 'password'))
    cert = x509.load_pem_x509_certificate(certificate)
    public.write_bytes(cert.public_bytes(ser.Encoding.DER))
    imported = import_identity(kind, private, public, 'password')
    assert public_bytes(imported[1].public_key()) == public_bytes(key.public_key())
    p12 = tmp_path / 'bundle.pfx'
    p12.write_bytes(pkcs12.serialize_key_and_certificates(b'client', key, cert, None,
                    ser.BestAvailableEncryption(b'password')))
    assert import_identity('tls', p12, password='password', bundle=True)[2] == certificate
    private.write_bytes(private_bytes(material(FORMATS[0], 2048)[1]))
    with pytest.raises(ValueError, match='do not match'):
        import_identity('tls', private, public)
    with pytest.raises(ValueError, match='2048'):
        generate(FORMATS[4], 1024)
    with pytest.raises(ValueError):
        generate('DSA key for SFTP', 2048)
    key = material()[1]
    private.write_bytes(key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.OpenSSH,
                                         ser.BestAvailableEncryption(b'password')))
    assert import_identity('ssh', private, password='password')[0] == 'ssh'
    public.write_bytes(material()[1].public_key().public_bytes(ser.Encoding.OpenSSH, ser.PublicFormat.OpenSSH))
    with pytest.raises(ValueError, match='do not match'):
        import_identity('ssh', private, public, 'password')
    private.write_bytes(b'PuTTY-User-Key-File-3: ssh-ed25519')
    with pytest.raises(ValueError, match='PuTTYgen'):
        import_identity('ssh', private)


@pytest.mark.parametrize('format_name,size', [(FORMATS[2], 256), (FORMATS[3], 384), (FORMATS[4], 2048)])
def test_managed_sftp_public_key_auth(ssh_server, tmp_path, format_name, size):
    root, site = ssh_server
    store = KeyStore(tmp_path / 'keys.enc')
    site.managed_key = store.add('client', material(format_name, size))
    record, key = store.unlock(site.managed_key)
    (root / 'authorized_keys').write_text(record['public'])
    with Connection(site, identity_loader=lambda s: store.unlock(s.managed_key)) as connection:
        connection.mkdir('/made-with-key')
        assert any(e.name == 'made-with-key' for e in connection.list('/'))


@pytest.mark.parametrize('protocol', PROTOCOLS[:3])
def test_managed_ftps_modes(servers, tmp_path, protocol):
    root, site = servers(tls=True, implicit=protocol == PROTOCOLS[2])
    site.protocol = protocol
    store = KeyStore(tmp_path / 'keys.enc')
    site.managed_key = store.add('client', material(FORMATS[1], 256))
    with Connection(site, 'password', identity_loader=lambda s: store.unlock(s.managed_key)) as connection:
        connection.mkdir('/tls-client')
        assert (root / 'tls-client').is_dir()


def test_tls_requires_and_receives_managed_certificate(servers, tmp_path):
    # Use the loopback fixture's server certificate; require a separate client certificate.
    _, site = servers(tls=True)
    store = KeyStore(tmp_path / 'keys.enc')
    identity = store.add('client', material(FORMATS[0], 2048))
    record, key = store.unlock(identity)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(site.ca_file, str(tmp_path / 'key.pem'))
    server.verify_mode = ssl.CERT_REQUIRED
    server.load_verify_locations(cadata=record['public'])
    client = ssl.create_default_context(cafile=site.ca_file)
    load_tls_identity(client, record, key)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(5)
    received = []
    def serve():
        try:
            sock, _ = listener.accept()
            with server.wrap_socket(sock, server_side=True) as tls:
                received.append(tls.getpeercert(binary_form=True))
                tls.sendall(b'ok')
        except Exception as error:
            received.append(error)
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        with socket.create_connection(listener.getsockname(), timeout=5) as sock:
            with client.wrap_socket(sock, server_hostname='localhost') as tls:
                assert tls.recv(2) == b'ok'
    finally:
        thread.join(6)
        listener.close()
    assert received == [x509.load_pem_x509_certificate(record['public'].encode()).public_bytes(ser.Encoding.DER)]


def test_key_manager_reference_site_and_prompts(window, monkeypatch):
    identity = window.key_access.store.add('protected', material(), True, 'password', False)
    dialog = KeyManager(window)
    assert [dialog.tree.headerItem().text(i) for i in range(4)] == ['Name', 'Type', 'Expiration', 'Key Size']
    assert dialog.tree.topLevelItemCount() == 1
    form = KeyForm(dialog)
    form.format.setCurrentText(FORMATS[2])
    assert form.size.currentText() == '256' and not form.size.isEnabled()
    form.format.setCurrentText(FORMATS[0])
    assert form.size.currentText() == '3072'
    assert '1024' not in [form.size.itemText(i) for i in range(form.size.count())]
    prefs = PreferencesDialog(window)
    assert 'Key Manager' in prefs.page_by_name
    site_dialog = SiteDialog(window, {})
    profile = asdict(Site(name='SSH', host='localhost', protocol=PROTOCOLS[3], managed_key=identity))
    site_dialog.load(profile)
    assert site_dialog.site().managed_key == identity
    calls = []
    def answer(*args, **kwargs):
        calls.append(True)
        return 'password', True
    monkeypatch.setattr(QInputDialog, 'getText', answer)
    for _ in range(2):
        assert window.key_access.resolve(site_dialog.site())[0]['name'] == 'protected'
    assert len(calls) == 2
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('', False))
    with pytest.raises(Cancelled):
        window.key_access.resolve(site_dialog.site())
    wrong_protocol = Site(host='localhost', managed_key=identity, protocol=PROTOCOLS[0])
    with pytest.raises(ValueError, match='incompatible'):
        window.key_access.resolve(wrong_protocol)
    for widget in (site_dialog, prefs, form, dialog):
        widget.close()


def test_form_background_save_and_worker_unlock(window, monkeypatch):
    dialog = KeyManager(window)
    form = KeyForm(dialog)
    form.name.setText('saved from form')
    form.format.setCurrentText(FORMATS[2])
    form.strong.setChecked(True)
    form.password.setText('test secret')
    form.save()
    settle(window)
    assert form.result() == form.DialogCode.Accepted
    identity = next(iter(window.key_access.store.metadata()))
    site = Site(host='localhost', protocol=PROTOCOLS[3], managed_key=identity)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('test secret', True))
    results = []
    window.run(lambda worker: window.key_access.resolve(site)[0]['name'], results.append)
    settle(window)
    assert results == ['saved from form']
    # Cancelling a protected identity does not trigger repeated retry prompts.
    calls = []
    def cancelled(site):
        calls.append(True)
        raise Cancelled('Cancelled')
    with pytest.raises(Cancelled):
        Connection(site, max_retries=2, identity_loader=cancelled).connect()
    assert calls == [True]
    first = dict(name='first', done=threading.Event(), password=None)
    second = dict(name='second', done=threading.Event(), password=None)
    prompts = []
    def concurrent_prompt(*args, **kwargs):
        prompts.append(args[2])
        if len(prompts) == 1:
            window.key_access.prompt(second)
            assert not second['done'].is_set()
        return 'test secret', True
    monkeypatch.setattr(QInputDialog, 'getText', concurrent_prompt)
    window.key_access.prompt(first)
    assert first['done'].is_set() and second['done'].is_set() and len(prompts) == 2
    form.close()
    dialog.close()
