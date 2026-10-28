"""Bounded checksum verification and staged destination publication."""
import ftplib
import hashlib
import posixpath
import re
import uuid
from dataclasses import replace
from pathlib import Path


class ReliabilityError(Exception):
    """Do not turn a failed verification/promotion into an overwrite retry."""


class UnsupportedHash(ReliabilityError):
    pass


ALGORITHMS = {'SHA-256': 'sha256', 'SHA-1': 'sha1', 'MD5': 'md5'}


def checked(cancel):
    if cancel.is_set():
        from .core import Cancelled
        raise Cancelled('Cancelled')


def algorithms(connection):
    if connection is None: return set(ALGORITHMS)
    from .protocol_adapter import adapter_for
    return adapter_for(connection).hash_algorithms()


def _backend_algorithms(connection):
    if connection is None:
        return set(ALGORITHMS)
    if getattr(connection, 'is_cloud', False):
        return set()
    if getattr(connection, 'sftp', None):
        return {'SHA-1', 'MD5'}  # check-file algorithms; extension may be absent.
    cached = getattr(connection, '_hash_algorithms', None)
    if cached is not None:
        return cached
    try:
        response = connection.ftp.sendcmd('FEAT')
    except ftplib.error_perm as error:
        if str(error)[:3] not in ('500', '502', '504'):
            raise
        response = ''
    result = set()
    for line in response.splitlines():
        match = re.fullmatch(r'\s*HASH\s+(.+?)\s*', line, re.I)
        if match:
            result.update(token.strip().rstrip('*').upper() for token in match[1].split(';'))
    connection._hash_algorithms = result & set(ALGORITHMS)
    return connection._hash_algorithms


def digest(connection, path, algorithm, cancel):
    try:
        if connection is None: return _digest(connection, path, algorithm, cancel)
        from .protocol_adapter import adapter_for
        return adapter_for(connection).digest(path, algorithm, cancel)
    except (OSError, ftplib.Error, ValueError) as error:
        raise ReliabilityError('Hash verification failed: ' + str(error)) from error


def _digest(connection, path, algorithm, cancel):
    checked(cancel)
    if connection is None:
        value = hashlib.new(ALGORITHMS[algorithm])
        with Path(path).open('rb') as stream:
            while block := stream.read(1024 * 1024):
                checked(cancel)
                value.update(block)
        return value.hexdigest()
    if getattr(connection, 'sftp', None):
        try:
            with connection.sftp.open(path, 'rb') as stream:
                value = stream.check(ALGORITHMS[algorithm], 0, 0, 0).hex()
        except OSError as error:
            # Paramiko returns a generic IOError for SSH_FX_OP_UNSUPPORTED.
            if 'unsupported' in str(error).lower() or 'not supported' in str(error).lower() or 'no supported hash' in str(error).lower():
                raise UnsupportedHash('SFTP server does not support check-file ' + algorithm) from error
            raise
    else:
        from .core import remote_path
        try:
            if hasattr(connection.ftp, 'set_compression'):
                connection.ftp.set_compression(False)
            connection.ftp.voidcmd('TYPE I')
            connection.ftp.sendcmd('OPTS HASH ' + algorithm)
            response = connection.ftp.sendcmd('HASH ' + remote_path(path))
        except ftplib.error_perm as error:
            if str(error)[:3] in ('500', '501', '502', '504'):
                raise UnsupportedHash('FTP server does not support HASH ' + algorithm) from error
            raise
        # ftplib retains preliminary lines of a multiline 213 response.
        parts = response.splitlines()[-1].split() if response else []
        if len(parts) < 3 or parts[0] != '213' or parts[1].upper() != algorithm:
            raise ReliabilityError('Invalid FTP HASH response or algorithm')
        if re.fullmatch(r'\d+-\d+', parts[2]):
            size = connection.size(path)
            if size is None or parts[2] != f'0-{max(0, size - 1)}' or len(parts) < 4:
                raise ReliabilityError('FTP HASH did not cover the complete file')
            value = parts[3]
        else:
            value = parts[2]
    length = hashlib.new(ALGORITHMS[algorithm]).digest_size * 2
    if not re.fullmatch('[0-9a-fA-F]{' + str(length) + '}', value):
        raise ReliabilityError('Invalid ' + algorithm + ' checksum')
    checked(cancel)
    return value.lower()


