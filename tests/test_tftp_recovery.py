import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from fluxftp.tftp import transfer
from fluxftp.core import Cancelled


def test_lost_ack_and_duplicate_ack_do_not_amplify(tmp_path):
    path = tmp_path / 'source'
    path.write_bytes(b'a' * 512 + b'end')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1', 0))
        server.settimeout(2)
        def run():
            _, client = server.recvfrom(1024)
            server.sendto(struct.pack('!HH', 4, 0), client)
            first = server.recvfrom(1024)[0]
            assert first == struct.pack('!HH', 3, 1) + b'a' * 512
            # Losing ACK 1 must retransmit the same block.
            assert server.recvfrom(1024)[0] == first
            server.sendto(struct.pack('!HH', 4, 1), client)
            final = server.recvfrom(1024)[0]
            assert final == struct.pack('!HH', 3, 2) + b'end'
            server.sendto(struct.pack('!HH', 4, 1), client)
            server.settimeout(.03)
            with pytest.raises(socket.timeout):
                server.recvfrom(1024)
            server.sendto(struct.pack('!HH', 4, 2), client)
        with ThreadPoolExecutor() as pool:
            task = pool.submit(run)
            assert transfer('127.0.0.1', server.getsockname()[1], 'file', path,
                            upload=True, timeout=.15) == 515
            task.result()


@pytest.mark.parametrize('failure', ['error', 'cancel', 'wrong_tid'])
def test_failed_download_keeps_original(tmp_path, failure):
    target = tmp_path / 'target'
    target.write_bytes(b'original')
    cancel = Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1', 0))
        server.settimeout(2)
        def run():
            _, client = server.recvfrom(1024)
            server.sendto(struct.pack('!HH', 3, 1) + b'a' * 512, client)
            assert server.recvfrom(1024)[0] == struct.pack('!HH', 4, 1)
            if failure == 'error':
                server.sendto(b'\0\5\0\2Access denied\0', client)
            elif failure == 'cancel':
                cancel.set()
            else:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as foreign:
                    foreign.settimeout(2)
                    foreign.sendto(struct.pack('!HH', 3, 2) + b'forged', client)
                    assert foreign.recvfrom(1024)[0][:4] == b'\0\5\0\5'
                cancel.set()
        with ThreadPoolExecutor() as pool:
            task = pool.submit(run)
            with pytest.raises(OSError if failure == 'error' else Cancelled):
                transfer('127.0.0.1', server.getsockname()[1], 'file', target,
                         cancel=cancel, timeout=.1)
            task.result()
    assert target.read_bytes() == b'original'
    assert not list(tmp_path.glob('.fluxftp-tftp-*'))
