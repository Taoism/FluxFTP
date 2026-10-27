"""Download CRC verification and narrowly owned CRC status artifacts."""
import ftplib
import hashlib
import json
import posixpath
import re
import threading
import zlib
from pathlib import Path

_lock = threading.Lock()
_signature = 'FluxFTP CRC status 1'


class CRCMismatch(Exception):
    def __init__(self, remote, local, expected, actual):
        self.remote, self.local = remote, str(local)
        super().__init__(f'CRC mismatch for {remote}: expected {expected:08X}, received {actual:08X}')


def sfv_names(entries, options):
    names = sorted((e.name for e in entries if not e.is_dir and not e.is_link and e.name.lower().endswith('.sfv')), key=lambda n: (n.casefold(), n))
    if len(names) > 1 and options.skip_multiple_sfv:
        return []
    return names[:1] if options.single_sfv else names


def parse_sfv(data, parent):
    values = {}
    for line in data.decode('utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith(';'): continue
        name, checksum = line.rsplit(None, 1)
        if not re.fullmatch('[0-9a-fA-F]{8}', checksum):
            raise ValueError('Invalid SFV checksum')
        name = name.replace('\\', '/')
        if name.startswith('/') or ':' in name or '..' in name.split('/') or any(ord(c) < 32 for c in name):
            raise ValueError('Unsafe path in SFV manifest')
        path = posixpath.normpath(posixpath.join(parent, name))
        value = int(checksum, 16)
        if path in values and values[path] != value: raise ValueError('Conflicting SFV checksums')
        values[path] = value
    return values


def expected_crc(connection, remote, cancel):
    options = connection.transfer_options
    cache = getattr(connection, '_sfv_crc_cache', None)
    if cache is None:
        cache = connection._sfv_crc_cache = {}
    parent = posixpath.dirname(remote) or '/'
    root = getattr(connection, 'crc_root', parent)
    parents = [parent]
    while parent != root and parent != '/' and parent.startswith(root.rstrip('/') + '/'):
        parent = posixpath.dirname(parent) or '/'
        parents.append(parent)
    found = None
    if options.crc_auto:
        for parent in parents:
            if parent not in cache:
                values = {}
                for name in sfv_names(connection.list(parent), options):
                    path = posixpath.join(parent, name)
                    content = bytearray()
                    def received(block):
                        from .core import Cancelled
                        if cancel.is_set(): raise Cancelled('Cancelled')
                        if len(content) + len(block) > 4 * 1024 * 1024: raise ValueError('SFV manifest exceeds 4 MB')
                        content.extend(block)
                    if hasattr(connection, 'read_file_bytes'):
                        received(connection.read_file_bytes(path, cancel, 4 * 1024 * 1024))
                    elif connection.sftp:
                        with connection.sftp.open(path, 'rb') as stream:
                            while block := stream.read(16384): received(block)
                    else:
                        connection.ftp.retrbinary('RETR ' + path, received)
                    for filename, checksum in parse_sfv(bytes(content), parent).items():
                        if filename in values and values[filename] != checksum: raise ValueError('Conflicting SFV checksums')
                        values[filename] = checksum
                cache[parent] = values
            value = cache[parent].get(remote)
            if value is not None:
                if found is not None and found != value: raise ValueError('Conflicting SFV checksums')
                found = value
    if options.crc_xcrc and connection.ftp and not getattr(connection, '_xcrc_unsupported', False):
        try:
            response = connection.ftp.sendcmd('XCRC ' + remote)
            match = re.fullmatch(r'2\d\d\s+([0-9a-fA-F]{8})\s*', response)
            if not match: raise ValueError('Unrecognized XCRC reply')
            server_crc = int(match[1], 16)
            if found is None: found = server_crc
            elif found != server_crc:
                connection.note('Server XCRC differs from SFV; checking against the SFV checksum')
        except ftplib.error_perm as error:
            if str(error).startswith(('500', '501', '502', '504')):
                connection._xcrc_unsupported = True
            else:
                raise
    return found


def record_status(local, status, options):
    """Never replace or remove unrelated files, symlinks, or folder contents."""
    local = Path(local)
    log = local.parent / '.fluxftp-crc.log'
    tags = local.parent / '.fluxftp-crc-status'
    owner = tags / '.owner'
    key = hashlib.sha256(local.name.encode('utf-8')).hexdigest()
    with _lock:
        if log.is_symlink() or tags.is_symlink() or owner.is_symlink():
            return
        data = {'format': _signature, 'files': {}}
        if log.exists():
            try: data = json.loads(log.read_text('utf-8'))
            except (ValueError, OSError): return
            if not isinstance(data, dict) or data.get('format') != _signature or not isinstance(data.get('files'), dict): return
        data['files'][local.name] = status
        complete = all(value == 'OK' for value in data['files'].values())
        if complete and options.crc_delete_log:
            if log.exists(): log.unlink()
        else:
            log.write_text(json.dumps(data, indent=2), encoding='utf-8')
        if tags.exists() and (not tags.is_dir() or not owner.is_file() or owner.read_text('utf-8') != _signature):
            return
        if complete and options.crc_delete_tags:
            if tags.exists():
                for child in tags.iterdir():
                    if not child.is_symlink() and child.is_file() and re.fullmatch(r'[0-9a-f]{64}\.json', child.name):
                        try: record = json.loads(child.read_text('utf-8'))
                        except (ValueError, OSError): continue
                        if isinstance(record, dict) and record.get('format') == _signature: child.unlink()
                if set(tags.iterdir()) == {owner}:
                    owner.unlink()
                    tags.rmdir()
        else:
            tags.mkdir(exist_ok=True)
            if not owner.exists(): owner.write_text(_signature, encoding='utf-8')
            tag = tags / (key + '.json')
            if not tag.is_symlink():
                if tag.exists():
                    try: existing = json.loads(tag.read_text('utf-8'))
                    except (ValueError, OSError): return
                    if not isinstance(existing, dict) or existing.get('format') != _signature: return
                tag.write_text(json.dumps({'format': _signature, 'file': local.name, 'status': status}), encoding='utf-8')


def verify_download(connection, partial, local, remote, cancel, ascii_mode=False):
    options = connection.transfer_options
    if not (options.crc_auto or options.crc_xcrc) or remote.lower().endswith('.sfv'): return
    if ascii_mode:
        connection.note('CRC check skipped for converted ASCII download: ' + remote)
        return
    expected = expected_crc(connection, remote, cancel)
    if expected is None: return
    actual = 0
    with Path(partial).open('rb') as stream:
        while block := stream.read(1048576):
            from .core import Cancelled
            if cancel.is_set(): raise Cancelled('Cancelled')
            actual = zlib.crc32(block, actual)
    status = 'OK' if actual == expected else f'Mismatch: expected {expected:08X}, received {actual:08X}'
    from .verification_reports import evidence
    evidence('CRC', 'verified' if actual == expected else 'mismatch', f'{expected:08X}', f'{actual:08X}', 'CRC32')
    try: record_status(local, status, options)
    except (OSError, ValueError) as error: connection.note('Could not update CRC status: ' + str(error))
    connection.note(f'CRC {status}: {remote}')
    if actual != expected:
        if not options.crc_keep_bad:
            Path(partial).unlink()
        raise CRCMismatch(remote, local, expected, actual)
