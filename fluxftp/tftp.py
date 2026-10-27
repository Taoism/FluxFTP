"""Bounded RFC 1350 octet transfers; no option negotiation or resume."""
import os
import socket
import struct
import tempfile
import time
from pathlib import Path
from threading import Event

from .core import Cancelled


def transfer(host, port, remote, local, *, upload=False, cancel=None,
             progress=lambda count: None, timeout=2.0, retries=5):
    cancel = cancel or Event()
    name = remote.encode('ascii')
    if not name or any(c < 32 or c == 127 for c in name) or len(name) > 480:
        raise ValueError('Use a nonempty ASCII remote filename, at most 480 bytes.')
    if not host.strip() or not 1 <= int(port) <= 65535 or timeout <= 0 or retries < 0:
        raise ValueError('Invalid TFTP endpoint or retry settings.')
    local = Path(local)
    if upload and local.stat().st_size >= 65535 * 512:
        raise ValueError('TFTP files must be smaller than 32 MiB (no block rollover).')
    family, kind, proto, _, address = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
    peer = None
    temporary = None
    count = 0
    def check():
        if cancel.is_set():
            raise Cancelled('TFTP cancelled; the remote server may retain a partial upload.')
    try:
        check()
        if upload:
            stream = local.open('rb')
        else:
            fd, temporary = tempfile.mkstemp(prefix='.fluxftp-tftp-', dir=local.parent)
            stream = os.fdopen(fd, 'wb')
        with stream, socket.socket(family, kind, proto) as sock:
            sock.settimeout(min(.1, timeout))
            packet = struct.pack('!H', 2 if upload else 1) + name + b'\0octet\0'
            expected = 0 if upload else 1
            payload = b''
            attempts = 0
            sock.sendto(packet, address)
            deadline = time.monotonic() + timeout
            while True:
                check()
                if time.monotonic() >= deadline:
                    if attempts >= retries:
                        raise TimeoutError('TFTP server did not acknowledge the transfer.')
                    sock.sendto(packet, peer or address)
                    attempts += 1
                    deadline = time.monotonic() + timeout
                try:
                    data, sender = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                if sender[0] != address[0] or (peer is not None and sender != peer):
                    sock.sendto(b'\0\5\0\5Unknown transfer ID\0', sender)
                    continue
                if len(data) < 4:
                    continue
                opcode, block = struct.unpack('!HH', data[:4])
                if opcode == 5:
                    message = data[4:260].split(b'\0', 1)[0].decode('ascii', 'replace')
                    raise OSError(f'TFTP error {block}: {message}')
                valid = (opcode == 4 and len(data) == 4) if upload else (opcode == 3 and len(data) <= 516)
                if not valid:
                    continue
                if block != expected:
                    if not upload and peer is not None and block == expected - 1:
                        sock.sendto(packet, peer)
                    # Duplicate ACKs never trigger retransmission (RFC 1123).
                    continue
                peer = sender
                attempts = 0
                if upload:
                    count += len(payload)
                    progress(count)
                    if expected and len(payload) < 512:
                        return count
                    if expected == 65535:
                        raise ValueError('TFTP block rollover is unsupported.')
                    expected += 1
                    payload = stream.read(512)
                    packet = struct.pack('!HH', 3, expected) + payload
                else:
                    payload = data[4:]
                    stream.write(payload)
                    count += len(payload)
                    progress(count)
                    packet = struct.pack('!HH', 4, expected)
                    if len(payload) < 512:
                        stream.flush()
                        os.fsync(stream.fileno())
                        sock.sendto(packet, peer)
                        # Dally to acknowledge a retransmitted final DATA packet.
                        end = time.monotonic() + timeout * 2
                        while time.monotonic() < end:
                            check()
                            try:
                                duplicate, sender = sock.recvfrom(2048)
                            except socket.timeout:
                                continue
                            if sender == peer and duplicate == data:
                                sock.sendto(packet, peer)
                        break
                    if expected == 65535:
                        raise ValueError('TFTP block rollover is unsupported.')
                    expected += 1
                sock.sendto(packet, peer)
                deadline = time.monotonic() + timeout
        check()
        os.replace(temporary, local)
        temporary = None
        return count
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
