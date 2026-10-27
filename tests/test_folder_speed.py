from types import SimpleNamespace
from PySide6.QtCore import Qt
from fluxftp.queue_tree import STATE_ROLE, SPEED_ROLE
from fluxftp.transfer_speed import RollingSpeed
from test_queue import window
from test_queue_folders import folder


def test_rolling_speed_survives_file_gaps_then_decays():
    meter = RollingSpeed()
    assert meter.sample(0, 0) == 0
    assert meter.sample(1024, 1) == 1024
    assert meter.sample(1024, 2) == 512
    meter.sample(1024, 6)
    assert meter.sample(1024, 7) == 0
    assert meter.sample(0, 8) == 0  # Retry/reset never produces negative rates.


def test_collapsed_folder_combines_bytes_across_finished_and_active_children(window, tmp_path, monkeypatch):
    import fluxftp.transfer_speed as module
    now = [0.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    window.paused = False
    parent, children = folder(window, tmp_path)
    parent['row'].setExpanded(False)
    for child in children:
        child['row'].setData(0, STATE_ROLE, 'active')
        child['row'].setData(4, Qt.ItemDataRole.UserRole, 4096)
    parent['row'].setData(0, SPEED_ROLE, RollingSpeed())
    parent['row'].data(0, SPEED_ROLE).sample(0, 0)
    now[0] = 1
    window.transfer_display_progress(children[0]['row'], 25, 1, 1024, 4096)
    window.transfer_display_progress(children[1]['row'], 50, 2, 2048, 4096)
    assert parent['row'].text(2) == window.file_size(3072) + '/s'
    children[0]['row'].setData(0, STATE_ROLE, 'complete')
    children[1]['row'].setData(0, STATE_ROLE, 'queued')
    now[0] = 2
    window.queue_commands.tick()
    assert parent['row'].text(2) == window.file_size(1536) + '/s'
    assert not parent['row'].isExpanded()
    window.paused = True
    window.queue_commands.tick()
    assert parent['row'].text(2) == '0 B/s'


def test_streaming_worker_rate_spans_file_boundaries(monkeypatch):
    import fluxftp.transfer_speed as speed_module
    import fluxftp.app as app_module
    now = [0.0]
    monkeypatch.setattr(speed_module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    worker = app_module.Worker(lambda _: None)
    samples = []
    worker.progress.connect(lambda *values: samples.append(values))
    worker.begin_file()
    now[0] = 1
    worker.report(1024, 1024)
    worker.begin_file()
    now[0] = 2
    worker.report(1024, 1024)
    assert samples[-1][1] == 1
    assert samples[-1][2:] == (2048, 0)
    assert worker.stream_bytes == 1024


def test_streaming_folder_displays_cumulative_bytes_without_inventing_total(window):
    from PySide6.QtWidgets import QTreeWidgetItem
    from fluxftp.queue_tree import STREAM_ROLE, PROGRESS_ROLE
    row = QTreeWidgetItem(['Cloud folder'])
    window.queue.addTopLevelItem(row)
    row.setData(0, STREAM_ROLE, True)
    row.setData(1, PROGRESS_ROLE, 0)
    window.transfer_display_progress(row, 50, 12, 7 * 1024**3, 0)
    assert row.text(3) == window.file_size(7 * 1024**3)
    assert row.text(4) == '—'
    assert row.data(1, PROGRESS_ROLE) == 50
    window.transfer_display_progress(row, 10, 12, 8 * 1024**3, 0)
    assert row.text(3) == window.file_size(8 * 1024**3)
