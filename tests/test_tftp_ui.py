import socket
import time
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from fluxftp.tftp_dialog import TftpDialog


def test_keyboard_navigation_and_cancel_responsiveness(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = TftpDialog()
    dialog.show()
    dialog.activateWindow()
    QTest.qWait(50)
    dialog.host.setFocus()
    QTest.keyClicks(dialog.host, '127.0.0.1')
    QTest.keyClick(dialog.host, Qt.Key.Key_Tab)
    assert dialog.port.hasFocus() or dialog.port.lineEdit().hasFocus()
    dialog.remote.setText('test')
    dialog.local.setText(str(tmp_path / 'download'))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as endpoint:
        endpoint.bind(('127.0.0.1', 0))
        dialog.port.setValue(endpoint.getsockname()[1])
        dialog.begin()
        assert not dialog.start.isEnabled() and dialog.stop.isEnabled()
        QTest.qWait(50)
        start = time.monotonic()
        QTest.mouseClick(dialog.stop, Qt.MouseButton.LeftButton)
        while dialog.worker is not None and time.monotonic() - start < 2:
            QTest.qWait(10)
        assert dialog.worker is None
        assert dialog.start.isEnabled()
        assert 'cancelled' in dialog.status.text()
    dialog.close()
