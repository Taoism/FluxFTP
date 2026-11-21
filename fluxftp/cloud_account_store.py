"""Encrypted cloud account records and transactional account editing."""
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from .cloud_registry import PROVIDERS


class CloudAccountStore:
    """Persistence and staged-edit operations mixed into CloudService."""

    def begin_edit(self, identity):
        """Stage account edits in an encrypted snapshot, preserving the account ID."""
        from .cloud import CloudService

        with self.lock:
            if identity not in self.accounts():
                raise ValueError('The cloud account no longer exists.')
            self.start()
            draft_dir = self.directory / ('edit-' + uuid.uuid4().hex)
            draft_dir.mkdir()
            draft = CloudService(draft_dir, self.vault)
            draft.edit_origin = self
            draft.edit_baseline = (self.config_path.read_bytes(), self.settings_path.read_bytes())
            draft.edit_identity = identity
            try:
                draft.config_path.write_bytes(draft.edit_baseline[0])
                draft.settings_path.write_bytes(draft.edit_baseline[1])
            except BaseException:
                draft.discard_edit()
                raise
            return draft

    def discard_edit(self):
        self.close()
        origin = self.edit_origin
        directory = self.directory.resolve()
        if directory.parent != origin.directory.resolve() or not directory.name.startswith('edit-'):
            raise RuntimeError('Invalid cloud edit directory.')
        if directory.exists():
            shutil.rmtree(directory)

    @staticmethod
    def _replace_file(path, content):
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def commit_edit(self):
        """Commit a staged encrypted snapshot only if the source is unchanged."""
        origin = self.edit_origin
        with origin.lock:
            baseline = self.edit_baseline
            current = (origin.config_path.read_bytes(), origin.settings_path.read_bytes())
            if current != baseline:
                raise RuntimeError('Cloud settings changed while editing. Cancel and open Edit again to keep those changes.')
            self.close()
            updated = (self.config_path.read_bytes(), self.settings_path.read_bytes())
            origin.close()
            if (origin.config_path.read_bytes(), origin.settings_path.read_bytes()) != baseline:
                raise RuntimeError('Cloud settings changed while editing. Cancel and open Edit again.')
            paths = (origin.config_path, origin.settings_path)
            try:
                for path, content in zip(paths, updated):
                    self._replace_file(path, content)
            except BaseException:
                for path, content in zip(paths, baseline):
                    self._replace_file(path, content)
                raise

    def accounts(self):
        with self.lock:
            if not self.settings_path.exists():
                return {}
            records = json.loads(self.settings_path.read_text('utf-8'))
            if not isinstance(records, dict):
                raise ValueError('Invalid cloud account list.')
            for identity, record in records.items():
                if not re.fullmatch(r'[0-9a-f]{32}', identity) or not isinstance(record, dict) or record.get('provider') not in PROVIDERS:
                    raise ValueError('Invalid cloud account entry.')
            return records

    def save_account(self, identity, name, provider):
        if not re.fullmatch(r'[0-9a-f]{32}', identity) or provider not in PROVIDERS:
            raise ValueError('Invalid cloud account.')
        name = name.strip()
        if not name or len(name) > 128 or any(ord(c) < 32 for c in name):
            raise ValueError('Enter a cloud account name of 1–128 characters.')
        with self.lock:
            records = self.accounts()
            if any(r['name'].casefold() == name.casefold() for k, r in records.items() if k != identity):
                raise ValueError('That cloud account name already exists.')
            records[identity] = dict(name=name, provider=provider)
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(records, indent=2), encoding='utf-8')
            temporary.replace(self.settings_path)

    def finish_account(self, identity, name, provider):
        details = self.call('config/get', {'name': 'c' + identity})
        if details.get('type') != PROVIDERS[provider][0]:
            raise ValueError('This authorization belongs to a different storage provider. Cancel and add that provider separately.')
        expected = {'Microsoft OneDrive': 'personal', 'Microsoft OneDrive for Business': 'business',
                    'Microsoft SharePoint Online': 'documentLibrary'}.get(provider)
        if expected and details.get('drive_type') != expected:
            raise ValueError('The selected drive does not match this provider. Choose Personal, Business, or a SharePoint document library as appropriate.')
        self.save_account(identity, name, provider)

    def remove_account(self, identity):
        with self.lock:
            records = self.accounts()
            self.call('config/delete', {'name': 'c' + identity})
            records.pop(identity, None)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(records, indent=2), encoding='utf-8')
            temporary.replace(self.settings_path)