def transfer(source, destination, src, dst, cancel, progress, resume, options, notice=None):
    from .verification_reports import evidence
    from .core import transfer_tree
    def note(message):
        if notice:
            notice(message)
            if destination or source:
                (destination or source).trace.event(message)
        elif destination or source:
            (destination or source).note(message)
    atomic = bool(destination and options.atomic_upload)
    if source and destination and (source.site.host.casefold(), source.site.port, source.site.username, posixpath.normpath(src)) == (destination.site.host.casefold(), destination.site.port, destination.site.username, posixpath.normpath(dst)):
        raise ReliabilityError('Source and destination are the same file.')
    if atomic and getattr(destination, 'is_cloud', False):
        raise ReliabilityError('Atomic upload is unavailable for cloud destinations; disable it for this transfer.')
    if atomic:
        existing = [entry for entry in destination.list(posixpath.dirname(dst) or '/') if entry.name == posixpath.basename(dst)]
        if any(entry.is_dir or entry.is_link for entry in existing):
            raise ReliabilityError('Atomic destination must be a regular file.')
    policy = options.hash_verification
    candidates = []
    if policy != 'Off':
        if (source or destination) and options.ascii(src):
            if policy == 'Required':
                raise ReliabilityError('Hash verification requires Binary transfer mode.')
            note('Hash not verified: ASCII transfer changes file bytes.')
        else:
            common = algorithms(source) & algorithms(destination)
            candidates = [name for name in ALGORITHMS if name in common]
            if not candidates:
                if policy == 'Required':
                    raise UnsupportedHash('No common server hash algorithm; transfer was not started.')
                note('Hash not verified: no common supported server hash algorithm.')
    expected, algorithm = None, None
    for candidate in candidates:
        try:
            expected = digest(source, src, candidate, cancel)
            algorithm = candidate
            break
        except UnsupportedHash:
            continue
    if candidates and expected is None:
        if policy == 'Required':
            raise UnsupportedHash('Source server hash extension is unavailable; transfer was not started.')
        note('Hash not verified: source server hash extension is unavailable.')
    if expected is None:
        evidence('checksum', 'not_checked', detail='Disabled or no usable native hash algorithm.')
    checked(cancel)
    # Verified local copies/downloads use a private sibling until verification
    # succeeds, preserving an existing visible destination on mismatch.
    staged = atomic or (destination is None and expected is not None)
    target = dst
    if staged:
        token = '.flux-' + uuid.uuid4().hex + '.part'
        target = posixpath.join(posixpath.dirname(dst), token) if destination else str(Path(dst).with_name(token))
    if destination is None:
        original = Path(dst)
        if original.is_symlink() or (original.exists() and not original.is_file()):
            raise ReliabilityError('Destination must be a regular file.')
        if original.with_name(original.name + '.fluxpart').is_symlink():
            raise ReliabilityError('Partial download cannot be a symbolic link.')
        if source is None and Path(src).resolve() == original.resolve():
            raise ReliabilityError('Source and destination are the same file.')
    inner = replace(options, atomic_upload=False, hash_verification='Off', exists_enabled=False, skip_enabled=False,
                    mode='ASCII' if options.ascii(src) else 'Binary')
    previous = [(connection, connection.transfer_options) for connection in (source, destination) if connection]
    published = False
    seed_partial = None
    old_local_target = getattr(source, 'verification_local_target', None) if source else None
    try:
        if atomic and resume:
            note('Atomic upload starts a new temporary file instead of resuming the visible destination.')
            resume = False
        if staged and destination is None and resume:
            old_partial = Path(dst).with_name(Path(dst).name + '.fluxpart')
            seed = old_partial if old_partial.exists() else Path(dst)
            if seed.is_symlink():
                raise ReliabilityError('Partial download cannot be a symbolic link.')
            if seed.exists():
                if seed == old_partial:
                    info = seed.stat()
                    seed_partial = (seed, (info.st_ino, info.st_size, info.st_mtime_ns))
                options.check_space(target, seed.stat().st_size)
                with seed.open('rb') as reader, Path(target + '.fluxpart').open('xb') as writer:
                    while block := reader.read(1024 * 1024):
                        checked(cancel)
                        writer.write(block)
        for connection, _ in previous:
            connection.transfer_options = inner
        if source and staged and destination is None:
            source.verification_local_target = dst
        result = transfer_tree(source, destination, src, target, False, cancel, progress, resume, options=inner)
        if expected is not None:
            actual = None
            for candidate in candidates[candidates.index(algorithm):]:
                try:
                    if candidate != algorithm:
                        expected = digest(source, src, candidate, cancel)
                    actual = digest(destination, target, candidate, cancel)
                    algorithm = candidate
                    break
                except UnsupportedHash:
                    continue
            if actual is None:
                evidence('checksum', 'unavailable', expected, None, algorithm)
                if policy == 'Required':
                    raise UnsupportedHash('Destination server hash extension is unavailable.')
                note('Hash not verified: destination server hash extension is unavailable.')
            else:
                if actual != expected:
                    evidence('checksum', 'mismatch', expected, actual, algorithm)
                    raise ReliabilityError(algorithm + ' hash mismatch; destination was not verified.')
                # Detect a source edited during the transfer/hash operation.
                if digest(source, src, algorithm, cancel) != expected:
                    evidence('checksum', 'source_changed', expected, actual, algorithm)
                    raise ReliabilityError('Source changed during hash verification.')
                evidence('checksum', 'verified', expected, actual, algorithm)
                note('Hash verified: ' + algorithm + ' · ' + dst)
        checked(cancel)
        if staged:
            if destination:
                try:
                    if getattr(destination, 'sftp', None):
                        destination.sftp.posix_rename(target, dst)
                    else:
                        destination.rename(target, dst)
                except (OSError, ftplib.Error) as error:
                    raise ReliabilityError('Atomic promotion failed; the existing destination was not deleted: ' + str(error)) from error
                note('Atomic upload promoted: ' + dst)
            else:
                Path(target).replace(dst)
            published = True
            if seed_partial:
                seed, fingerprint = seed_partial
                # Only retire the original partial if no other writer changed it.
                try:
                    if not seed.is_symlink() and seed.exists():
                        info = seed.stat()
                        if (info.st_ino, info.st_size, info.st_mtime_ns) == fingerprint:
                            seed.unlink()
                except OSError as error:
                    note('Could not retire original partial file ' + str(seed) + ': ' + str(error))
        return result
    except Exception as error:
        from .crc_check import CRCMismatch
        if isinstance(error, CRCMismatch) and staged and destination is None and options.crc_keep_bad:
            partial = Path(target + '.fluxpart')
            if partial.exists():
                partial.replace(Path(dst).with_name(Path(dst).name + '.fluxpart'))
        raise
    finally:
        if source and staged and destination is None:
            if old_local_target is None and hasattr(source, 'verification_local_target'):
                del source.verification_local_target
            elif old_local_target is not None:
                source.verification_local_target = old_local_target
        for connection, original_options in previous:
            connection.transfer_options = original_options
        if staged and not published:
            try:
                if destination:
                    destination.remove(target)
                else:
                    Path(target).unlink(missing_ok=True)
            except (OSError, ftplib.Error) as error:
                note('Could not remove temporary transfer file ' + target + ': ' + str(error))
        if staged and destination is None:
            try:
                Path(target + '.fluxpart').unlink(missing_ok=True)
            except OSError as error:
                note('Could not remove temporary partial file ' + target + '.fluxpart: ' + str(error))
