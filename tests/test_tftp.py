import socket
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from fluxftp.tftp import transfer
from fluxftp.core import Cancelled


@pytest.mark.parametrize('size', [0, 1, 512, 1500])
@pytest.mark.parametrize('upload', [False, True])
def test_real_udp_transfer(tmp_path, size, upload):
    content = bytes(i % 256 for i in range(size))
    target = tmp_path / 'file'
    target.write_bytes(content if upload else b'old')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.settimeout(3)
        def server():
            request, client = listener.recvfrom(1024)
            assert request == struct.pack('!H', 2 if upload else 1) + b'file\0octet\0'
            # Replies must use the negotiated transfer port, not port 69.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as data:
                data.bind(('127.0.0.1', 0))
                data.settimeout(3)
                if upload:
                    data.sendto(struct.pack('!HH', 4, 0), client)
                    received = b''
                    block = 1
                    while True:
                        packet, _ = data.recvfrom(1024)
                        assert packet[:4] == struct.pack('!HH', 3, block)
                        received += packet[4:]
                        data.sendto(struct.pack('!HH', 4, block), client)
                        if len(packet) < 516:
                            break
                        block += 1
                    assert received == content
                else:
                    for index, offset in enumerate(range(0, size + 1, 512), 1):
                        packet = struct.pack('!HH', 3, index) + content[offset:offset + 512]
                        data.sendto(packet, client)
                        assert data.recvfrom(1024)[0] == struct.pack('!HH', 4, index)
                        # Duplicate DATA, including final DATA, must be re-ACKed.
                        data.sendto(packet, client)
                        assert data.recvfrom(1024)[0] == struct.pack('!HH', 4, index)
        with ThreadPoolExecutor() as pool:
            future = pool.submit(server)
            assert transfer('127.0.0.1', listener.getsockname()[1], 'file', target,
                            upload=upload, timeout=.1) == size
            future.result()
    assert target.read_bytes() == content


def test_timeout_preserves_destination(tmp_path):
    target = tmp_path / 'file'
    target.write_bytes(b'original')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(('127.0.0.1', 0))
        with pytest.raises(TimeoutError):
            transfer('127.0.0.1', listener.getsockname()[1], 'file', target, timeout=.03, retries=1)
    assert target.read_bytes() == b'original'
    assert not list(tmp_path.glob('.fluxftp-tftp-*'))


def test_cancel_before_io(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        transfer('127.0.0.1', 69, 'file', tmp_path / 'file', cancel=cancel)
    assert not (tmp_path / 'file').exists()
    assert not list(tmp_path.glob('.fluxftp-tftp-*'))


def test_accessible_dialog(qapp=None):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QAccessible
    from fluxftp.tftp_dialog import TftpDialog
    app = QApplication.instance() or QApplication([])
    dialog = TftpDialog()
    for field in (dialog.host, dialog.port, dialog.direction, dialog.remote, dialog.local):
        interface = QAccessible.queryAccessibleInterface(field)
        assert interface.text(QAccessible.Text.Name)
    dialog.begin()
    assert dialog.worker is None
    assert 'Enter' in dialog.status.text()
    dialog.close()
