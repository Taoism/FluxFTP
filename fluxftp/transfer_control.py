"""Cooperative payload pause; cancellation remains responsive while paused."""
from threading import Event


class TransferControl:
    def __init__(self):
        self.requested = Event()

    def checkpoint(self, cancel, notify):
        if not self.requested.is_set():
            return
        notify(True)
        try:
            while self.requested.is_set():
                if cancel.wait(.1):
                    from .core import Cancelled
                    raise Cancelled('Cancelled while paused')
        finally:
            notify(False)


def can_pause(job):
    if job.get('state') == 'queued': return not job.get('editing')
    if job.get('state') != 'active' or job.get('editing') or job.get('deployment'): return False
    from .protocol_adapter import supports_payload_pause
    return supports_payload_pause(job)


def pause_jobs(window, jobs):
    for job in jobs:
        if not can_pause(job):
            continue
        job['held'] = True
        record = next((r for r in window.active.values() if r.get('job') is job), None)
        if record:
            record['worker'].transfer_control.requested.set()
            job['row'].setText(7, f"{job['route']} · Pause requested; waiting for payload checkpoint")
        else:
            job['row'].setText(7, f"{job['route']} · Held")
    window.save_queue_state()


def resume_active(window, jobs):
    chosen = {id(j) for j in jobs}
    for record in window.active.values():
        job = record.get('job')
        if job and id(job) in chosen:
            job['held'] = False
            record['worker'].transfer_control.requested.clear()
            job['row'].setText(7, f"{job['route']} · Resuming")
