"""Bounded remote folder manifests used for trading readiness checks."""
import hashlib
import json
import posixpath

from .core import Cancelled, safe_name


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled('Auto-trading scan cancelled.')


def folder_manifest(connection, folder_path, *, cancel=None, max_depth=64, max_entries=100_000):
    """Return a deterministic recursive metadata manifest and digest.

    Symlinks and unsupported/colliding names are rejected rather than followed.
    The function is read-only and enforces depth and entry-count bounds.
    """
    if not isinstance(folder_path, str) or not folder_path.startswith('/') or '..' in folder_path.split('/'):
        raise ValueError('Source folder must be an absolute remote path without parent segments.')
    if type(max_depth) is not int or not 1 <= max_depth <= 128:
        raise ValueError('max_depth must be between 1 and 128.')
    if type(max_entries) is not int or not 1 <= max_entries <= 1_000_000:
        raise ValueError('max_entries must be between 1 and one million.')
    pending = [(folder_path.rstrip('/') or '/', '', 0)]
    manifest = []
    seen_paths = set()
    while pending:
        _check_cancel(cancel)
        remote, relative, depth = pending.pop()
        entries = connection.list(remote, cancel=cancel) if getattr(connection, 'is_cloud', False) else connection.list(remote)
        seen_names = set()
        for entry in entries:
            _check_cancel(cancel)
            name = safe_name(entry.name)
            folded = name.casefold()
            if folded in seen_names:
                raise ValueError(f'Directory listing contains ambiguous duplicate names: {name}')
            seen_names.add(folded)
            rel = posixpath.join(relative, name) if relative else name
            if rel in seen_paths:
                raise ValueError('Directory listing contains a duplicate path.')
            seen_paths.add(rel)
            if len(seen_paths) > max_entries:
                raise ValueError(f'Folder exceeds the configured {max_entries:,}-entry scan limit.')
            if getattr(entry, 'is_link', False):
                raise ValueError(f'Folder contains a symbolic link: {rel}')
            is_dir = bool(entry.is_dir)
            modified = str(getattr(entry, 'modified', '') or '')[:128]
            size = int(getattr(entry, 'size', 0) or 0)
            if size < 0:
                raise ValueError(f'Folder contains an invalid size: {rel}')
            manifest.append((rel, 'dir' if is_dir else 'file', size, modified))
            if is_dir:
                if depth >= max_depth:
                    raise ValueError(f'Folder exceeds the configured {max_depth}-level depth limit.')
                pending.append((posixpath.join(remote, name), rel, depth + 1))
    manifest.sort(key=lambda item: (item[0].casefold(), item[0]))
    payload = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return manifest, hashlib.sha256(payload).hexdigest()


def completion_marker_present(manifest, marker):
    """Check for a direct child completion marker in a captured manifest."""
    return not marker or any(path == marker and kind == 'file' for path, kind, _size, _modified in manifest)
