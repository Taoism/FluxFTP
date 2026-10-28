"""Queue state and scheduling policy, independent of Qt and protocol workers.

The desktop adapter supplies settings snapshots and launches workers. All methods
are serialized by that adapter on its owner thread. Workspace values are opaque
identity tokens; the engine never calls widgets or reads presentation rows.
"""
from dataclasses import dataclass
import os
import posixpath
import time
import uuid

from .priority_rules import DEFAULT_RULES, priority_rank


@dataclass(frozen=True)
class QueuePolicy:
    single_connection: bool = False
    per_tab: int = 10
    priority_rules: str = DEFAULT_RULES
    cycle_retry: bool = True
    retry_after: bool = True
    retry_upload: int = 2
    retry_download: int = 5
    retry_fxp: int = 0


@dataclass(frozen=True)
class QueueEvent:
    kind: str
    job: object
    detail: object = None


def site_key(site):
    return None if site is None else (site.host.casefold(), site.port, site.username.casefold(), site.protocol)


def schedule_key(job, rules=DEFAULT_RULES):
    order = job.get('manual_order')
    return (0, order) if order is not None else (1, priority_rank(job.get('name', ''), rules))


class QueueEngine:
    def __init__(self, *, clock=time.time, monotonic=time.monotonic):
        self.jobs, self.pending, self.active = [], [], {}
        self.paused = self.batch_failed = False
        self.clock, self.monotonic = clock, monotonic
        self.listeners = []

    def subscribe(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback) if callback in self.listeners else None

    def emit(self, kind, job=None, detail=None):
        event = QueueEvent(kind, job, detail)
        for callback in tuple(self.listeners): callback(event)

    @staticmethod
    def transfer_record(job):
        source, destination = job['source_site'], job['destination_site']
        sites = {site_key(site): site for site in (source, destination) if site is not None}
        route = job['route']
        return dict(workspace=job['workspace'], route=route, site_keys=set(sites),
            download_keys={site_key(source)} if route in ('Download', 'FXP', 'Relay') and source else set(),
            upload_keys={site_key(destination)} if route in ('Upload', 'FXP', 'Relay') and destination else set(),
            single_keys={key for key, site in sites.items() if site.single_connection},
            site_total_limits={key: site.max_transfers_total for key, site in sites.items()},
            download_limits={key: site.max_downloads for key, site in sites.items()},
            upload_limits={key: site.max_uploads for key, site in sites.items()},
            max_total=min((site.max_transfers_total for site in sites.values()), default=6))

    def can_start(self, job, policy):
        if job.get('buffered') or job.get('held') or job.get('editing'): return False
        if job.get('scheduled_at') is not None and job['scheduled_at'] > self.clock(): return False
        if job.get('dst'):
            remote = job.get('destination_site')
            target = posixpath.normpath(job['dst']) if remote else os.path.normcase(os.path.abspath(job['dst']))
            separator = '/' if remote else os.sep
            for record in self.active.values():
                other = record.get('job', {})
                if not other.get('dst') or site_key(remote) != site_key(other.get('destination_site')): continue
                other_target = posixpath.normpath(other['dst']) if remote else os.path.normcase(os.path.abspath(other['dst']))
                if (target == other_target or
                    (job.get('directory') and other_target.startswith(target.rstrip(separator) + separator)) or
                    (other.get('directory') and target.startswith(other_target.rstrip(separator) + separator))): return False
        if policy.single_connection and self.active: return False
        candidate = self.transfer_record(job)
        active = [record for record in self.active.values() if record['workspace'] is candidate['workspace']]
        if len(active) >= policy.per_tab: return False
        if len(active) >= min([candidate['max_total'], *(r['max_total'] for r in active)]): return False
        for record in active:
            if (candidate['single_keys'] | record['single_keys']) & (candidate['site_keys'] & record['site_keys']): return False
        for limits, keys in (('site_total_limits', 'site_keys'), ('download_limits', 'download_keys'), ('upload_limits', 'upload_keys')):
            for key, limit in candidate[limits].items():
                if sum(key in record[keys] for record in active) >= limit: return False
        return True

    def next_index(self, policy):
        if self.paused or any(r.get('job', {}).get('deployment') for r in self.active.values()): return None
        return min((i for i, job in enumerate(self.pending) if self.can_start(job, policy) and not (job.get('deployment') and self.active)),
            key=lambda i: schedule_key(self.pending[i], getattr(self.pending[i].get('options'), 'priority_rules', policy.priority_rules)), default=None)

    def begin(self, job):
        if job not in self.jobs: self.jobs.append(job)
        job['state'] = 'active'
        job.pop('transfer_outcome', None)
        job.pop('actual_dst', None)
        job['transfer_started'] = self.monotonic()
        job['batch_active'] = True
        self.emit('started', job)

    def register(self, record, transfer_id=None):
        transfer_id = transfer_id or uuid.uuid4().hex
        if transfer_id in self.active: raise ValueError('Duplicate active transfer ID')
        self.active[transfer_id] = record
        return transfer_id

    def release(self, transfer_id):
        record = self.active.get(transfer_id)
        if record and record.get('handling_failure'):
            record['finish_deferred'] = True
            return None
        return self.active.pop(transfer_id, None)

    @staticmethod
    def retry_limit(job, policy):
        route = {'Copy': 'download', 'Relay': 'fxp'}.get(job['route'], job['route'].lower())
        return max(0, min(999, getattr(policy, 'retry_' + route)))

    def retry(self, job, policy):
        if job.get('watch') or job.get('deployment') or job.get('crc_job') or job.get('suppress_retry'): return False
        limit = self.retry_limit(job, policy)
        if job.get('retries', 0) >= limit: return False
        if job in self.pending and job.get('state') != 'failed': return False
        job.update(retries=job.get('retries', 0) + 1, state='queued', batch_active=True)
        if job not in self.pending: self.pending.append(job)
        self.emit('retry', job, limit)
        return True

    def settle(self, job, policy):
        if job and job.get('state') == 'queued' and job not in self.pending: self.pending.append(job)
        if job and job.get('state') == 'failed' and policy.cycle_retry: self.retry(job, policy)
        if not self.active and not self.pending and policy.retry_after:
            for failed in self.jobs:
                if failed.get('state') == 'failed': self.retry(failed, policy)
        self.batch_failed = any(j.get('batch_active', True) and j.get('state') in ('failed', 'cancelled') for j in self.jobs)
        self.emit('finished', job)

    def reset_failed(self):
        for job in self.jobs:
            if job.get('state') == 'failed':
                job.update(state='queued', retries=0, crc_attempt=0, suppress_retry=False, batch_active=True)
                if job not in self.pending: self.pending.append(job)
                self.emit('reset', job)

    def cancel(self, jobs=None):
        """Cancel queued work and return active records for the adapter to signal."""
        chosen = None if jobs is None else {id(job) for job in jobs}
        matches = lambda job: chosen is None or id(job) in chosen
        active = [r for r in self.active.values() if chosen is None or matches(r.get('job'))]
        pending = [j for j in self.pending if matches(j)]
        if active or pending or chosen: self.batch_failed = True
        for job in pending:
            job.update(state='cancelled', suppress_retry=True)
            self.emit('cancelled', job)
        self.pending[:] = [j for j in self.pending if not matches(j)]
        for record in active:
            if record.get('job') is not None: record['job']['suppress_retry'] = True
        for job in self.jobs:
            if matches(job) and job.get('state') == 'failed':
                job['suppress_retry'] = True
                if chosen is not None: job['state'] = 'cancelled'
        return active
