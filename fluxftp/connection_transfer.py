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


@tracked_file
@xdupe_guard
def transfer_tree(source, destination, src, dst, is_dir, cancel, progress, resume=False, depth=0, options=None, skipped=None, renamed=None, planned=None, listed_size=None, skipped_directories=None, conflict=None, notice=None, file_started=None):
    """Copy between local/remote or two FTP servers without following directory links."""
    if cancel.is_set():
        raise Cancelled("Cancelled")
    if depth > 100:
        raise ValueError("Directory depth limit exceeded")
    options = options or (destination.transfer_options if destination else source.transfer_options if source else TransferOptions())
    name = posixpath.basename(src.rstrip("/")) if source else Path(src).name
    size = listed_size
    if options.skip_enabled and options.skip_zero and not is_dir and size is None:
        try:
            size = source.size(src) if source else Path(src).stat().st_size
        except ftplib.error_perm:
            # Servers without SIZE can still transfer; unknown size is not zero.
            size = None
    reason = options.skip_reason(name, is_dir, size, automatic=depth > 0)
    if reason:
        if is_dir and skipped_directories is not None:
            skipped_directories.add(src)
        if skipped:
            skipped(src, reason)
        return False
    if not is_dir:
        if name.lower().endswith('.sfv') and (options.single_sfv or options.skip_multiple_sfv):
            from .crc_check import sfv_names
            parent = (posixpath.dirname(src) or '/') if source else Path(src).parent
            siblings = source.list(parent) if source else [Entry(p.name, p.is_dir(), is_link=p.is_symlink()) for p in parent.iterdir()]
            if name not in sfv_names(siblings, options):
                if skipped: skipped(src, 'Misc: SFV selection rule')
                return False
        if planned is not None:
            planned.append((src, dst, size))
            return True
        if file_started:
            file_started(src, dst)
        if options.exists_enabled and not (destination and options.atomic_upload):
            from .file_exists import transfer_existing
            return transfer_existing(source, destination, src, dst, cancel, progress, resume, options, skipped, renamed, conflict, notice)
        if options.atomic_upload or options.hash_verification != "Off":
            from .transfer_reliability import transfer
            return transfer(source, destination, src, dst, cancel, progress, resume, options, notice)
        if source and destination:
            from .protocol_adapter import connection_capabilities
            source_caps, destination_caps = connection_capabilities(source), connection_capabilities(destination)
            if 'cloud' in (source_caps.backend, destination_caps.backend):
                from .cloud_connection import relay
                # Object stores do not support appending at an arbitrary byte offset.
                if resume and any(e.name == posixpath.basename(dst) for e in destination.list(posixpath.dirname(dst) or '/')):
                    raise ValueError('Cloud relay cannot resume an existing destination; choose overwrite or enable resume-failure overwrite.')
                relay(source, destination, src, dst, cancel, progress, resume=False)
            elif source_caps.direct_fxp and destination_caps.direct_fxp:
                from .core import fxp
                fxp(source, destination, src, dst, resume=resume, cancel=cancel, progress=progress)
            elif 'unknown' not in (source_caps.backend, destination_caps.backend):
                from .remote_relay import relay
                relay(source, destination, src, dst, cancel, progress, resume)
            else:
                raise ValueError('The selected backend has no remote-to-remote transfer adapter.')
        elif destination:
            from .protocol_adapter import adapter_for
            adapter_for(destination).transfer(src, dst, True, cancel, progress, resume)
        elif source:
            from .protocol_adapter import adapter_for
            adapter_for(source).transfer(dst, src, False, cancel, progress, resume)
        else:
            source_path, destination_path = Path(src), Path(dst)
            if source_path.resolve() == destination_path.resolve():
                raise ValueError("Source and destination are the same file")
            if destination_path.is_symlink():
                raise ValueError("Refusing to overwrite a symbolic link")
            partial = destination_path.with_name(destination_path.name + ".fluxpart")
            if partial.is_symlink():
                raise ValueError("Partial copy cannot be a symbolic link")
            total = source_path.stat().st_size
            offset = partial.stat().st_size if resume and partial.exists() else 0
            if offset > total:
                raise ValueError("Partial copy exceeds source size")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            options.check_space(dst, total - offset)
            with source_path.open("rb") as input_file, partial.open("ab" if offset else "wb") as output_file:
                input_file.seek(offset)
                while block := input_file.read(262144):
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    options.check_space(dst, len(block))
                    output_file.write(block)
                    offset += len(block)
                    progress(offset, total)
            if offset != total:
                from .verification_reports import evidence
                evidence('size', 'mismatch', total, offset)
                raise IOError(f"Size mismatch: expected {total}, copied {offset}")
            from .verification_reports import evidence
            evidence('size', 'verified', total, offset)
            if cancel.is_set():
                raise Cancelled("Cancelled")
            partial.replace(destination_path)
            import shutil
            shutil.copystat(source_path, destination_path)
            progress(total, total)
        return True
    if source:
        entries = source.list(src)
    else:
        entries = [Entry(p.name, p.is_dir(), p.stat().st_size, is_link=p.is_symlink()) for p in Path(src).iterdir()]
    if source and not entries and re.search(r'-\s*COMPLETE\s*-', name, re.IGNORECASE):
        if skipped_directories is not None:
            skipped_directories.add(src)
        if skipped:
            skipped(src, 'Empty COMPLETE marker folder')
        return False
    reason = options.skip_reason(name, True, automatic=depth > 0, empty=not entries)
    if reason:
        if skipped_directories is not None:
            skipped_directories.add(src)
        if skipped:
            skipped(src, reason)
        return False
    kept = []
    for entry in entries:
        reason = options.skip_reason(entry.name, entry.is_dir, automatic=True)
        if reason:
            if entry.is_dir and skipped_directories is not None:
                skipped_directories.add(posixpath.join(src, entry.name) if source else str(Path(src) / entry.name))
            if skipped:
                skipped(posixpath.join(src, entry.name) if source else str(Path(src) / entry.name), reason)
        else:
            kept.append(entry)
    entries = kept
    created = False
    if destination:
        parent = posixpath.dirname(dst.rstrip("/")) or "/"
        name = posixpath.basename(dst.rstrip("/"))
        matches = [e for e in destination.list(parent) if e.name == name]
        if matches:
            if not matches[0].is_dir or matches[0].is_link:
                raise ValueError(f"Destination is not a directory: {dst}")
        else:
            destination.mkdir(dst)
            created = True
    else:
        if Path(dst).is_symlink():
            raise ValueError("Refusing to write through a directory symlink")
        created = not Path(dst).exists()
        if created and options.deny_parent_name and Path(dst).name.casefold() == Path(dst).parent.name.casefold():
            raise ValueError('New directory cannot have the same name as its parent')
        Path(dst).mkdir(parents=True, exist_ok=True)
    if destination and options.remove_missing_zero:
        from .misc_transfer import remove_missing_zero
        remove_missing_zero(source, destination, src, dst, options, cancel)
    route = "FXP" if source and destination else "Upload" if destination else "Download" if source else "Copy"
    entries.sort(key=lambda entry: options.priority(entry.name))
    converted_names = [options.filename(e.name, route) for e in entries]
    compared = [name.casefold() for name in converted_names] if not destination and os.name == "nt" else converted_names
    if len(compared) != len(set(compared)):
        raise ValueError("Filename conversion would overwrite another file in this folder.")
    planned_before = len(planned) if planned is not None else 0
    for e, target_name in zip(entries, converted_names):
        safe_name(e.name)
        safe_name(target_name)
        if e.is_link:
            raise ValueError(f"Symbolic links are not transferred: {e.name}")
        s = posixpath.join(src, e.name) if source else str(Path(src) / e.name)
        d = posixpath.join(dst, target_name) if destination else str(Path(dst) / target_name)
        transfer_tree(source, destination, s, d, e.is_dir, cancel, progress, resume, depth + 1, options, skipped, renamed, planned, e.size, skipped_directories, conflict, notice, file_started)
    if created and options.no_empty_dirs and (planned is None or len(planned) == planned_before):
        if destination:
            if not destination.list(dst): destination.remove(dst, is_dir=True)
        elif not any(Path(dst).iterdir()):
            Path(dst).rmdir()
    return True



