"""Background execution helper shared by desktop workflows."""
import time
from threading import Event

from PySide6.QtCore import QThread, Signal
from . import diagnostics


class Worker(QThread):
    pause_changed = Signal(bool)
    result = Signal(object)
    failed = Signal(object)
    file_started = Signal(str, str)
    progress = Signal(int, int, object, object)  # byte counts may exceed 32 bits

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel = Event()
        from .transfer_control import TransferControl
        self.transfer_control = TransferControl()
        self.last = 0.0
        self.started_at = time.monotonic()
        self.stream_speed = None
        self.stream_bytes = self.file_bytes = 0

    def begin_file(self):
        from .transfer_speed import RollingSpeed
        if self.stream_speed is None:
            self.stream_speed = RollingSpeed()
            self.stream_speed.sample(0)
        self.stream_bytes += self.file_bytes
        self.file_bytes = 0
        self.last = 0

    def report(self, done, total):
        self.transfer_control.checkpoint(self.cancel, self.pause_changed.emit)
        now = time.monotonic()
        if now - self.last > .15 or done == total:
            self.last = now
            self.file_bytes = done
            rate = (self.stream_speed.sample(self.stream_bytes + done) / 1024 if self.stream_speed else
                    done / max(.1, now - self.started_at) / 1024)
            self.progress.emit(int(100 * done / total) if total else 100,
                               min(2147483647, int(rate)),
                               self.stream_bytes + done if self.stream_speed else done,
                               0 if self.stream_speed else total)

    def run(self):
        try:
            self.result.emit(self.function(self))
        except Exception as e:
            diagnostics.worker_failure(e)
            self.failed.emit(e)


