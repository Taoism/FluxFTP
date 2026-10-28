"""Read-only synchronization plans and guarded execution through the queue."""
import os
import posixpath
import math
import ftplib
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import replace

from .core import Cancelled, safe_name, transfer_tree

SCAN_LIMIT = 10000


class PreviewChanged(ValueError):
    """Retrying unchanged metadata cannot repair an obsolete approval."""


def check_roots(source_site, destination_site, source_root, destination_root):
    def identity(site):
        return (site.host.casefold(), site.port, site.username.casefold(), site.protocol, site.cloud_account) if site else None
    if identity(source_site) != identity(destination_site): return
    if source_site:
        a, b = posixpath.normpath(source_root), posixpath.normpath(destination_root)
        overlap = a == b or a.startswith(b.rstrip('/') + '/') or b.startswith(a.rstrip('/') + '/')
    else:
        a, b = Path(source_root).resolve(), Path(destination_root).resolve()
        overlap = a == b or a.is_relative_to(b) or b.is_relative_to(a)
    if overlap:
        raise ValueError('Synchronization folders must be separate and cannot contain one another.')


def joined(root, relative, remote):
    return posixpath.join(root, relative) if remote else str(Path(root).joinpath(*relative.split('/')))


def stamp(value):
    """Only timestamps with a known UTC interpretation can select 'newer'."""
    try:
        value = str(value)
        if value.replace('.', '', 1).isdigit() and len(value.split('.')[0]) <= 10:
            result = float(value)
        elif len(value.split('.')[0].rstrip('Z')) == 14 and value[:14].isdigit():
            result = datetime.strptime(value.rstrip('Z'), '%Y%m%d%H%M%S.%f' if '.' in value else '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp()
        else:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                return None
            result = parsed.timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError, OSError):
        return None


def fingerprint(entry):
    return dict(directory=entry.is_dir, link=entry.is_link, size=0 if entry.is_dir else entry.size,
                modified='' if entry.is_dir else str(entry.modified))


def local_fingerprint(path):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        return dict(directory=False, link=True, size=0, modified='')
    st = path.stat()
    return dict(directory=path.is_dir(), link=False, size=0 if path.is_dir() else st.st_size,
                modified='' if path.is_dir() else str(st.st_mtime_ns / 1e9))


def check_remote_root(connection, root):
    if not root.startswith('/') or '..' in root.split('/'):
        raise ValueError('Choose an absolute remote folder.')
    folder = '/'
    for name in root.strip('/').split('/') if root.strip('/') else []:
        matches = [e for e in connection.list(folder) if e.name == name]
        if len(matches) != 1 or not matches[0].is_dir or matches[0].is_link:
            raise ValueError('Remote synchronization root must contain only regular folders.')
        folder = posixpath.join(folder, name)


def scan(connection, root, cancel, limit=SCAN_LIMIT, hash_algorithm=None, exclude=None):
    if connection is None:
        path = Path(root)
        if not path.is_absolute() or not path.is_dir() or local_fingerprint(path)['link']:
            raise ValueError('Choose an existing local folder without symbolic links or junctions.')
        # Also reject links in the path leading to the selected root.
        for parent in path.parents:
            if local_fingerprint(parent)['link']:
                raise ValueError('Folder paths cannot pass through symbolic links or junctions.')
    else:
        check_remote_root(connection, root)
    result = {}
    pending = [('', 0)]
    while pending:
        if cancel.is_set(): raise Cancelled('Synchronization scan cancelled')
        relative, depth = pending.pop()
        if depth > 100: raise ValueError('Synchronization scan exceeds 100 folder levels.')
        path = joined(root, relative, connection is not None)
        if connection:
            entries = [(entry.name, fingerprint(entry)) for entry in connection.list(path)]
        else:
            entries = [(entry.name, local_fingerprint(entry)) for entry in Path(path).iterdir()]
        for name, info in entries:
            if cancel.is_set(): raise Cancelled('Synchronization scan cancelled')
            safe_name(name)
            key = posixpath.join(relative, name)
            if exclude and exclude(key): continue
            if key in result: raise ValueError('Duplicate directory entry: ' + key)
            result[key] = info
            if len(result) > limit:
                raise ValueError(f'Synchronization preview exceeds {limit:,} entries. Choose a smaller folder.')
            if info['directory'] and not info['link']:
                pending.append((key, depth + 1))
    if hash_algorithm:
        from .transfer_reliability import digest
        for relative, info in result.items():
            if info['directory'] or info['link']: continue
            info['hash_algorithm'] = hash_algorithm
            info['digest'] = digest(connection, joined(root, relative, connection is not None), hash_algorithm, cancel)
            current = lookup(connection, root, relative)
            if current != {k: info[k] for k in ('directory', 'link', 'size', 'modified')}:
                raise PreviewChanged('File changed while computing checksums; scan again.')
    return result


