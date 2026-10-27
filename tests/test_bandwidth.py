from datetime import datetime
from threading import Event, Thread
import time
from types import SimpleNamespace

import pytest
from fluxftp.bandwidth import DEFAULTS, effective, allocations, BandwidthManager, FairBudget, ManagedLimiter
from fluxftp.core import Cancelled, Connection
from test_cloud import service
from test_integration import servers


def test_daily_schedule_wraps_midnight_and_combined_limits():
    config = dict(DEFAULTS, enabled=True, total=100, scheduled=True, start='22:00', end='06:00', scheduled_total=20)
    assert effective(config, datetime(2026, 1, 1, 23))[0] == 20 * 1024
    assert effective(config, datetime(2026, 1, 1, 6))[0] == 100 * 1024
    config['end'] = '22:00'
    assert effective(config, datetime(2026, 1, 1, 12))[0] == 20 * 1024


@pytest.mark.parametrize('counts', [dict(native=[1, 0], cloud=[1, 0]), dict(native=[3, 2], cloud=[1, 7]), dict(native=[1, 1], cloud=[9999, 9999])])
def test_disjoint_budget_shares_cannot_exceed_caps(counts):
    rates = allocations((1024, 800, 600), counts)
    assert sum(sum(v) for v in rates.values()) <= 1024
    assert sum(v[0] for v in rates.values()) <= 800
    assert sum(v[1] for v in rates.values()) <= 600
    assert all(rates[p][d] > 0 for p in counts for d in range(2) if counts[p][d])


def test_native_and_real_backend_share_exact_byte_units(service):
    manager = BandwidthManager(service)
    manager.configure(dict(DEFAULTS, enabled=True, total=1024))
    with manager.lease('native', [0]):
        with manager.lease('cloud', [0]):
            assert manager.native[0].rate == 512 * 1024
            actual = service.call('core/bwlimit')
            assert actual['bytesPerSecondTx'] == 512 * 1024
        assert manager.native[0].rate == 1024 * 1024
    assert manager.counts == {'native': [0, 0], 'cloud': [0, 0]}


def test_fair_budget_limits_multiple_threads_and_cancels():
    budget = FairBudget()
    budget.configure(100 * 1024)
    started = time.monotonic()
    threads = [Thread(target=budget.consume, args=(30 * 1024, Event())) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(3)
    assert all(not thread.is_alive() for thread in threads)
    assert time.monotonic() - started >= .45
    cancel = Event(); cancel.set()
    with pytest.raises(Cancelled): budget.consume(1, cancel)
    assert not budget.queue


def test_failed_backend_update_stops_backend_and_releases_lease():
    closed = []
    service = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), close=lambda: closed.append(True))
    def fail(*args, **kwargs): raise OSError('backend failure')
    service.call = fail
    manager = BandwidthManager(service)
    manager.config = dict(DEFAULTS, enabled=True, total=100)
    with pytest.raises(OSError):
        with manager.lease('cloud', [0]): pass
    assert closed and manager.counts['cloud'] == [0, 0]


def test_global_limit_applies_despite_legacy_local_bypass(servers, tmp_path):
    root, site = servers()
    (root / 'file').write_bytes(b'x' * 32768)
    manager = BandwidthManager()
    manager.config = dict(DEFAULTS, enabled=True, download=64)
    with Connection(site, 'password') as connection:
        connection.bandwidth_manager = manager
        started = time.monotonic()
        connection.transfer(tmp_path / 'file', '/file', False, Event(), lambda *_: None)
        assert time.monotonic() - started >= .35
    assert manager.counts['native'] == [0, 0]
