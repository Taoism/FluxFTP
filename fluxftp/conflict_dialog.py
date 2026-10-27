"""Serialize worker conflict requests on the Qt GUI thread."""
from collections import deque
from dataclasses import dataclass, field
from threading import Event

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout


@dataclass
class Request:
    details: dict
    cancel: Event
    ready: Event = field(default_factory=Event)
    action: str = ''


class ConflictDialog(QDialog):
    def __init__(self, parent, details):
        super().__init__(parent)
        self.setWindowTitle('File already exists')
        self.resize(610, 260)
        self.action = ''
        layout = QVBoxLayout(self)
        label = QLabel(f"Source: {details['src']}\nSize: {details['source_size']:,} bytes\n\n"
                       f"Destination: {details['dst']}\nSize: {details['target_size']:,} bytes")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        hint = QLabel('Rename keeps both files using a numbered name. Resume assumes the existing prefix matches the source.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.remaining = QCheckBox('Apply this choice to remaining conflicts in this queue run')
        layout.addWidget(self.remaining)
        buttons = QDialogButtonBox()
        self.actions = {}
        for name in ('Overwrite', 'Resume', 'Rename', 'Skip'):
            button = buttons.addButton(name, QDialogButtonBox.ButtonRole.ActionRole)
            button.clicked.connect(lambda _=False, action='Auto ' + name: self.choose(action))
            self.actions[name] = button
        self.actions['Resume'].setEnabled(details['can_resume'])
        if not details['can_resume']:
            self.actions['Resume'].setToolTip('Resume is unavailable for this destination or file size.')
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel).clicked.connect(self.reject)
        layout.addWidget(buttons)

    def choose(self, action):
        self.action = action
        self.accept()


class ConflictController(QObject):
    requested = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.waiting = deque()
        self.current = None
        self.dialog = None
        self.choice = ''
        self.requested.connect(self.enqueue, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.check_cancelled)

    def ask(self, details, cancel):
        from .core import Cancelled
        request = Request(details, cancel)
        self.requested.emit(request)
        while not request.ready.wait(.1):
            if cancel.is_set():
                raise Cancelled('Cancelled while waiting for a conflict choice')
        if cancel.is_set() or not request.action:
            raise Cancelled('File conflict cancelled')
        return request.action

    def enqueue(self, request):
        self.waiting.append(request)
        self.show_next()

    def show_next(self):
        if self.current:
            return
        while self.waiting:
            request = self.waiting.popleft()
            if request.cancel.is_set():
                request.ready.set()
                continue
            if self.choice and (self.choice != 'Auto Resume' or request.details['can_resume']):
                request.action = self.choice
                request.ready.set()
                continue
            self.current = request
            self.dialog = ConflictDialog(self.window, request.details)
            self.dialog.finished.connect(self.finish)
            self.dialog.open()
            self.timer.start()
            return

    def finish(self, result):
        request, dialog = self.current, self.dialog
        if request is None:
            return
        if result == QDialog.DialogCode.Accepted and not request.cancel.is_set():
            request.action = dialog.action
            if dialog.remaining.isChecked():
                self.choice = dialog.action
        request.ready.set()
        self.current = self.dialog = None
        self.timer.stop()
        dialog.deleteLater()
        QTimer.singleShot(0, self.show_next)

    def check_cancelled(self):
        if self.current and self.current.cancel.is_set():
            self.dialog.reject()

    def reset(self):
        self.choice = ''
