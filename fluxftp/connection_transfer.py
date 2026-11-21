"""Upload/download execution methods mixed into the protocol connection."""
import ftplib
import os
import posixpath
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from .ascii_rules import NormalizedReader
from .bandwidth import native_transfer
from .connection_model import Cancelled, Entry, remote_path, safe_name
from .transfer_options import TransferOptions
from .verification_reports import tracked_file
from .xdupe import guard as xdupe_guard


class ConnectionTransferMixin:
    """Byte transfer, safe resume, ASCII conversion, and download finalization."""
    def finish_download(self, partial, local, remote, cancel, ascii_mode):
        from .crc_check import verify_download
        verify_download(self, partial, getattr(self, 'verification_local_target', local), remote, cancel, ascii_mode)
        if self.transfer_options.keep_file_time:
            try:
                if self.sftp:
                    stamp = self.sftp.stat(remote).st_mtime
                else:
                    response = self.ftp.sendcmd('MDTM ' + remote)
                    value = response[4:].strip()
                    stamp = datetime.strptime(value, '%Y%m%d%H%M%S.%f' if '.' in value else '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp()
                os.utime(partial, (os.stat(partial).st_atime, stamp))
            except (OSError, ftplib.Error, ValueError) as error:
                self.note('Could not preserve server timestamp: ' + str(error))

    @native_transfer
    def transfer(self, local, remote, upload, cancel: Event, progress, resume=False):
        options = self.transfer_options
        original_passive = self.ftp.passiveserver if self.ftp else None
        if options.force_active and self.ftp and self.proxy:
            raise ValueError("Force Active Mode cannot be used through a proxy.")
        try:
            for attempt in range(3):
                if cancel.is_set():
                    raise Cancelled("Cancelled")
                if self.ftp:
                    if options.force_active:
                        self.ftp.set_pasv(False)
                    self.ftp.data_timeout = options.stall_seconds if options.stall_resume else self.command_timeout
                elif self.sftp:
                    self.sftp.get_channel().settimeout(options.stall_seconds if options.stall_resume else self.command_timeout)
                try:
                    return self._transfer_once(local, remote, upload, cancel, progress, resume)
                except TimeoutError:
                    if not options.stall_resume or attempt == 2:
                        raise
                    self.trace.event("No transfer data before timeout; reconnecting to resume (maximum two retries)")
                    self.close()
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    self.connect()
                    resume = not (self.ftp and options.ascii(remote) and options.ascii_no_resume)
        finally:
            if self.ftp and original_passive is not None:
                self.ftp.set_pasv(original_passive)
                self.ftp.data_timeout = None
            elif self.sftp:
                self.sftp.get_channel().settimeout(self.command_timeout)

    def _transfer_once(self, local, remote, upload, cancel: Event, progress, resume=False):
        from .core import AsciiUploadRejected, bypass_proxy
        options = self.transfer_options
        ascii_mode = self.ftp is not None and options.ascii(remote)
        if ascii_mode and options.ascii_no_resume:
            resume = False  # Text conversion changes byte offsets; restart safely.
        limiter = self.upload_limiter if upload else self.download_limiter
        verification_limiter = self.download_limiter
        if options.bypass_local:
            peer = self.site.host
            if not self.proxy:
                sock = self.ftp.sock if self.ftp else self.ssh.get_transport().sock
                peer = sock.getpeername()[0]
            if bypass_proxy(peer, True):
                limiter = None
                verification_limiter = None
        if self.ftp:
            self.ftp.last_transfer_noop = time.monotonic()
        if getattr(self, 'bandwidth_manager', None):
            from .bandwidth import ManagedLimiter
            limiter = ManagedLimiter(self.bandwidth_manager, 0 if upload else 1, limiter)
            verification_limiter = ManagedLimiter(self.bandwidth_manager, 1, verification_limiter)
        self.phase = "upload" if upload else "download"
        self.trace.event(f"Transfer start: direction={self.phase}; local={local}; remote={remote}; resume={resume}")
        local, remote = Path(local), remote_path(remote)
        if self.site.protocol == 'SCP · SSH':
            from .scp import transfer as scp_transfer
            return scp_transfer(self, local, remote, upload, cancel, progress, resume, limiter)
        if ascii_mode and resume:
            return self._resume_ascii(local, remote, upload, cancel, progress, limiter, verification_limiter)
        if upload:
            total = local.stat().st_size
            exists = any(e.name == posixpath.basename(remote) for e in self.list(posixpath.dirname(remote) or "/")) if resume else False
            self.phase = "upload"
            offset = (self.size(remote) or 0) if resume and exists else 0
            if offset > total:
                raise ValueError("Remote file exceeds local size; cannot resume")
            def chunks_sent(block):
                nonlocal offset
                if cancel.is_set():
                    raise Cancelled("Cancelled")
                offset += len(block)
                if self.ftp:
                    self.ftp.transfer_noop()
                if limiter:
                    limiter.consume(len(block), cancel)
                progress(offset, total)
            if self.sftp:
                with local.open("rb") as src, self.sftp.open(remote, "r+b" if resume and exists else "wb") as dst:
                    src.seek(offset)
                    dst.seek(offset)
                    while block := src.read(16384):
                        if cancel.is_set():
                            raise Cancelled("Cancelled")
                        dst.write(block)
                        chunks_sent(block)
            else:
                with local.open("rb") as src:
                    src.seek(offset)
                    if ascii_mode:
                        try:
                            self._ascii_upload(src, remote, chunks_sent)
                        except AsciiUploadRejected:
                            if cancel.is_set():
                                raise Cancelled('Cancelled')
                            self.trace.event('Server refuses ASCII uploads; retrying this file in Binary mode.')
                            ascii_mode = False
                            src.seek(0)
                            offset = 0
                            self.ftp.storbinary("STOR " + remote, src, 16384, chunks_sent)
                    else:
                        self.ftp.storbinary("STOR " + remote, src, 16384, chunks_sent, rest=offset if resume else None)
        else:
            total = self.size(remote) or 0
            partial = local.with_name(local.name + ".fluxpart")
            if partial.is_symlink():
                raise ValueError("Partial download cannot be a symbolic link")
            offset = partial.stat().st_size if resume and partial.exists() else 0
            if offset > total:
                raise ValueError("Partial download exceeds remote size")
            offset = max(0, offset - options.rollback_kib * 1024)
            options.check_space(local, max(0, total - offset))
            with partial.open("r+b" if offset else "wb", buffering=self.download_buffering()) as dst:
                dst.seek(offset)
                dst.truncate(offset)
                def received(block):
                    nonlocal offset
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    options.check_space(local, len(block))
                    dst.write(block)
                    offset += len(block)
                    if self.ftp:
                        self.ftp.transfer_noop()
                    if limiter:
                        limiter.consume(len(block), cancel)
                    progress(offset, total)
                if self.sftp:
                    with self.sftp.open(remote, "rb") as src:
                        src.seek(offset)
                        while block := src.read(16384):
                            received(block)
                else:
                    if ascii_mode:
                        self._ascii_download(remote, received)
                    else:
                        self.ftp.retrbinary("RETR " + remote, received, 16384, rest=offset if offset else None)
            if not ascii_mode and offset != total:
                raise IOError(f"Size mismatch: expected {total}, received {offset}")
            if cancel.is_set():
                raise Cancelled("Cancelled")
            self.finish_download(partial, local, remote, cancel, ascii_mode)
            partial.replace(local)
        if not ascii_mode:
            from .verification_reports import evidence
            actual = self.size(remote) if upload else offset
            evidence('size', 'verified' if actual == total else 'mismatch', total, actual)
            if actual != total: raise IOError("Remote size does not match the uploaded file")
        if cancel.is_set():
            raise Cancelled("Cancelled")
        progress(offset if ascii_mode else total, offset if ascii_mode else total)
        self.trace.event(f"Transfer complete: bytes={total}")

    def _resume_ascii(self, local, remote, upload, cancel, progress, limiter, verification_limiter):
        options = self.transfer_options
        done = 0
        if upload:
            total = local.stat().st_size
            existing = [entry for entry in self.list(posixpath.dirname(remote) or "/") if entry.name == posixpath.basename(remote)]
            if any(entry.is_dir or entry.is_link for entry in existing):
                raise ValueError("ASCII resume requires a regular remote file.")
            exists = bool(existing)
            with local.open("rb") as raw:
                source = NormalizedReader(raw)
                if exists:
                    def verify(block):
                        nonlocal done
                        if cancel.is_set():
                            raise Cancelled("Cancelled")
                        if source.read(len(block)) != block:
                            raise ValueError("ASCII resume refused: the existing remote file is not a matching prefix of the local file.")
                        done += len(block)
                        if verification_limiter:
                            verification_limiter.consume(len(block), cancel)
                    self._ascii_download(remote, verify, newline=b"\n")
                def sent(block):
                    nonlocal done
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    done += len(block)
                    if limiter:
                        limiter.consume(len(block), cancel)
                    self.ftp.transfer_noop()
                    progress(done, max(done, total))
                self._ascii_upload(source, remote, sent, append=exists)
        else:
            partial = local.with_name(local.name + ".fluxpart")
            if partial.is_symlink():
                raise ValueError("Partial download cannot be a symbolic link")
            retained = max(0, (partial.stat().st_size if partial.exists() else 0) - options.rollback_kib * 1024)
            total = self.size(remote) or 0
            options.check_space(local, max(0, total - retained))
            remaining = retained
            prepared = False
            with partial.open("r+b" if partial.exists() else "w+b", buffering=self.download_buffering()) as target:
                def received(block):
                    nonlocal done, remaining, prepared
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    if limiter:
                        limiter.consume(len(block), cancel)
                    take = min(remaining, len(block))
                    if take and target.read(take) != block[:take]:
                        raise ValueError("ASCII resume refused: the partial download does not match the remote file.")
                    remaining -= take
                    if not remaining and not prepared:
                        target.truncate(retained)
                        prepared = True
                    if block[take:]:
                        options.check_space(local, len(block) - take)
                        target.write(block[take:])
                    done += len(block)
                    self.ftp.transfer_noop()
                    progress(done, max(done, total))
                self._ascii_download(remote, received)
                if remaining:
                    raise ValueError("ASCII resume refused: the partial download exceeds the remote file.")
                if not prepared:
                    target.truncate(0)
            if cancel.is_set():
                raise Cancelled("Cancelled")
            self.finish_download(partial, local, remote, cancel, True)
            partial.replace(local)
        if cancel.is_set():
            raise Cancelled("Cancelled")
        progress(done, done)
        self.trace.event(f"ASCII transfer complete after verified-prefix resume: bytes={done}")

    def _ascii_upload(self, stream, remote, callback, append=False):
        from .core import AsciiUploadRejected
        self.ftp.voidcmd("TYPE A")
        pending = b""
        try:
            data_connection = self.ftp.transfercmd(("APPE " if append else "STOR ") + remote)
        except ftplib.error_perm as error:
            message = str(error).casefold()
            if (not append and message.startswith(('550 ', '553 ')) and 'ascii' in message
                    and any(word in message for word in ('not allowed', 'not supported', 'disabled', 'forbidden'))):
                raise AsciiUploadRejected(str(error)) from error
            raise
        with data_connection as data:
            while block := stream.read(16384):
                raw = pending + block
                pending = b"\r" if raw.endswith(b"\r") else b""
                if pending:
                    raw = raw[:-1]
                data.sendall(raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
                callback(block)
            if pending:
                data.sendall(pending)
            if isinstance(data, ssl.SSLSocket):
                data.unwrap()
        self.ftp.voidresp()

    def _ascii_download(self, remote, callback, newline=None):
        newline = os.linesep.encode("ascii") if newline is None else newline
        self.ftp.voidcmd("TYPE A")
        pending = b""
        with self.ftp.transfercmd("RETR " + remote) as data:
            while block := data.recv(16384):
                raw = pending + block
                pending = b"\r" if raw.endswith(b"\r") else b""
                if pending:
                    raw = raw[:-1]
                callback(raw.replace(b"\r\n", b"\n").replace(b"\n", newline))
            if pending:
                callback(pending)
            if isinstance(data, ssl.SSLSocket):
                data.unwrap()
        self.ftp.voidresp()


from .transfer_service import transfer_tree
