"""File conflict decisions shared by upload, download and FXP jobs."""
import ftplib
import posixpath
import shutil
import threading
import hashlib
from dataclasses import replace
from pathlib import Path

_lock = threading.Lock()
_active = set()


def _local_prefix_hash(path, length, cancel):
    digest = hashlib.sha256()
    remaining = length
    with Path(path).open('rb') as stream:
        while remaining:
            if cancel.is_set():
                from .core import Cancelled
                raise Cancelled('Cancelled while checking the existing destination')
            block = stream.read(min(1024 * 1024, remaining))
            if not block: return None
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def _prefix_hash(connection, path, length, cancel):
    if connection is None:
        return _local_prefix_hash(path, length, cancel)
    import hashlib
    value, remaining = hashlib.sha256(), length
    if getattr(connection, 'sftp', None):
        with connection.sftp.open(path, 'rb') as stream:
            while remaining:
                if cancel.is_set():
                    from .core import Cancelled
                    raise Cancelled('Cancelled while checking the existing destination')
                block = stream.read(min(1024 * 1024, remaining))
                if not block: return None
                value.update(block)
                remaining -= len(block)
        return value.hexdigest()
    if getattr(connection, 'ftp', None):
        from .core import remote_path, Cancelled
        connection.ftp.voidcmd('TYPE I')
        cancelled = [False]
        def consume(block):
            nonlocal remaining
            if cancel.is_set():
                cancelled[0] = True
                return
            if remaining:
                block = block[:remaining]
                value.update(block)
                remaining -= len(block)
        connection.ftp.retrbinary('RETR ' + remote_path(path), consume, 65536)
        if cancelled[0]: raise Cancelled('Cancelled while checking the existing destination')
        return value.hexdigest() if remaining == 0 else None
    return None


def _match_status(source, destination, src, dst, source_size, target_size, cancel):
    """Compare content when possible; report metadata-only evidence explicitly."""
    from .transfer_reliability import algorithms, digest
    if target_size > source_size:
        return 'Destination is larger than source', False
    if target_size < source_size:
        try:
            left = _prefix_hash(source, src, target_size, cancel)
            right = _prefix_hash(destination, dst, target_size, cancel)
            if left is None or right is None:
                return 'Could not read the complete destination', False
            return ('Content matches source prefix', True) if left == right else ('Content differs from source prefix', False)
        except Exception:
            if cancel.is_set():
                from .core import Cancelled
                raise Cancelled('Cancelled while checking the existing destination')
            return 'Content could not be checked', False
    if target_size == source_size:
        try:
            common = algorithms(source) & algorithms(destination)
            for algorithm in ('SHA-256', 'SHA-1', 'MD5'):
                if algorithm not in common: continue
                same = digest(source, src, algorithm, cancel) == digest(destination, dst, algorithm, cancel)
                return (f'Content matches ({algorithm})', True) if same else (f'Content differs ({algorithm})', False)
        except Exception:
            if cancel.is_set():
                from .core import Cancelled
                raise Cancelled('Cancelled while checking the existing destination')
        if source is None and destination is None:
            try:
                source_stat, target_stat = Path(src).stat(), Path(dst).stat()
                if source_stat.st_size == target_stat.st_size and source_stat.st_mtime_ns == target_stat.st_mtime_ns:
                    return 'Size and modification time match (fallback)', True
            except OSError:
                pass
    return 'Not verified; destination backend cannot compare this prefix', False


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
        source_fingerprint = Path(src).stat() if source is None else None
        needs_match = options.conflict_prompt or action == 'Ask' or action == 'Auto Resume'
        if needs_match:
            match_status, verified_match = _match_status(source, destination, src, dst, source_size,
                                                         target_size, cancel)
        else:
            match_status, verified_match = 'Not checked', False
        if (options.conflict_prompt or action == 'Ask' or (action == 'Auto Resume' and not verified_match)):
            if conflict is None:
                raise ValueError('The existing destination was not verified for resume; choose a File Exists action from the queue.')
            from .protocol_adapter import supports_resume
            action = conflict(dict(src=src, dst=dst, source_size=source_size, target_size=target_size,
                can_resume=target_size < source_size and not (destination and options.atomic_upload)
                and verified_match
                and not options.ascii(src) and supports_resume(source, destination), match_status=match_status))
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
        if action == 'Auto Resume':
            if destination:
                fresh = next((e for e in destination.list(parent or '/') if e.name == name), None)
            else:
                fresh = Path(dst).stat() if Path(dst).exists() and not Path(dst).is_symlink() else None
            if fresh != fingerprint:
                from .transfer_reliability import ReliabilityError
                raise ReliabilityError('Destination changed after its content was checked; resume was stopped.')
            if source_fingerprint is not None and Path(src).stat() != source_fingerprint:
                from .transfer_reliability import ReliabilityError
                raise ReliabilityError('Source changed after its content was checked; resume was stopped.')
            if source is not None:
                current_status, current_match = _match_status(source, destination, src, dst, source_size,
                                                             target_size, cancel)
                if not current_match:
                    from .transfer_reliability import ReliabilityError
                    raise ReliabilityError('Source or destination content changed after it was checked; resume was stopped. ' + current_status)
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
