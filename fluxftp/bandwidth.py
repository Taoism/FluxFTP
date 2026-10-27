"""Coordinate native transfers and the cloud backend under one host budget."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
import threading
import time

from PySide6.QtCore import QObject, QTimer, QTime
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QCheckBox,
    QSpinBox, QTimeEdit, QDialogButtonBox, QLabel)

DEFAULTS = dict(enabled=False, total=0, upload=0, download=0, scheduled=False,
                start='09:00', end='18:00', scheduled_total=0, scheduled_upload=0, scheduled_download=0)


def effective(config, now=None):
    now = now or datetime.now()
    minute = now.hour * 60 + now.minute
    def minutes(value):
        hour, minute = map(int, value.split(':'))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59: raise ValueError('Invalid bandwidth schedule time.')
        return hour * 60 + minute
    start, end = minutes(config['start']), minutes(config['end'])
    scheduled = config['scheduled'] and (start == end or (start <= minute < end if start < end else minute >= start or minute < end))
    prefix = 'scheduled_' if scheduled else ''
    return tuple(config[prefix + key] * 1024 for key in ('total', 'upload', 'download')) if config['enabled'] else (0, 0, 0)


def allocations(limits, counts):
    """Disjoint engine/direction shares; their sum cannot exceed the total cap."""
    total, upload, download = limits
    direction_counts = [sum(counts[p][d] for p in counts) for d in range(2)]
    combined = sum(direction_counts)
    result = {pool: [0, 0] for pool in counts}
    for direction, cap in enumerate((upload, download)):
        count = direction_counts[direction]
        if total and combined:
            share = max(1, total * count // combined) if count else 0
            cap = min(cap, share) if cap else share
        if not count: continue
        remaining = cap
        active = [pool for pool in counts if counts[pool][direction]]
        for index, pool in enumerate(active):
            rate = remaining if index == len(active)-1 else max(1, min(remaining - (len(active)-index-1), cap * counts[pool][direction] // count)) if cap else 0
            result[pool][direction] = rate
            remaining -= rate
    return result


class FairBudget:
    """FIFO small quanta with live rate changes and cancellable waits."""
    def __init__(self):
        self.lock = threading.Lock()
        self.queue = deque()
        self.rate = 0
        self.next = 0.0

    def configure(self, rate):
        with self.lock: self.rate = max(0, rate)

    def consume(self, count, cancel):
        from .core import Cancelled
        while count > 0:
            token = object()
            with self.lock: self.queue.append(token)
            try:
                while True:
                    if cancel.is_set(): raise Cancelled('Cancelled')
                    with self.lock:
                        now = time.monotonic()
                        if not self.rate:
                            return
                        if self.queue[0] is token and now >= self.next:
                            quantum = min(count, 16384, max(1, self.rate // 10))
                            self.next = now + quantum / self.rate
                            count -= quantum
                            break
                    cancel.wait(.02)
            finally:
                with self.lock: self.queue.remove(token)


class ManagedLimiter:
    def __init__(self, manager, direction, legacy=None):
        self.manager, self.direction, self.legacy = manager, direction, legacy

    @property
    def rate(self):
        return self.manager.native[self.direction].rate if self.manager.config['enabled'] else getattr(self.legacy, 'rate', 0)

    def consume(self, count, cancel):
        limiter = self.manager.native[self.direction] if self.manager.config['enabled'] else self.legacy
        if limiter: limiter.consume(count, cancel)


class BandwidthManager:
    def __init__(self, service=None):
        self.service = service
        self.config = dict(DEFAULTS)
        self.lock = threading.RLock()
        self.counts = {'native': [0, 0], 'cloud': [0, 0]}
        self.native = [FairBudget(), FairBudget()]
        self.applied = {'native': [0, 0], 'cloud': [0, 0]}
        self.backend_process = None
        self.problem = ''
        self.legacy_cloud = (0, 0)

    def configure(self, config):
        with self.lock:
            self.config = dict(config)
            self.refresh()

    def refresh(self):
        with self.lock:
            rates = allocations(effective(self.config), self.counts)
            cloud = rates['cloud'] if self.config['enabled'] else list(self.legacy_cloud)
            if self.config['enabled']:
                total, upload, download = effective(self.config)
                for direction in range(2):
                    if not self.counts['cloud'][direction]:
                        caps = [n for n in (total, (upload, download)[direction]) if n]
                        cloud[direction] = min(caps) if caps else 0
            # Reduce native rates before asking the backend to increase its share.
            for direction in range(2):
                old, new = self.native[direction].rate, rates['native'][direction]
                if new and (not old or new < old): self.native[direction].configure(new)
            service = self.service
            running = service and getattr(service, 'process', None) and service.process.poll() is None
            if running and (cloud != self.applied['cloud'] or self.backend_process is not service.process):
                try:
                    service.call('core/bwlimit', {'rate': ':'.join(f'{n}B' if n else 'off' for n in cloud)})
                    self.backend_process = service.process
                except Exception:
                    self.problem = 'Cloud bandwidth update failed; cloud backend stopped to preserve the shared limit.'
                    service.close()
                    raise OSError(self.problem) from None
            for direction in range(2): self.native[direction].configure(rates['native'][direction])
            self.applied = {'native': rates['native'], 'cloud': list(cloud)}
            self.problem = ''

    @contextmanager
    def lease(self, pool, directions):
        with self.lock:
            for direction in directions: self.counts[pool][direction] += 1
            try: self.refresh()
            except BaseException:
                for direction in directions: self.counts[pool][direction] -= 1
                raise
        try: yield
        finally:
            with self.lock:
                for direction in directions: self.counts[pool][direction] -= 1
                try: self.refresh()
                except OSError: pass  # The stopped backend makes its active jobs fail.


def native_transfer(function):
    @wraps(function)
    def wrapped(self, local, remote, upload, cancel, progress, resume=False):
        manager = getattr(self, 'bandwidth_manager', None)
        if not manager: return function(self, local, remote, upload, cancel, progress, resume)
        directions = [0, 1] if upload and resume and self.transfer_options.ascii(remote) else [0 if upload else 1]
        with manager.lease('native', directions):
            return function(self, local, remote, upload, cancel, progress, resume)
    return wrapped


class BandwidthControl(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.worker, self.key = window, None, None
        self.manager = BandwidthManager(window.cloud_service)
        window.bandwidth_manager = self.manager
        window.cloud_service.bandwidth_manager = self.manager
        self.manager.config = self.settings()
        self.key = (tuple(self.manager.config.items()), effective(self.manager.config))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(2000)

    def settings(self):
        return {key: self.window.preferences.value('Bandwidth/' + key, value, type=type(value)) for key, value in DEFAULTS.items()}

    def tick(self):
        if self.worker: return
        config = self.settings()
        key = (tuple(config.items()), effective(config))
        if key == self.key: return
        self.key = key
        def done(_): self.worker = None
        def failed(error):
            self.worker = None
            self.window.log.appendPlainText('Bandwidth management: ' + str(error))
        self.worker = self.window.run(lambda _: self.manager.configure(config), done, failed, autostart=False)
        self.worker.start()

    def show(self):
        dialog = QDialog(self.window)
        dialog.setWindowTitle('Unified Bandwidth Management')
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        controls = {}
        for key, title in (('enabled', 'Use shared limits across FTP, SFTP and cloud'),
                           ('total', 'Combined upload + download'), ('upload', 'Upload ceiling'), ('download', 'Download ceiling'),
                           ('scheduled', 'Use daily scheduled limits'), ('start', 'Schedule starts'), ('end', 'Schedule ends'),
                           ('scheduled_total', 'Scheduled combined limit'), ('scheduled_upload', 'Scheduled upload ceiling'),
                           ('scheduled_download', 'Scheduled download ceiling')):
            value = self.settings()[key]
            if isinstance(value, bool):
                control = QCheckBox(title)
                control.setChecked(value)
                form.addRow(control)
            elif isinstance(value, int):
                control = QSpinBox()
                control.setRange(0, 2_000_000)
                control.setSuffix(' KiB/s')
                control.setSpecialValueText('Unlimited')
                control.setValue(value)
                form.addRow(title, control)
            else:
                control = QTimeEdit(QTime.fromString(value, 'HH:mm'))
                control.setDisplayFormat('HH:mm')
                form.addRow(title, control)
            from .accessibility import name_control
            name_control(control, title)
            controls[key] = control
        layout.addLayout(form)
        note = QLabel('Shared limits replace legacy per-site speed limits and local-network bypass while enabled. '
            'Native and cloud engines share the budget according to active transfers. Limits are average payload rates; '
            'protocol overhead and brief buffered bursts are additional. Direct FXP and provider-side copies do not pass through this computer. '
            'Schedules use local time every day; equal start/end means all day.')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            for key, control in controls.items():
                value = control.isChecked() if isinstance(control, QCheckBox) else control.value() if isinstance(control, QSpinBox) else control.time().toString('HH:mm')
                self.window.preferences.setValue('Bandwidth/' + key, value)
            self.tick()
        dialog.deleteLater()
