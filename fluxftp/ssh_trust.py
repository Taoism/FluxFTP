"""First-use SSH trust requires an independently supplied SHA-256 fingerprint."""
import base64
import hashlib
import os
import tempfile
import time
from pathlib import Path
from threading import Event, RLock

import paramiko
from PySide6.QtCore import QObject, Signal, Qt, QTimer, QThread
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QDialogButtonBox

from .trust_service import fingerprint, remember, VerifiedPolicy








class TrustDialog(QDialog):
    def __init__(self, parent, host, key):
        super().__init__(parent)
        self.setWindowTitle('Verify SSH server identity')
        self.resize(650, 290)
        rows = QVBoxLayout(self)
        self.expected = fingerprint(key)
        details = QLabel(f'Server: {host}\nKey type: {key.get_name()}\nPresented fingerprint:\n{self.expected}')
        details.setTextFormat(Qt.TextFormat.PlainText)
        details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.setWordWrap(True)
        rows.addWidget(details)
        note = QLabel('Get the SHA-256 fingerprint from your server administrator through a separate trusted channel, then enter it below. A match saves this host key for future connections.')
        note.setWordWrap(True)
        rows.addWidget(note)
        self.confirmation = QLineEdit()
        self.confirmation.setPlaceholderText('SHA256: fingerprint supplied by your administrator')
        self.confirmation.setAccessibleName('Independently verified SSH fingerprint')
        rows.addWidget(self.confirmation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.trust = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.trust.setText('Trust and connect')
        self.trust.setEnabled(False)
        self.confirmation.textChanged.connect(lambda text: self.trust.setEnabled(text.strip() == self.expected))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        rows.addWidget(buttons)


class SSHTrust(QObject):
    requested = Signal(object)

    def __init__(self, window, path):
        super().__init__(window)
        self.window, self.path = window, Path(path)
        self.pending = []
        self.current = self.dialog = None
        self.requested.connect(self.enqueue, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.expire)

    def verify(self, host, key):
        if QThread.currentThread() == self.thread():
            dialog = TrustDialog(self.window, host, key)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted and dialog.confirmation.text().strip() == fingerprint(key)
            dialog.deleteLater()
            return accepted
        cancel = getattr(QThread.currentThread(), 'cancel', Event())
        request = dict(host=host, key=key, ready=Event(), accepted=False, deadline=time.monotonic() + 300)
        self.requested.emit(request)
        while not request['ready'].wait(.1):
            if cancel.is_set() or time.monotonic() >= request['deadline']:
                request['ready'].set()
                return False
        return request['accepted']

    def enqueue(self, request):
        self.pending.append(request)
        self.next()

    def next(self):
        if self.current: return
        while self.pending:
            request = self.pending.pop(0)
            if request['ready'].is_set(): continue
            self.current = request
            self.dialog = TrustDialog(self.window, request['host'], request['key'])
            self.dialog.finished.connect(self.finish)
            self.dialog.open()
            self.timer.start()
            return

    def finish(self, result):
        request, dialog = self.current, self.dialog
        if request is None: return
        request['accepted'] = (not request['ready'].is_set() and result == QDialog.DialogCode.Accepted
                               and dialog.confirmation.text().strip() == fingerprint(request['key']))
        request['ready'].set()
        self.current = self.dialog = None
        self.timer.stop()
        dialog.deleteLater()
        QTimer.singleShot(0, self.next)

    def expire(self):
        if self.current and (self.current['ready'].is_set() or time.monotonic() >= self.current['deadline']):
            self.dialog.reject()