def compare(left, right, mode='Left → Right', rule='Size and time', tolerance=2):
    """Every existing-file difference is an explicit conflict, never implicit overwrite."""
    rows = []
    blocked = set()
    # Portable collision checking avoids ambiguous writes to Windows destinations.
    names = {}
    collisions = set()
    for key in set(left) | set(right):
        folded = key.casefold()
        if folded in names and names[folded] != key:
            collisions.update((key, names[folded]))
        names[folded] = key
    for key in sorted(set(left) | set(right), key=lambda k: (k.count('/'), k.casefold(), k)):
        a, b = left.get(key), right.get(key)
        action, reason, choices = 'Skip', '', ['Skip']
        ancestors = [str(p) for p in Path(key).parents if str(p) != '.']
        if key in collisions or any(p.replace('\\', '/') in blocked for p in ancestors):
            reason = 'Blocked: name collision or parent conflict'
            blocked.add(key)
        elif any(info and info['link'] for info in (a, b)):
            reason = 'Blocked: symbolic link or junction'
            blocked.add(key)
        elif a and b and a['directory'] != b['directory']:
            reason = 'Blocked: file/folder type conflict'
            blocked.add(key)
        elif a and b and a['directory']:
            reason = 'Same folder'
        elif a and b:
            ta, tb = stamp(a['modified']), stamp(b['modified'])
            same = a['size'] == b['size'] and ((bool(a.get('digest')) and a.get('hash_algorithm') == b.get('hash_algorithm') and a['digest'] == b.get('digest'))
                if rule == 'Checksum' else (rule == 'Size only' or
                    (ta is not None and tb is not None and abs(ta - tb) <= tolerance)))
            if same:
                reason = 'Same by ' + rule.lower()
            else:
                reason = 'Conflict: different files' if ta is not None and tb is not None else 'Conflict: unknown timestamp'
                choices += ['Left → Right', 'Right → Left'] if mode == 'Two-way' else [mode]
        elif a:
            reason = 'Only on left'
            if mode != 'Right → Left': action = 'Left → Right'; choices.append(action)
        else:
            reason = 'Only on right'
            if mode != 'Left → Right': action = 'Right → Left'; choices.append(action)
        rows.append(dict(path=key, left=a, right=b, action=action, reason=reason, choices=choices))
    return rows


from .queue_metadata import validate_spec


def lookup(connection, root, relative):
    """Verify every ancestor; missing parents mean the leaf is absent."""
    parts = relative.split('/')
    if connection is None:
        root_path = Path(root)
        if not root_path.is_absolute(): raise ValueError('Local synchronization root must be absolute.')
        for parent in (root_path, *root_path.parents):
            if local_fingerprint(parent)['link']:
                raise ValueError('Synchronization path now contains a link or junction.')
    else:
        check_remote_root(connection, root)
    folder = root
    for index, name in enumerate(parts):
        if connection:
            matches = [e for e in connection.list(folder) if e.name == name]
            if len(matches) > 1: raise ValueError('Duplicate remote entry: ' + name)
            info = fingerprint(matches[0]) if matches else None
        else:
            path = Path(folder) / name
            info = local_fingerprint(path) if path.exists() or path.is_symlink() else None
        if info is None: return None
        if info['link']: raise ValueError('Synchronization path now contains a link or junction.')
        if index == len(parts) - 1: return info
        if not info['directory']: raise ValueError('Synchronization parent is no longer a folder.')
        folder = joined(folder, name, connection is not None)


