"""Direct TFTP transfers, independent of browsable FTP workspaces."""
from threading import Event
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QSpinBox,
    QComboBox, QPushButton, QLabel, QFileDialog, QMessageBox)
from .tftp import transfer


class TransferWorker(QThread):
    progress = Signal(str)
    outcome = Signal(str)

    def __init__(self, parent, arguments):
        super().__init__(parent)
        self.arguments = arguments
        self.cancel = Event()

    def run(self):
        import time
        last = 0.0
        def report(count):
            nonlocal last
            now = time.monotonic()
            if now - last >= .1:
                self.progress.emit(f'{count:,} bytes transferred')
                last = now
        try:
            count = transfer(**self.arguments, cancel=self.cancel,
                             progress=report)
            self.outcome.emit(f'Completed: {count:,} bytes')
        except Exception as error:
            self.outcome.emit(str(error))


class TftpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('TFTP transfer')
        self.resize(530, 320)
        self.worker = None
        form = QFormLayout(self)
        notice = QLabel('TFTP sends files without encryption or authentication.\n'
                        'Binary files below 32 MiB; no browsing or resume.')
        form.addRow(notice)
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(69)
        self.direction = QComboBox()
        self.direction.addItems(['Download', 'Upload'])
        self.remote = QLineEdit()
        self.local = QLineEdit()
        for title, field in [('Server', self.host), ('UDP port', self.port),
                             ('Direction', self.direction), ('Remote filename', self.remote),
                             ('Local file', self.local)]:
            field.setAccessibleName(title)
            form.addRow(title, field)
        self.browse = QPushButton('Browse…')
        self.browse.clicked.connect(self.choose)
        form.addRow(self.browse)
        self.status = QLabel('Ready')
        self.status.setWordWrap(True)
        self.status.setAccessibleName('Transfer status')
        form.addRow(self.status)
        self.start = QPushButton('Start transfer')
        self.start.clicked.connect(self.begin)
        self.stop = QPushButton('Cancel transfer')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.cancel)
        form.addRow(self.start, self.stop)

    def choose(self):
        chooser = QFileDialog.getOpenFileName if self.direction.currentIndex() else QFileDialog.getSaveFileName
        path, _ = chooser(self, 'Local file', self.local.text())
        if path:
            self.local.setText(path)

    def begin(self):
        from pathlib import Path
        upload = bool(self.direction.currentIndex())
        if not all(field.text().strip() for field in (self.host, self.remote, self.local)):
            self.status.setText('Enter the server, remote filename, and local file.')
            return
        if not upload and Path(self.local.text()).exists():
            if QMessageBox.question(self, 'Replace local file', 'Replace the local file after a successful download?') != QMessageBox.StandardButton.Yes:
                return
        self.worker = TransferWorker(self, dict(host=self.host.text().strip(), port=self.port.value(),
            remote=self.remote.text(), local=self.local.text(), upload=upload))
        self.worker.progress.connect(self.status.setText)
        self.worker.outcome.connect(self.status.setText)
        self.worker.finished.connect(self.finished_transfer)
        self.busy(True)
        self.status.setText('Connecting…')
        self.worker.start()

    def busy(self, active):
        for widget in (self.host, self.port, self.direction, self.remote, self.local, self.browse, self.start):
            widget.setEnabled(not active)
        self.stop.setEnabled(active)

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()

    def finished_transfer(self):
        self.worker.deleteLater()
        self.worker = None
        self.busy(False)

    def reject(self):
        if self.worker:
            self.cancel()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.cancel()
            event.ignore()
        else:
            super().closeEvent(event)


def show_tftp(window):
    TftpDialog(window).exec()
