"""Single-file legacy SCP payloads over a verified Paramiko SSH transport.

SFTP supplies browsing/metadata. No shell parsing of server-supplied paths, no
recursive SCP records and no byte-offset resume; the normal queue owns trees.
"""
import posixpath
import re
import shlex
import socket
import time
from threading import Event, Thread
from pathlib import Path


class SCPError(OSError):
    pass


def execute(channel, command, cancel, timeout):
    """Paramiko's exec acknowledgement wait has no channel timeout of its own."""
    finished, expired = Event(), Event()
    def guard():
        deadline = time.monotonic() + timeout
        while not finished.wait(.1):
            if cancel.is_set() or time.monotonic() >= deadline:
                if not cancel.is_set(): expired.set()
                try: channel.close()
                except Exception: pass
                return
    watcher = Thread(target=guard, daemon=True)
    watcher.start()
    try:
        try:
            channel.exec_command(command)
        except Exception as error:
            if cancel.is_set():
                from .core import Cancelled
                raise Cancelled('SCP cancelled') from error
            if expired.is_set(): raise TimeoutError('SCP command acknowledgement timed out') from error
            raise SCPError('Server rejected SCP execution; enable the scp command and a POSIX-compatible shell, or select SFTP.') from error
    finally:
        finished.set()
        watcher.join(1)


class Wire:
    def __init__(self, channel, cancel, timeout):
        self.channel, self.cancel, self.timeout = channel, cancel, timeout
        channel.settimeout(.2)

    def checked(self):
        if self.cancel.is_set():
            from .core import Cancelled
            raise Cancelled('SCP cancelled')

    def io(self, operation):
        deadline = time.monotonic() + self.timeout
        while True:
            self.checked()
            try:
                return operation()
            except socket.timeout:
                if time.monotonic() >= deadline:
                    raise TimeoutError('SCP channel timed out')

    def send(self, data):
        while data:
            count = self.io(lambda: self.channel.send(data))
            if count <= 0: raise SCPError('SCP channel closed while sending')
            data = data[count:]

    def receive(self, size):
        block = self.io(lambda: self.channel.recv(size))
        if not block:
            raise SCPError('SCP channel closed unexpectedly; server must allow the scp command')
        return block

    def line(self):
        data = bytearray()
        for _ in range(8192):
            char = self.receive(1)
            if char == b'\n': return bytes(data)
            data.extend(char)
        raise SCPError('SCP response exceeded the header limit')

    def ack(self):
        code = self.receive(1)
        if code == b'\0': return
        if code in (b'\1', b'\2'):
            detail = self.line().decode('utf-8', 'replace')
            raise SCPError('SCP server refused transfer: ' + ''.join(c for c in detail if c.isprintable())[:500])
        raise SCPError('Invalid SCP acknowledgement')

    def finish(self):
        self.channel.shutdown_write()
        deadline = time.monotonic() + self.timeout
        # Require EOF and a successful command status, not just a payload ACK.
        while True:
            self.checked()
            try:
                data = self.channel.recv(1024)
                if data: raise SCPError('Unexpected extra SCP record')
                break
            except socket.timeout:
                if time.monotonic() >= deadline: raise TimeoutError('SCP completion timed out')
        while not self.channel.exit_status_ready():
            self.checked()
            if time.monotonic() >= deadline: raise TimeoutError('SCP exit status timed out')
            self.cancel.wait(.05)
        if self.channel.recv_exit_status() != 0:
            raise SCPError('Remote scp command failed or did not report a successful exit')


def transfer(connection, local, remote, upload, cancel, progress, resume, limiter):
    from .core import remote_path, Cancelled
    from .verification_reports import evidence
    if resume:
        raise ValueError('SCP does not support byte-offset resume. Choose Overwrite, or use SFTP for resumable transfers.')
    remote = remote_path(remote)
    name = posixpath.basename(remote)
    if not name or name in ('.', '..') or any(ord(c) < 32 or ord(c) == 127 for c in remote):
        raise ValueError('Invalid SCP path')
    local = Path(local)
    total = local.stat().st_size if upload else connection.size(remote)
    if total is None or total < 0: raise SCPError('SCP requires a known file size')
    options = connection.transfer_options
    partial = local.with_name(local.name + '.fluxpart')
    if not upload:
        if partial.is_symlink(): raise ValueError('Partial download cannot be a symbolic link')
        options.check_space(local, total)
    if cancel.is_set(): raise Cancelled('SCP cancelled')
    channel = connection.ssh.get_transport().open_session(timeout=connection.command_timeout)
    timeout = options.stall_seconds if options.stall_resume else connection.command_timeout
    try:
        channel.settimeout(connection.command_timeout)
        execute(channel, 'scp ' + ('-t' if upload else '-f') + ' -- ' + shlex.quote(remote), cancel, connection.command_timeout)
        wire = Wire(channel, cancel, timeout)
        done = 0
        if upload:
            wire.ack()
            wire.send(f'C0600 {total} {name}\n'.encode('utf-8'))
            wire.ack()
            with local.open('rb') as source:
                while done < total:
                    wire.checked()
                    block = source.read(min(65536, total-done))
                    if not block: raise SCPError('Local file shrank during upload')
                    wire.send(block)
                    done += len(block)
                    if limiter: limiter.consume(len(block), cancel)
                    progress(done, total)
                if source.read(1): raise SCPError('Local file grew during upload')
            wire.send(b'\0')
            wire.ack()
        else:
            wire.send(b'\0')
            header = wire.line()
            match = re.fullmatch(rb'C[0-7]{4} ([0-9]{1,20}) (.+)', header)
            if not match or int(match[1]) != total or match[2] != name.encode('utf-8'):
                raise SCPError('SCP returned an unexpected filename, size or record')
            wire.send(b'\0')
            with partial.open('wb', buffering=connection.download_buffering()) as target:
                while done < total:
                    block = wire.receive(min(65536, total-done))
                    options.check_space(local, len(block))
                    target.write(block)
                    done += len(block)
                    if limiter: limiter.consume(len(block), cancel)
                    progress(done, total)
            wire.ack()
            wire.send(b'\0')
        wire.finish()
        wire.checked()
        actual = connection.size(remote) if upload else done
        evidence('size', 'verified' if actual == total else 'mismatch', total, actual)
        if actual != total: raise SCPError('SCP remote size did not match the uploaded file')
        if not upload:
            connection.finish_download(partial, local, remote, cancel, False)
            wire.checked()
            partial.replace(local)
        progress(total, total)
    finally:
        try: channel.close()
        except Exception: pass
