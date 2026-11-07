"""Saved-site, encrypted-credential, and data-folder persistence behavior."""
import json
import uuid
from pathlib import Path

from . import credentials
from .core import Site


class ConfigurationPersistence:
    """Persistence operations implemented against the owning desktop window."""
    def change_data_folder(self, folder):
        destination = Path(folder).expanduser().resolve()
        current = self.sites_file.parent.resolve()
        if destination == current:
            return True
        try:
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / "sites.json"
            existing = {}
            if target.exists():
                existing = json.loads(target.read_text("utf-8"))
                if not isinstance(existing, dict):
                    raise ValueError("The selected data folder contains an invalid sites.json")
            migrated = {**existing, **self.sites}
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(migrated, indent=2), "utf-8")
            temporary.replace(target)
            self.sites_file = target
            self.sites = migrated
            self.preferences.setValue("General/data_path", str(destination))
            self.preferences.sync()
            self.log.appendPlainText(f"FluxFTP data folder changed to {destination}")
            return True
        except (OSError, ValueError) as error:
            self.error(f"Could not change data folder: {error}")
            return False

    def save_sites(self, proposed=None):
        proposed = self.sites if proposed is None else proposed
        try:
            old_ids = {data.get("_credential_id") for data in self.sites.values() if data.get("_credential_id")}
            proposed = {name: dict(data) for name, data in proposed.items()}
            for data in proposed.values():
                data.setdefault("_credential_id", uuid.uuid4().hex)
            self.sites_file.parent.mkdir(parents=True, exist_ok=True)
            if self.preferences.value("General/backup_profiles", True, type=bool) and self.sites_file.exists():
                from datetime import date
                import shutil
                backup_dir = self.sites_file.parent / "backup"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_file = backup_dir / f"sites-{date.today():%Y%m%d}.json"
                if not backup_file.exists():
                    shutil.copy2(self.sites_file, backup_file)
                    backups = sorted(backup_dir.glob("sites-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                    for stale in backups[4:]:
                        stale.unlink()
            temp = self.sites_file.with_suffix(".tmp")
            temp.write_text(json.dumps(proposed, indent=2), "utf-8")
            temp.replace(self.sites_file)
            self.sites = proposed
            new_ids = {data.get("_credential_id") for data in proposed.values() if data.get("_credential_id")}
            for credential_id in old_ids - new_ids:
                try:
                    self.credential_vault.set(credential_id, "")
                    credentials.delete_legacy_password(credential_id)
                except RuntimeError:
                    pass
            return True
        except OSError as e:
            self.error(f"Could not save sites: {e}")
            return False

    @staticmethod
    def site_from_profile(data):
        from dataclasses import fields
        allowed = {field.name for field in fields(Site)}
        return Site(**{key: value for key, value in data.items() if key in allowed})

    def get_site_password(self, profile):
        credential_id = profile.get("_credential_id", "")
        if not credential_id:
            return ""
        password = self.credential_vault.get(credential_id)
        if password:
            return password
        return self.credential_vault.migrate_legacy_keyring_secret(credential_id)

    def set_proxy_password(self, password):
        self.proxy_password = password
        self._proxy_password_entered = True
        self.credential_vault.set(getattr(self, "proxy_credential_id", "proxy"), password)

    def set_site_password(self, site_name, password):
        profile = self.sites.get(site_name)
        if not profile:
            return
        credential_id = profile.get("_credential_id")
        if not credential_id:
            profile["_credential_id"] = credential_id = uuid.uuid4().hex
            if not self.save_sites(self.sites):
                raise RuntimeError("Could not save the site profile's secure credential reference.")
        self.credential_vault.set(credential_id, password)
        credentials.delete_legacy_password(credential_id)

