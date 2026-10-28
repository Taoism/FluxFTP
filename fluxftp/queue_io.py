"""Worker I/O with GUI-owned snapshots and serialized recovery-file writes."""
from .queue_storage import StorageSnapshot, import_file
import json
from copy import deepcopy
from dataclasses import asdict
from PySide6.QtCore import QObject, QEventLoop, Qt, QTimer, Signal
from PySide6.QtWidgets import QProgressDialog, QApplication
from .core import Cancelled


def checked(cancel):
    if cancel.is_set(): raise Cancelled('Queue operation cancelled')


def progress_parent(window):
    active = QApplication.activeModalWidget()
    ancestor = active
    while ancestor is not None:
        if ancestor is window: return active
        ancestor = ancestor.parentWidget()
    return window


def background(window, label, function, cancelable=True):
    """Keep Qt pumping while a worker performs an explicit queue operation."""
    dialog = QProgressDialog(label, 'Cancel' if cancelable else '', 0, 0, progress_parent(window))
    if not cancelable: dialog.setCancelButton(None)
    dialog.setWindowTitle('Transfer queue')
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    loop, result, errors = QEventLoop(), [], []
    window._queue_operation_depth = getattr(window, '_queue_operation_depth', 0) + 1
    worker = window.run(function, result.append, errors.append, raw_failure=True, autostart=False)
    dialog.canceled.connect(worker.cancel.set)
    worker.finished.connect(loop.quit)
    dialog.show()
    worker.start()
    loop.exec()
    window._queue_operation_depth -= 1
    dialog.close()
    dialog.deleteLater()
    QTimer.singleShot(0, window.start_next)
    if errors: raise errors[0]
    return result[0] if result else None


class Snapshot(StorageSnapshot):
    def __init__(self, window, include_deferred=True):
        super().__init__()
        deferred = tuple(getattr(window, 'deferred_queue', ())) if include_deferred else ()
        if deferred:
            self.add_items(deferred)
        keep_failed = window.preferences.value('Queue/keep_failed', True, type=bool)
        try:
            for job in sorted(window.queue_jobs, key=lambda job: bool(job.get('buffered'))):
                if job.get('buffered'):
                    if job.get('state') != 'queued': continue
                    overrides = dict(workspace=max(0, window.tabs.indexOf(job['workspace'])),
                                     queue_group=dict(job['queue_group']))
                    for key in ('manual_order', 'scheduled_at'):
                        if job.get(key) is not None: overrides[key] = job[key]
                    if job.get('scheduled_at') is None and job.get('buffer_unschedule'):
                        overrides['scheduled_at'] = None
                    if job.get('held'): overrides['held'] = True
                    elif job.get('buffer_release'): overrides['held'] = False
                    self.add_buffer(job['buffer'], overrides)
                elif 'src' in job and job.get('state') in ('queued', 'active', 'failed'):
                    if job['state'] == 'failed' and not keep_failed: continue
                    from .queue_job import snapshot
                    item = deepcopy(snapshot(window, job))
                    self.add_items((item,))
        except Exception:
            self.close()
            raise


class QueueWriter(QObject):
    idle = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.worker = None
        self.pending = None
        self.error = None

    def save(self):
        snapshot = Snapshot(self.window)
        if self.pending: self.pending.close()
        self.pending = snapshot
        self.pump()

    def pump(self):
        if self.worker or self.pending is None: return
        from .queue_manager import write_queue
        snapshot, self.pending = self.pending, None
        path = self.window.queue_file
        def write(worker):
            try:
                write_queue(path, snapshot, cancel=worker.cancel)
            finally:
                snapshot.close()
        self.worker = self.window.run(write, self.saved, self.failed, autostart=False)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def saved(self, _):
        self.error = None

    def failed(self, message):
        self.error = str(message)
        self.window.log.appendPlainText('Cannot save queue: ' + self.error)

    def finished(self):
        self.worker = None
        if self.pending is not None: self.pump()
        else: self.idle.emit()

    def flush(self):
        if self.worker or self.pending is not None:
            dialog = QProgressDialog('Saving recovery queue…', '', 0, 0, progress_parent(self.window))
            dialog.setCancelButton(None)
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.setMinimumDuration(0)
            loop = QEventLoop()
            self.idle.connect(loop.quit)
            self.window._queue_operation_depth = getattr(self.window, '_queue_operation_depth', 0) + 1
            dialog.show()
            loop.exec()
            self.window._queue_operation_depth -= 1
            self.idle.disconnect(loop.quit)
            dialog.close()
            dialog.deleteLater()
            QTimer.singleShot(0, self.window.start_next)
        return self.error is None


def writer(window):
    if not hasattr(window, '_queue_writer'): window._queue_writer = QueueWriter(window)
    return window._queue_writer


def persist(window):
    """An explicit recovery choice persists even with automatic saving off."""
    output = writer(window)
    output.save()
    if not output.flush(): raise OSError(output.error)


def export(window, path):
    from pathlib import Path
    from .queue_manager import write_queue
    if hasattr(window, 'queue_file') and Path(path).resolve() == window.queue_file.resolve():
        raise ValueError('Select an exported queue list; the active recovery queue is managed automatically.')
    snapshot = Snapshot(window)
    try:
        background(window, 'Exporting queue…', lambda worker: write_queue(path, snapshot, worker.cancel))
    finally:
        snapshot.close()
