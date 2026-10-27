import json
import os
import time
from threading import Event
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from fluxftp.queue_buffer import BUFFER_LIMIT
from fluxftp.queue_manager import read_queue, write_queue
from fluxftp.queue_filter import search_page
from test_queue import window
from test_queue_buffer import buffered_folder


def test_100001_files_bounded_ui_search_recovery_cancel(window, tmp_path):
    import ctypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in
            ('peak', 'working', 'peak_pool', 'pool', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
    def rss():
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        assert ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return counters.working
    before = rss()
    window.paused = True
    started = time.monotonic()
    parent = buffered_folder(window, tmp_path, 100001)
    planned = time.monotonic() - started
    assert len(window.queue_jobs) <= BUFFER_LIMIT + 1
    buffered_rss = rss() - before
    window.show()
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    for n in range(15):
        window.queue.verticalScrollBar().setValue(n * 100)
        QTest.qWait(20)
    timer.stop()
    assert len(ticks) >= 10
    parent['buffer'].db.commit()
    found, more = search_page([], [(parent['buffer'].path, {})], '100000.bin', 'All', 0, Event())
    assert len(found) == 1 and not more
    saved = tmp_path / '100k.json'
    start = time.monotonic()
    write_queue(saved, window.iter_queue_snapshot())
    exported = time.monotonic() - start
    restored = read_queue(saved)
    assert len(restored) == 100001
    window.discard_queue_jobs(list(window.queue_jobs))
    start = time.monotonic()
    window.restore_queue_items(restored)
    recovered = time.monotonic() - start
    assert len(window.queue_jobs) <= BUFFER_LIMIT + 1
    assert sum(1 for _ in window.iter_queue_snapshot()) == 100001
    window.preferences.setValue('Confirm/delete_queue_items', False)
    start = time.monotonic()
    window.stop_queue()
    cancelled = time.monotonic() - start
    assert not window.pending and not window.active
    assert not list(window.iter_queue_snapshot())
    report = dict(files=100001, planning_seconds=planned, buffered_rss_mb=buffered_rss / 1048576,
                  export_seconds=exported, recovery_seconds=recovered, cancellation_seconds=cancelled,
                  scroll_timer_ticks=len(ticks), snapshot_mb=saved.stat().st_size / 1048576)
    print('\n100K_METRICS=' + json.dumps(report))
