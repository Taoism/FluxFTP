"""Qt binding and temporary mapping adapter for the independent job model.

Existing queue consumers can migrate incrementally from mapping keys to models.
Widgets, credentials, callbacks and disk handles never enter JobModel.
"""
from collections.abc import MutableMapping
from PySide6.QtCore import QObject, Signal, Slot, Qt
from shiboken6 import isValid

from .job_model import JobModel, RUNTIME_FIELDS, queue_descriptor
from .queue_tree import STATE_ROLE, JOB_ID_ROLE


class JobView(QObject):
    changed = Signal(object)

    def __init__(self, window, model, row, workspace):
        super().__init__()
        self.window, self.model, self.row, self.workspace = window, model, row, workspace
        self.closed = False
        self.changed.connect(self.render)
        self.unsubscribe = model.subscribe(self.changed.emit)
        row.setData(0, JOB_ID_ROLE, model.job_id)
        row.setData(0, STATE_ROLE, model.state.value)

    @Slot(object)
    def render(self, event):
        if self.closed or self.row is None or not isValid(self.row): return
        if event.field == 'state':
            self.row.setData(0, STATE_ROLE, event.value.value)
        elif event.field == 'size':
            self.row.setData(4, Qt.ItemDataRole.UserRole, event.value)
        elif event.field == 'progress':
            self.window.transfer_display_progress(self.row, *event.value)

    def close(self):
        self.unsubscribe()
        self.closed = True
        self.deleteLater()


class QueueJob(MutableMapping):
    def __init__(self, window, values):
        values = dict(values)
        row, workspace = values.pop('row'), values.pop('workspace')
        values.setdefault('name', row.text(0))
        self.runtime = {key: values.pop(key) for key in RUNTIME_FIELDS if key in values}
        values.setdefault('size', row.data(4, Qt.ItemDataRole.UserRole))
        self.model = JobModel(identity=values.pop('job_id', None), state=values.pop('state', 'queued'), **values)
        self.view = JobView(window, self.model, row, workspace)

    def __getitem__(self, key):
        if key in ('row', 'workspace'): return getattr(self.view, key)
        if key in RUNTIME_FIELDS: return self.runtime[key]
        return self.model.get(key)

    def __setitem__(self, key, value):
        if key in ('row', 'workspace'): setattr(self.view, key, value)
        elif key in RUNTIME_FIELDS: self.runtime[key] = value
        else: self.model.set(key, value)

    def __delitem__(self, key):
        if key in ('row', 'workspace'): raise ValueError('Queue view bindings are required.')
        if key in RUNTIME_FIELDS: del self.runtime[key]
        else: self.model.delete(key)

    def __iter__(self):
        yield from self.model.values()
        yield from self.runtime
        yield from ('row', 'workspace')

    def __len__(self):
        return len(self.model.values()) + len(self.runtime) + 2

    def __eq__(self, other):
        return self is other


def snapshot(window, job):
    if isinstance(job, QueueJob):
        return queue_descriptor(job.model, max(0, window.tabs.indexOf(job.view.workspace)))
    # Compatibility for older extensions/tests that still create plain dictionaries.
    from .job_model import job_id
    job.setdefault('job_id', job_id())
    values = dict(job, size=job['row'].data(4, Qt.ItemDataRole.UserRole))
    return queue_descriptor(values, max(0, window.tabs.indexOf(job['workspace'])))
