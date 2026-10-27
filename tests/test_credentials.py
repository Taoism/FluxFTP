import json

import pytest

from fluxftp import credentials
secure_backend = credentials._keyring

def test_vault_encrypts_and_roundtrips_site_and_proxy_passwords(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "_key", lambda create=False: b"\x22" * 32)
    path = tmp_path / "credentials.enc"
    vault = credentials.CredentialVault(path)
    vault.set("site-123", "site-password-東京")
    vault.set("proxy", "proxy-secret")

    encrypted = path.read_bytes()
    assert b"site-password" not in encrypted
    assert b"proxy-secret" not in encrypted
    assert json.loads(encrypted)["format"] == credentials.FILE_FORMAT
    assert vault.get("site-123") == "site-password-東京"
    assert vault.get("proxy") == "proxy-secret"

    vault.set("site-123", "")
    assert vault.get("site-123") == ""
    assert vault.get("proxy") == "proxy-secret"


def test_vault_rejects_tampered_ciphertext(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "_key", lambda create=False: b"\x33" * 32)
    path = tmp_path / "credentials.enc"
    vault = credentials.CredentialVault(path)
    vault.set("site-123", "secret")
    envelope = json.loads(path.read_text("utf-8"))
    data = envelope["ciphertext"]
    envelope["ciphertext"] = ("A" if data[0] != "A" else "B") + data[1:]
    path.write_text(json.dumps(envelope), "utf-8")
    with pytest.raises(RuntimeError, match="Could not decrypt"):
        vault.get("site-123")


def test_legacy_password_migrates_into_encrypted_file(tmp_path, monkeypatch):
    legacy = {"old-id": "previous-version-secret"}
    monkeypatch.setattr(credentials, "_key", lambda create=False: b"\x44" * 32)
    monkeypatch.setattr(credentials, "get_legacy_password", lambda key: legacy.get(key, ""))
    monkeypatch.setattr(credentials, "delete_legacy_password", lambda key: legacy.pop(key, None))
    vault = credentials.CredentialVault(tmp_path / "credentials.enc")

    assert vault.migrate_legacy_keyring_secret("old-id") == "previous-version-secret"
    assert vault.get("old-id") == "previous-version-secret"
    assert not legacy
    assert b"previous-version-secret" not in vault.path.read_bytes()


def test_concurrent_vault_writers_preserve_every_record(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    vault = credentials.CredentialVault(tmp_path / 'concurrent.enc')
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: vault.set(str(index), 'secret-' + str(index)), range(30)))
    assert all(vault.get(str(i)) == 'secret-' + str(i) for i in range(30))


def test_plaintext_backend_refused_os_backend_selected_from_chain(monkeypatch):
    import keyring
    from types import SimpleNamespace
    Plain = type('Plain', (), {'__module__': 'keyrings.alt.file', 'priority': 10})
    monkeypatch.setattr(keyring, 'get_keyring', lambda: Plain())
    with pytest.raises(RuntimeError, match='OS-protected'): secure_backend()
    Secure = type('Secure', (), {'__module__': 'keyring.backends.Windows', 'priority': 5,
        'get_password': lambda self, *args: 'protected', 'set_password': lambda *args: None,
        'delete_password': lambda *args: None})
    monkeypatch.setattr(keyring, 'get_keyring', lambda: SimpleNamespace(priority=10, backends=[Plain(), Secure()]))
    assert secure_backend().get_password('service', 'account') == 'protected'
