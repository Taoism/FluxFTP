"""Reviewed local-to-remote deployments with durable backups and guarded rollback."""
from .queue_metadata import validate_profile, validate_job
from dataclasses import asdict, replace
import time
from pathlib import Path

from .core import Cancelled
from .folder_sync import scan, lookup, joined, execute_sync, PreviewChanged
from .sync_profiles import atomic_json
from .transfer_reliability import digest
from .watch_upload import excluded


TEXT_SNAPSHOT_EXTENSIONS = frozenset({'.astro', '.cjs', '.cfg', '.conf', '.css', '.env.example', '.gql',
    '.graphql', '.html', '.htm', '.ini', '.js', '.json', '.jsx', '.less', '.md', '.mdx', '.mjs',
    '.php', '.properties', '.py', '.rst', '.sass', '.scss', '.sh', '.sql', '.svelte', '.toml', '.ts',
    '.tsx', '.txt', '.vue', '.xml', '.yaml', '.yml'})
MAX_TEXT_SNAPSHOT_FILE = 256 * 1024
MAX_TEXT_SNAPSHOT_TOTAL = 8 * 1024 * 1024


def _text_snapshot_candidate(relative, size):
    if size < 0 or size > MAX_TEXT_SNAPSHOT_FILE:
        return False
    name = Path(relative).name.casefold()
    return (not Path(name).suffix or Path(name).suffix in TEXT_SNAPSHOT_EXTENSIONS or name.endswith('.env.example') or
            name in {'.htaccess', 'dockerfile', 'makefile'})




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


def execute(window, connection, job, worker):
    plan = validate_job(job['deployment'])
    directory = revision_path(window, plan['id'])
    if plan['rollback']: return rollback(window, connection, job, worker)
    if directory.exists():
        raise PreviewChanged('This deployment revision already started. Inspect its rollback preview or create a fresh deployment preview.')
    directory.mkdir(parents=True)
    journal = dict(version=1, created=time.time(), state='running', plan=plan,
                   site=asdict(connection.site), entries={}, source_text={})
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
            if _text_snapshot_candidate(relative, file['source']['size']):
                current_bytes = Path(src).read_bytes()
                existing_total = sum(len(value.encode('utf-8')) for value in journal['source_text'].values())
                if (len(current_bytes) == file['source']['size'] and
                        digest(None, src, 'SHA-256', worker.cancel) == file['source_sha256'] and
                        existing_total + len(current_bytes) <= MAX_TEXT_SNAPSHOT_TOTAL):
                    try:
                        journal['source_text'][relative] = current_bytes.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        pass
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




from .deployment_rollback import (
    binary_connection, remote_digest, revision_path, read_revision, site_identity,
    rollback_preview, rollback,
)
