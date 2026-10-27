"""Bounded, cancellable recursive automation with explicit conflict policies."""
import os
import posixpath
from pathlib import Path
from .core import Cancelled, safe_name


def check(cancel):
    if cancel.is_set():
        raise Cancelled('Folder transfer cancelled; completed files are retained.')


def local_safe(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Automation paths cannot contain symbolic links or junctions.')


def remote_directory_safe(connection, path, cancel, allow_missing_leaf=False):
    from .automation import list_folder
    parts = [part for part in path.split('/') if part]
    current = '/'
    for index, part in enumerate(parts):
        safe_name(part)
        entries = list_folder(connection, current, cancel)
        entry = next((e for e in entries if e.name == part), None)
        if entry is None and allow_missing_leaf and index == len(parts) - 1:
            return
        if entry is None or not entry.is_dir or entry.is_link:
            raise ValueError('Remote folder path is missing or contains a file/link: ' + part)
        current = posixpath.join(current, part)


def transfer_folder(connection, step, worker, report):
    from .automation import list_folder
    upload = step['action'] == 'Upload folder'
    root = Path(step['local'])
    local_safe(root)
    remote_directory_safe(connection, step['remote'], worker.cancel, allow_missing_leaf=upload)
    if upload and not root.is_dir():
        raise ValueError('Upload source folder does not exist.')
    if not upload:
        parent = list_folder(connection, posixpath.dirname(step['remote']), worker.cancel) if step['remote'] != '/' else None
        entry = next((e for e in parent if e.name == posixpath.basename(step['remote'])), None) if parent is not None else None
        if parent is not None and (not entry or not entry.is_dir or entry.is_link):
            raise ValueError('Download source must be an existing regular directory.')
        root.mkdir(parents=True, exist_ok=True)
    pending = [(root, step['remote'], 0)]
    count = 0
    while pending:
        check(worker.cancel)
        local, remote, depth = pending.pop()
        if depth > 128:
            raise ValueError('Folder nesting exceeds 128 levels.')
        local_safe(local)
        if upload:
            # mkdir implementations already handle an existing directory.
            if remote != '/':
                parent = list_folder(connection, posixpath.dirname(remote), worker.cancel)
                existing = next((e for e in parent if e.name == posixpath.basename(remote)), None)
                if existing and (not existing.is_dir or existing.is_link):
                    raise ValueError('Remote destination is not a regular directory.')
                if not existing:
                    connection.mkdir(remote)
            targets = {e.name: e for e in list_folder(connection, remote, worker.cancel)}
            with os.scandir(local) as listing:
                entries = list(listing)
        else:
            local.mkdir(exist_ok=True)
            entries = list_folder(connection, remote, worker.cancel)
        for entry in entries:
            check(worker.cancel)
            count += 1
            if count > 1_000_000:
                raise ValueError('Folder transfer exceeds one million entries.')
            name = safe_name(entry.name)
            child = local / name
            target = posixpath.join(remote, name)
            local_safe(child)
            link = entry.is_symlink() if upload else entry.is_link
            directory = entry.is_dir(follow_symlinks=False) if upload else entry.is_dir
            if link:
                raise ValueError('Folder transfer refuses symbolic links: ' + name)
            if directory:
                pending.append((child, target, depth + 1))
                continue
            if upload and not entry.is_file(follow_symlinks=False):
                raise ValueError('Upload source is not a regular file: ' + name)
            existing = targets.get(name) if upload else child.exists()
            if upload and existing and (existing.is_dir or existing.is_link):
                raise ValueError('Destination is not a regular file: ' + name)
            if not upload and existing and not child.is_file():
                raise ValueError('Destination is not a regular file: ' + name)
            if existing:
                if step['exists'] == 'Fail':
                    raise ValueError('Destination already exists: ' + name)
                if step['exists'] == 'Skip':
                    report('Skipped existing file: ' + name)
                    continue
            connection.transfer(child, target, upload, worker.cancel, worker.report, resume=False)
            report('Transferred: ' + name)
    return count
