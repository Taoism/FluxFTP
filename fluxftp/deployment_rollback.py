"""Durable deployment revision journals, readback, and guarded rollback."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import re
import tempfile

from .core import Cancelled, Site
from .folder_sync import joined, lookup, PreviewChanged, local_fingerprint
from .queue_metadata import validate_job
from .sync_profiles import atomic_json
from .transfer_reliability import digest


@contextmanager
def binary_connection(connection):
    old = connection.transfer_options
    connection.transfer_options = replace(old, mode='Binary', exists_enabled=False, skip_enabled=False,
        crc_auto=False, crc_xcrc=False, keep_file_time=False)
    try: yield
    finally: connection.transfer_options = old


def remote_digest(connection, path, cancel):
    from .transfer_reliability import algorithms, UnsupportedHash
    if 'SHA-256' in algorithms(connection):
        try: return digest(connection, path, 'SHA-256', cancel)
        except UnsupportedHash: pass
    import tempfile
    with tempfile.TemporaryDirectory(prefix='fluxftp-verify-') as directory:
        local = Path(directory) / 'content'
        with binary_connection(connection):
            connection.transfer(local, path, False, cancel, lambda *_: None)
        return digest(None, local, 'SHA-256', cancel)




def revision_path(window, identity):
    if not re.fullmatch('[0-9a-f]{32}', identity): raise ValueError('Invalid deployment revision.')
    root = window.sites_file.parent / 'deployments'
    path = root / identity
    for parent in (path, *path.parents):
        if parent.exists() and local_fingerprint(parent)['link']:
            raise ValueError('Deployment backups cannot pass through links or junctions.')
    return path


def read_revision(directory):
    path = directory / 'manifest.json'
    if path.is_symlink() or path.stat().st_size > 32_000_000: raise ValueError('Invalid deployment journal.')
    journal = json.loads(path.read_text('utf-8'))
    validate_job(journal['plan'])
    if not isinstance(journal.get('entries'), dict): raise ValueError('Invalid deployment journal entries.')
    snapshots = journal.get('source_text', {})
    if not isinstance(snapshots, dict) or len(snapshots) > len(journal['plan']['files']):
        raise ValueError('Invalid deployment text snapshots.')
    total = 0
    allowed = {item['relative'] for item in journal['plan']['files']}
    for relative, content in snapshots.items():
        if relative not in allowed or not isinstance(content, str) or len(content.encode('utf-8')) > 256 * 1024:
            raise ValueError('Invalid deployment text snapshot entry.')
        total += len(content.encode('utf-8'))
        if total > 8 * 1024 * 1024:
            raise ValueError('Deployment text snapshots exceed the size limit.')
    return journal


def site_identity(site):
    return (site.host.casefold(), site.port, site.username, site.protocol, site.cloud_account)

def rollback_preview(directory, connection, cancel):
    journal = read_revision(directory)
    if site_identity(Site(**journal['site'])) != site_identity(connection.site):
        raise ValueError('Saved site now points to a different deployment endpoint.')
    rows = []
    for file in journal['plan']['files']:
        entry = journal['entries'].get(file['relative'])
        if not entry: continue
        if not re.fullmatch(r'\d{5}\.backup', entry.get('backup', '')): raise ValueError('Invalid deployment backup path.')
        if entry.get('before_sha256'):
            backup = directory / entry['backup']
            if backup.is_symlink() or digest(None, backup, 'SHA-256', cancel) != entry['before_sha256']:
                raise ValueError('Deployment backup is missing or changed: ' + file['relative'])
        root, relative = journal['plan']['profile']['destination'], file['relative']
        current = lookup(connection, root, relative)
        if current is None:
            if file['destination'] is None:
                rows.append(dict(relative=relative, action='Already absent', fingerprint=None, digest=None))
                continue
            raise PreviewChanged('Destination was removed after deployment: ' + relative)
        if current['directory'] or current['link']: raise PreviewChanged('Destination type changed: ' + relative)
        actual = remote_digest(connection, joined(root, relative, True), cancel)
        if lookup(connection, root, relative) != current: raise PreviewChanged('Destination changed during rollback preview.')
        if actual == entry.get('before_sha256'):
            action = 'Already restored'
        elif actual == file['source_sha256']:
            action = 'Restore backup' if file['destination'] else 'Delete new file'
        else: raise PreviewChanged('Destination differs from both the deployed file and its backup: ' + relative)
        rows.append(dict(relative=relative, action=action, fingerprint=current, digest=actual))
    return journal, rows


def rollback(window, connection, job, worker):
    plan = validate_job(job['deployment'])
    directory = revision_path(window, plan['id'])
    journal, rows = rollback_preview(directory, connection, worker.cancel)
    # The queued profile must match the immutable original journal.
    if plan['profile'] != journal['plan']['profile'] or plan['files'] != journal['plan']['files']:
        raise ValueError('Rollback plan differs from its deployment journal.')
    from .verification_reports import tracked_file, evidence
    @tracked_file
    def restore(source_connection, destination_connection, src, dst, is_dir, row, entry):
        root = plan['profile']['destination']
        if lookup(connection, root, row['relative']) != row['fingerprint'] or remote_digest(connection, dst, worker.cancel) != row['digest']:
            raise PreviewChanged('Destination changed before rollback: ' + row['relative'])
        if row['action'] == 'Delete new file':
            connection.remove(dst, False)
            evidence('rollback', 'deleted', detail='New deployment file removed after content verification.')
        else:
            if digest(None, src, 'SHA-256', worker.cancel) != entry['before_sha256']:
                raise PreviewChanged('Backup changed before rollback.')
            with binary_connection(connection): connection.transfer(src, dst, True, worker.cancel, worker.report)
            actual = remote_digest(connection, dst, worker.cancel)
            evidence('checksum', 'verified' if actual == entry['before_sha256'] else 'mismatch', entry['before_sha256'], actual, 'SHA-256')
            if actual != entry['before_sha256']: raise PreviewChanged('Restored file failed verification; backup retained.')
        return True
    for row in rows:
        if worker.cancel.is_set(): raise Cancelled('Rollback cancelled; backups retained.')
        if row['action'].startswith('Already'): continue
        entry = journal['entries'][row['relative']]
        src, dst = directory / entry['backup'], joined(plan['profile']['destination'], row['relative'], True)
        restore(None, connection, str(src), dst, False, row, entry)
        entry['state'] = 'restored'
        atomic_json(directory / 'manifest.json', journal)
    journal['state'] = 'rolled_back'
    atomic_json(directory / 'manifest.json', journal)
    return dict(transferred=True, messages=['Rollback complete. Empty remote folders and local backups retained.'])
