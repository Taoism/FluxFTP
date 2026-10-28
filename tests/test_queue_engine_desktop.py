from test_queue import window, job_for
from test_ui import settle


def test_desktop_uses_engine_state_and_events(window):
    engine = window.queue_engine
    assert window.pending is engine.pending
    assert window.active is engine.active
    assert window.queue_jobs is engine.jobs
    events = []
    engine.subscribe(lambda event: events.append((event.kind, event.job)))
    attempts = []
    def transfer(_):
        attempts.append(True)
        if len(attempts) == 1: raise OSError('Retry this attempt')
    job = job_for(window, transfer)
    identity = job['job_id']
    window.start_next()
    settle(window)
    assert len(attempts) == 2 and job['state'] == 'complete'
    assert job['job_id'] == identity
    assert [kind for kind, current in events if current is job] == ['started', 'retry', 'finished', 'started', 'finished']
    assert not engine.active and not engine.pending and not engine.batch_failed


def test_selection_cancellation_keeps_other_engine_jobs(window):
    selected = job_for(window, lambda _: None, name='selected')
    other = job_for(window, lambda _: None, name='other')
    window.paused = True
    selected['row'].setSelected(True)
    window.cancel_selected()
    assert window.queue_engine.pending == [other]
    assert selected['state'] == 'cancelled' and other['state'] == 'queued'
    assert window.queue_engine.paused
