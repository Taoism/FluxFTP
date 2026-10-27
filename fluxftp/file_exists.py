"""File conflict decisions shared by upload, download and FXP jobs."""
import ftplib
import posixpath
import shutil
import threading
from dataclasses import replace
from pathlib import Path

_lock = threading.Lock()
_active = set()


def transfer_existing(source, destination, src, dst, cancel, progress, resume, options, skipped, renamed, conflict=None, notice=None):
    from .core import transfer_tree, Cancelled
    route = 'fxp' if source and destination else 'upload' if destination else 'download'
    if destination:
        parent, name = posixpath.split(dst)
        entries = {entry.name: entry for entry in destination.list(parent or '/')}
        entry = entries.get(name)
        fingerprint = entry
        if entry and (entry.is_dir or entry.is_link):
            raise ValueError('Destination must be a regular file.')
        target_size = destination.size(dst) if entry else None
        if entry and target_size is None:
            raise ValueError('Cannot compare destination size for File Exists rules.')
        namespace = (destination.site.host.casefold(), destination.site.port, destination.site.username)
        def key(path): return namespace, path
        def occupied(path): return posixpath.basename(path) in entries or key(path) in _active
    else:
        target = Path(dst)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('Destination must be a regular file.')
        parent, name = str(target.parent), target.name
        target_size = target.stat().st_size if target.exists() else None
        fingerprint = target.stat() if target.exists() else None
        def key(path): return ('local', str(Path(path).resolve()).casefold())
        def occupied(path):
            p = Path(path)
            return p.exists() or p.is_symlink() or p.with_name(p.name + '.fluxpart').exists() or key(path) in _active
    action = None
    if target_size is not None:
        source_size = source.size(src) if source else Path(src).stat().st_size
        if source_size is None:
            raise ValueError('Cannot compare file sizes for File Exists rules.')
        relation = 'smaller' if target_size < source_size else 'larger' if target_size > source_size else 'same'
        action = getattr(options, f'{route}_{relation}')
        if options.conflict_prompt or action == 'Ask':
            if conflict is None:
                raise ValueError('This transfer requires a File Exists choice; start it from the queue.')
            action = conflict(dict(src=src, dst=dst, source_size=source_size, target_size=target_size,
                can_resume=target_size < source_size and not (destination and (options.atomic_upload or getattr(destination, 'is_cloud', False))) and not options.ascii(src)))
            if cancel.is_set(): raise Cancelled('Cancelled')
            if action not in ('Auto Resume', 'Auto Skip', 'Auto Rename', 'Auto Overwrite'):
                raise Cancelled('File conflict cancelled')
            if destination:
                fresh = next((e for e in destination.list(parent or '/') if e.name == name), None)
            else:
                fresh = Path(dst).stat() if Path(dst).exists() and not Path(dst).is_symlink() else None
            if fresh != fingerprint:
                from .transfer_reliability import ReliabilityError
                raise ReliabilityError('Destination changed while waiting for the conflict choice.')
        if action == 'Auto Skip':
            if source is None and destination and relation == 'same' and hasattr(destination, 'verify_skip'):
                destination.verify_skip(src, dst, cancel)
            if skipped: skipped(src, 'File Exists: target is ' + relation)
            return False
    original = dst
    with _lock:
        if action == 'Auto Rename':
            if destination:
                # Refresh after waiting for the reservation lock: another rename
                # may have finished since the initial comparison listing.
                entries = {entry.name: entry for entry in destination.list(parent or '/')}
            stem, suffix = posixpath.splitext(name)
            for number in range(1, 10001):
                candidate = f'{stem} ({number}){suffix}'
                dst = posixpath.join(parent, candidate) if destination else str(Path(parent) / candidate)
                if not occupied(dst): break
            else:
                raise ValueError('No unused numbered destination name is available.')
        if key(dst) in _active:
            raise ValueError('Another transfer is already writing this destination.')
        reservation = key(dst)
        _active.add(reservation)
    try:
        if dst != original and renamed: renamed(original, dst)
        if action:
            resume = action == 'Auto Resume'
        inner = replace(options, exists_enabled=False, skip_enabled=False)
        if resume and action == 'Auto Resume' and not destination:
            # Keep the visible destination intact until the normal partial-file replace.
            target = Path(dst)
            partial = target.with_name(target.name + '.fluxpart')
            if partial.is_symlink():
                raise ValueError('Partial download cannot be a symbolic link')
            options.check_space(partial, target.stat().st_size)
            shutil.copyfile(target, partial)
        def run(continuing):
            return transfer_tree(source, destination, src, dst, False, cancel, progress,
                                 continuing, options=inner, skipped=skipped, renamed=renamed, notice=notice)
        try:
            return run(resume)
        except (ftplib.Error, OSError, ValueError):
            if cancel.is_set(): raise Cancelled('Cancelled')
            if not resume or not options.overwrite_resume_failed:
                raise
            # Discard pending FTP replies and REST state before one clean overwrite.
            for connection in (source, destination):
                if connection:
                    connection.trace.event('Resume failed; retrying once with overwrite')
                    connection.close()
                    connection.connect()
            return run(False)
    finally:
        with _lock:
            _active.discard(reservation)
