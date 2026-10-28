"""UI-independent transfer identity, state, data and change notifications."""
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import re
import uuid


class JobState(str, Enum):
    QUEUED = 'queued'
    ACTIVE = 'active'
    COMPLETE = 'complete'
    FAILED = 'failed'
    CANCELLED = 'cancelled'

    def __str__(self):
        return self.value


RUNTIME_FIELDS = frozenset({'row', 'workspace', 'task', 'buffer',
                            'source_password', 'destination_password'})


def job_id(value=None):
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Invalid queue job ID.')
    return value


@dataclass(frozen=True)
class JobChange:
    job_id: str
    field: str
    value: object


class JobModel:
    def __init__(self, *, identity=None, state=JobState.QUEUED, **fields):
        if RUNTIME_FIELDS.intersection(fields):
            raise ValueError('Job data cannot contain UI bindings, tasks, buffers or credentials.')
        self._id = job_id(identity)
        self._state = JobState(state)
        self._fields = dict(fields)
        self._listeners = []

    @property
    def job_id(self):
        return self._id

    @property
    def state(self):
        return self._state

    def subscribe(self, listener):
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def get(self, field):
        if field == 'job_id': return self._id
        if field == 'state': return self._state
        return self._fields[field]

    def set(self, field, value):
        if field == 'job_id':
            if value != self._id: raise ValueError('A queue job ID cannot change.')
            return
        if field in RUNTIME_FIELDS:
            raise ValueError('Runtime values do not belong in job data.')
        if field == 'state':
            value = JobState(value)
            if value == self._state: return
            self._state = value
        else:
            self._fields[field] = value
        event = JobChange(self._id, field, value)
        for listener in tuple(self._listeners): listener(event)

    def delete(self, field):
        if field in ('job_id', 'state'): raise ValueError('Job identity and state are required.')
        del self._fields[field]

    def values(self):
        return dict(self._fields, job_id=self._id, state=self._state)


def queue_descriptor(values, workspace):
    """One credential-free schema shared by recovery, export and async snapshots."""
    if isinstance(values, JobModel): values = values.values()
    item = {key: values[key] for key in ('src', 'dst', 'name', 'directory', 'resume', 'state')}
    item.update(job_id=job_id(values.get('job_id')), workspace=workspace,
        retries=values.get('retries', 0), size=values.get('size'), held=values.get('held', False),
        automatic=values.get('automatic', False), manual_order=values.get('manual_order'),
        scheduled_at=values.get('scheduled_at'), source_side=values.get('source_side', 1),
        destination_side=values.get('destination_side', 2), crc_job=values.get('crc_job', False),
        crc_attempt=values.get('crc_attempt', 0), crc_root=values.get('crc_root', ''))
    for key in ('source_site', 'destination_site'):
        site = values.get(key)
        item[key] = asdict(site) if is_dataclass(site) else site
    for key in ('queue_group', 'sync', 'watch', 'deployment'):
        if values.get(key): item[key] = values[key]
    return item