def ensure_folders(connection, root, relative):
    current = root
    for name in relative.split('/') if relative else []:
        info = lookup(connection, current, name)
        path = joined(current, name, connection is not None)
        if info is None:
            try:
                if connection: connection.mkdir(path)
                else: Path(path).mkdir()
            except Exception:
                # A parallel transfer may have created the same parent.
                info = lookup(connection, current, name)
                if not info or not info['directory'] or info['link']: raise
        elif not info['directory'] or info['link']:
            raise ValueError('Synchronization destination parent is not a regular folder.')
        current = path


def execute_sync(source, destination, job, worker):
    spec = validate_spec(job['sync'])
    check_roots(job.get('source_site'), job.get('destination_site'), spec['source_root'], spec['destination_root'])
    deleting = spec.get('operation') == 'delete'
    if job['directory'] != (False if deleting else spec['source']['directory']):
        raise ValueError('Synchronization file type no longer matches the preview.')
    if worker.cancel.is_set(): raise Cancelled('Synchronization cancelled')
    for key, connection, field in (('source', source, 'src'), ('destination', destination, 'dst')):
        path = joined(spec[key + '_root'], spec['relative'], connection is not None)
        expected = job[field]
        if (posixpath.normpath(path) != posixpath.normpath(expected) if connection else
                os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(expected))):
            raise PreviewChanged('Synchronization paths no longer match the approved preview.')
        current = lookup(connection, spec[key + '_root'], spec['relative'])
        if current and spec[key] and 'digest' in spec[key]:
            from .transfer_reliability import digest
            current['hash_algorithm'] = spec[key]['hash_algorithm']
            current['digest'] = digest(connection, path, current['hash_algorithm'], worker.cancel)
        if current != spec[key]:
            # Creating a missing folder twice is harmless when child jobs race.
            if not (key == 'destination' and job['directory'] and spec[key] is None and current and current['directory'] and not current['link']):
                raise PreviewChanged(f'{key.title()} changed since synchronization preview; preview again.')
    if worker.cancel.is_set(): raise Cancelled('Synchronization cancelled')
    if deleting:
        if destination: destination.remove(job['dst'], False)
        else: Path(job['dst']).unlink()
        return dict(transferred=True, messages=['Deleted destination-only file from the reviewed mirror plan.'])
    relative = spec['relative'] if job['directory'] else posixpath.dirname(spec['relative'])
    ensure_folders(destination, spec['destination_root'], relative)
    if job['directory']:
        return dict(transferred=True)
    # The approved preview determines paths and overwrite policy. Avoid resume,
    # renames and cleanup side effects that would change that plan.
    options = replace(job['options'], exists_enabled=False, skip_enabled=False,
        single_sfv=False, skip_multiple_sfv=False, remove_missing_zero=False, mode='Binary', keep_file_time=True)
    for connection in (source, destination):
        if connection: connection.transfer_options = options
    messages = []
    transferred = transfer_tree(source, destination, job['src'], job['dst'], False,
        worker.cancel, worker.report, resume=False, options=options, notice=messages.append)
    modified = stamp(spec['source']['modified'])
    if destination and modified is not None:
        try:
            destination.set_modified(job['dst'], modified)
        except (OSError, ValueError, ftplib.Error) as error:
            messages.append(f'File copied; destination timestamp could not be preserved: {error}')
    return dict(transferred=transferred, messages=messages)
