"""Bounded recursive inspection and same-server operations for the FTP menu."""
import posixpath
from .core import Cancelled, safe_name, remote_path


def scan_directory(connection, root, cancel, limit=100000):
    root = remote_path(root)
    pending = [(root, 0)]
    seen = set()
    result = []
    while pending:
        if cancel.is_set():
            raise Cancelled('Directory scan cancelled')
        folder, depth = pending.pop()
        if folder in seen:
            continue
        if depth > 128:
            raise ValueError('Directory scan exceeded 128 levels.')
        seen.add(folder)
        entries = sorted(connection.list(folder), key=lambda entry: (not entry.is_dir, entry.name.casefold()))
        for entry in entries:
            if cancel.is_set():
                raise Cancelled('Directory scan cancelled')
            safe_name(entry.name)
            path = posixpath.join(folder, entry.name)
            result.append((path, entry))
            if len(result) > limit:
                raise ValueError(f'Directory scan exceeded {limit:,} entries; choose a smaller folder.')
            if entry.is_dir and not entry.is_link:
                pending.append((path, depth + 1))
    return result


def move_items(connection, source, entries, destination, cancel):
    source = posixpath.normpath(remote_path(source))
    destination = posixpath.normpath(remote_path(destination))
    if not destination.startswith('/'):
        raise ValueError('Enter an absolute destination directory.')
    if source == destination:
        raise ValueError('Choose a different destination directory.')
    targets = {entry.name.casefold() for entry in connection.list(destination)}
    for entry in entries:
        safe_name(entry.name)
        original = posixpath.join(source, entry.name)
        if entry.name.casefold() in targets:
            raise ValueError(f'Destination already contains {entry.name}; nothing was moved.')
        if entry.is_dir and (destination == original or destination.startswith(original.rstrip('/') + '/')):
            raise ValueError('Cannot move a directory into itself.')
        targets.add(entry.name.casefold())
    completed = 0
    for entry in entries:
        if cancel.is_set():
            raise Cancelled(f'Move cancelled after {completed} item(s).')
        try:
            connection.rename(posixpath.join(source, entry.name), posixpath.join(destination, entry.name))
        except Exception as error:
            raise RuntimeError(f'{completed} item(s) moved before failure: {error}') from error
        completed += 1
    return completed
