"""Binary remote-to-remote relay using a bounded-memory private disk staging file."""
import posixpath
import tempfile
from dataclasses import replace
from pathlib import Path


def relay(source, destination, src, dst, cancel, progress, resume=False):
    from .core import Cancelled
    identity = lambda connection, path: (connection.site.protocol, connection.site.host.casefold(),
        connection.site.port, connection.site.username, posixpath.normpath(path))
    if identity(source, src) == identity(destination, dst):
        raise ValueError('Relay source and destination are the same file')
    originals = [(connection, connection.transfer_options) for connection in (source, destination)]
    try:
        # SFTP is byte-oriented. Avoid local newline conversion on a mixed FTP route.
        for connection, options in originals:
            connection.transfer_options = replace(options, mode='Binary')
        total = source.size(src)
        if total is None or total < 0:
            raise ValueError('Relay requires a known source size')
        existing = next((entry for entry in destination.list(posixpath.dirname(dst) or '/')
                         if entry.name == posixpath.basename(dst)), None)
        if existing and (existing.is_dir or existing.is_link):
            raise ValueError('Relay destination must be a regular file')
        if resume and existing and (existing.size is None or existing.size > total):
            raise ValueError('Relay destination exceeds source size or size is unknown')
        with tempfile.TemporaryDirectory(prefix='fluxftp-sftp-relay-') as directory:
            local = Path(directory) / 'payload'
            source.transfer_options.check_space(local, total)
            from .protocol_adapter import adapter_for
            adapter_for(source).transfer(local, src, False, cancel, lambda done, _: progress(done, total * 2), False)
            if cancel.is_set(): raise Cancelled('Cancelled')
            if local.stat().st_size != total or source.size(src) != total:
                raise OSError('Relay source size changed during download')
            adapter_for(destination).transfer(local, dst, True, cancel,
                                 lambda done, _: progress(total + done, total * 2), resume)
            if cancel.is_set(): raise Cancelled('Cancelled')
            progress(total * 2, total * 2)
    finally:
        for connection, options in originals:
            connection.transfer_options = options
