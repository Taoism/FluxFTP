"""AES-GCM encrypted credential file with its key protected by the OS vault."""

from __future__ import annotations

import base64
import json
import os
import secrets
from threading import RLock
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SERVICE_NAME = "FluxFTP"
KEY_ACCOUNT = "encrypted-vault-key-v1"
FILE_FORMAT = "fluxftp-encrypted-credentials"
FILE_VERSION = 1
AAD = b"FluxFTP encrypted credential file v1"
_vault_lock = RLock()


def _keyring():
    try:
        import keyring
    except ImportError as error:
        raise RuntimeError("Secure password storage is unavailable. Install FluxFTP with its keyring dependency.") from error
    try:
        backend = keyring.get_keyring()
    except Exception as error:
        raise RuntimeError("Could not access the system credential vault. Passwords were not saved.") from error
    if getattr(backend, "priority", 0) <= 0:
        raise RuntimeError("No secure system credential vault is available. Passwords were not saved.")
    # Never accept plaintext file backends simply because they advertise a
    # positive priority. Select an OS-protected backend from a chain if needed.
    allowed = ('keyring.backends.Windows', 'keyring.backends.macOS',
               'keyring.backends.SecretService', 'keyring.backends.kwallet',
               'keyring.backends.libsecret')
    candidates = getattr(backend, 'backends', [backend])
    selected = next((item for item in candidates if type(item).__module__ in allowed and getattr(item, 'priority', 0) > 0), None)
    if selected is None:
        raise RuntimeError('An OS-protected credential vault is required. Plaintext and third-party password backends are not used.')
    from types import SimpleNamespace
    return SimpleNamespace(get_password=selected.get_password, set_password=selected.set_password,
                           delete_password=selected.delete_password, errors=keyring.errors)


def _key(create=False):
    keyring = _keyring()
    try:
        stored = keyring.get_password(SERVICE_NAME, KEY_ACCOUNT)
        if stored:
            result = base64.b64decode(stored, validate=True)
            if len(result) != 32:
                raise RuntimeError("The encrypted credential file key is invalid.")
            return result
        if not create:
            raise RuntimeError("The encrypted credential file key is missing from the system credential vault.")
        result = secrets.token_bytes(32)
        keyring.set_password(SERVICE_NAME, KEY_ACCOUNT, base64.b64encode(result).decode("ascii"))
        return result
    except keyring.errors.KeyringError as error:
        raise RuntimeError("Could not access the encrypted credential file key in the system credential vault.") from error
    except (ValueError, TypeError) as error:
        raise RuntimeError("The encrypted credential file key is invalid.") from error


class CredentialVault:
    """Read and write password records in one authenticated encrypted file."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            envelope = json.loads(self.path.read_text("utf-8"))
            if not isinstance(envelope, dict):
                raise RuntimeError("The encrypted credential file has an invalid envelope.")
            if envelope.get("format") != FILE_FORMAT or envelope.get("version") != FILE_VERSION:
                raise RuntimeError("Unsupported encrypted credential file format.")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            plaintext = AESGCM(_key()).decrypt(nonce, ciphertext, AAD)
            records = json.loads(plaintext.decode("utf-8"))
            if not isinstance(records, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in records.items()):
                raise RuntimeError("The encrypted credential file has invalid contents.")
            return records
        except RuntimeError:
            raise
        except (OSError, ValueError, KeyError, TypeError, UnicodeError, InvalidTag) as error:
            raise RuntimeError("Could not decrypt the credential file. It may be damaged or its key is unavailable.") from error

    def _save(self, records):
        try:
            key = _key(create=True)
            nonce = secrets.token_bytes(12)
            plaintext = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            envelope = {
                "format": FILE_FORMAT,
                "version": FILE_VERSION,
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(AESGCM(key).encrypt(nonce, plaintext, AAD)).decode("ascii"),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            with temporary.open('w', encoding='utf-8') as stream:
                stream.write(json.dumps(envelope, indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            temporary.replace(self.path)
        except RuntimeError:
            raise
        except OSError as error:
            raise RuntimeError("Could not write the encrypted credential file.") from error

    def get(self, credential_id):
        if not credential_id:
            return ""
        return self._load().get(str(credential_id), "")

    def set(self, credential_id, password):
        with _vault_lock:
            self._set(credential_id, password)

    def _set(self, credential_id, password):
        if not credential_id:
            raise RuntimeError("A credential ID is required to save this password.")
        records = self._load()
        if password:
            records[str(credential_id)] = str(password)
        else:
            records.pop(str(credential_id), None)
        if records:
            self._save(records)
        elif self.path.exists():
            try:
                self.path.unlink()
            except OSError as error:
                raise RuntimeError("Could not remove the empty encrypted credential file.") from error

    def migrate_legacy_keyring_secret(self, credential_id):
        """Move a password saved by older FluxFTP builds into the encrypted file."""
        if not credential_id:
            return ""
        password = get_legacy_password(credential_id)
        if password:
            self.set(credential_id, password)
            delete_legacy_password(credential_id)
        return password


def get_legacy_password(credential_id):
    keyring = _keyring()
    try:
        return keyring.get_password(SERVICE_NAME, str(credential_id)) or ""
    except keyring.errors.KeyringError as error:
        raise RuntimeError("Could not migrate the site's saved password from the previous storage format.") from error


def delete_legacy_password(credential_id):
    keyring = _keyring()
    try:
        keyring.delete_password(SERVICE_NAME, str(credential_id))
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as error:
        raise RuntimeError("Could not remove the site's old password entry from the system credential vault.") from error
