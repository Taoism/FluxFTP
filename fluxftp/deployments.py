"""Reviewed local-to-remote deployments with durable backups and guarded rollback."""
from .queue_metadata import validate_profile, validate_job
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import posixpath
from pathlib import Path
import re
import time
import uuid

from .core import Cancelled, Site
from .folder_sync import scan, lookup, joined, validate_spec, execute_sync, PreviewChanged, local_fingerprint
from .sync_profiles import atomic_json
from .transfer_reliability import digest
from .watch_upload import excluded




def preview(profile, connection, cancel):
    validate_profile(profile)
    left = scan(None, profile['source'], cancel, exclude=lambda path: excluded(path, profile['excludes']))
    right = scan(connection, profile['destination'], cancel, exclude=lambda path: excluded(path, profile['excludes']))
    from .folder_sync import compare
    rows = compare(left, right, rule='Size and time', tolerance=2)
    files, blocked = [], []
    # Read back same-size files to avoid missing same-size/time content changes.
    for row in rows:
        if cancel.is_set(): raise Cancelled('Deployment preview cancelled')
        relative, source, target = row['path'], row['left'], row['right']
        if excluded(relative, profile['excludes']) or not source: continue
        if row['reason'].startswith('Blocked'):
            blocked.append(relative)
            continue
        if source['directory']: continue
        sha = digest(None, joined(profile['source'], relative, False), 'SHA-256', cancel)
        if lookup(None, profile['source'], relative) != source:
            raise PreviewChanged('Local file changed during deployment preview.')
        if target and source['size'] == target['size']:
            # Prefer native SHA-256. Fallback readback is cancellable and uses
            # the normal transfer engine (including shared bandwidth limits).
            actual = remote_digest(connection, joined(profile['destination'], relative, True), cancel)
            if lookup(connection, profile['destination'], relative) != target:
                raise PreviewChanged('Remote file changed during deployment preview.')
            if actual == sha: continue
        files.append(dict(relative=relative, source=source, destination=target, source_sha256=sha))
    if blocked: raise ValueError('Deployment contains blocked links, type/name conflicts: ' + ', '.join(blocked[:10]))
    return files


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
    return journal


def site_identity(site):
    return (site.host.casefold(), site.port, site.username, site.protocol, site.cloud_account)


def execute(window, connection, job, worker):
    plan = validate_job(job['deployment'])
    directory = revision_path(window, plan['id'])
    if plan['rollback']: return rollback(window, connection, job, worker)
    if directory.exists():
        raise PreviewChanged('This deployment revision already started. Inspect its rollback preview or create a fresh deployment preview.')
    directory.mkdir(parents=True)
    journal = dict(version=1, created=time.time(), state='running', plan=plan,
                   site=asdict(connection.site), entries={})
    manifest = directory / 'manifest.json'
    atomic_json(manifest, journal)
    from .verification_reports import tracked_file, evidence
    @tracked_file
    def publish(source_connection, destination_connection, src, dst, is_dir, file):
        sync = dict(source_root=plan['profile']['source'], destination_root=plan['profile']['destination'],
            relative=file['relative'], source=file['source'], destination=file['destination'])
        options = replace(job['options'], mode='Binary', hash_verification='Off', exists_enabled=False,
                          skip_enabled=False, crc_auto=False, crc_xcrc=False)
        result = execute_sync(None, connection, dict(src=src, dst=dst, directory=False, source_site=None,
            destination_site=connection.site, sync=sync, options=options), worker)
        if plan['profile']['verification'] == 'Checksum readback':
            actual = remote_digest(connection, dst, worker.cancel)
            evidence('checksum', 'verified' if actual == file['source_sha256'] else 'mismatch', file['source_sha256'], actual, 'SHA-256')
            if actual != file['source_sha256']: raise PreviewChanged('Deployment readback checksum mismatch; backup retained.')
        else:
            actual = connection.size(dst)
            evidence('size', 'verified' if actual == file['source']['size'] else 'mismatch', file['source']['size'], actual)
            if actual != file['source']['size']: raise PreviewChanged('Deployment destination size mismatch; backup retained.')
        if digest(None, src, 'SHA-256', worker.cancel) != file['source_sha256']:
            raise PreviewChanged('Source changed during deployment; backup retained for review.')
        return result
    try:
        for index, file in enumerate(plan['files']):
            if worker.cancel.is_set(): raise Cancelled('Deployment cancelled; backups retained for rollback review.')
            relative = file['relative']
            src, dst = joined(plan['profile']['source'], relative, False), joined(plan['profile']['destination'], relative, True)
            if lookup(None, plan['profile']['source'], relative) != file['source'] or digest(None, src, 'SHA-256', worker.cancel) != file['source_sha256']:
                raise PreviewChanged('Deployment source changed since preview: ' + relative)
            if lookup(connection, plan['profile']['destination'], relative) != file['destination']:
                raise PreviewChanged('Deployment destination changed since preview: ' + relative)
            entry = dict(backup=f'{index:05d}.backup', before_sha256=None, state='prepared')
            if file['destination']:
                backup = directory / entry['backup']
                with binary_connection(connection): connection.transfer(backup, dst, False, worker.cancel, lambda *_: None)
                entry['before_sha256'] = digest(None, backup, 'SHA-256', worker.cancel)
                import os
                with backup.open('r+b') as saved: os.fsync(saved.fileno())
                if lookup(connection, plan['profile']['destination'], relative) != file['destination']:
                    raise PreviewChanged('Destination changed while backing up: ' + relative)
                if remote_digest(connection, dst, worker.cancel) != entry['before_sha256']:
                    raise PreviewChanged('Backup does not match the current destination: ' + relative)
            entry['state'] = 'writing'
            journal['entries'][relative] = entry
            atomic_json(manifest, journal)  # Backup and intent are durable before mutation.
            publish(None, connection, src, dst, False, file)
            entry['state'] = 'applied'
            atomic_json(manifest, journal)
            worker.report(index+1, len(plan['files']))
        journal['state'] = 'complete'
        atomic_json(manifest, journal)
        return dict(transferred=True, messages=[f'Deployment complete. Rollback revision: {plan["id"]}'])
    except BaseException:
        journal['state'] = 'interrupted'
        atomic_json(manifest, journal)
        raise


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
