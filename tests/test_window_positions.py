from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
import pytest

from fluxftp.app import SiteDialog
from fluxftp.site_manager import SitesDialog
from test_queue import window, job_for, settle
from test_queue_folders import folder


def test_dialog_geometry_is_remembered_per_window_type(window):
    window.show()
    first = SiteDialog(window, {}, editing=True)
    first.show()
    first.resize(720, 650)
    first.move(20, 30)
    QTest.qWait(300)
    saved = first.geometry()
    first.reject()
    second = SiteDialog(window, {}, editing=True)
    second.show()
    QTest.qWait(20)
    assert second.size() == saved.size()
    assert (second.pos() - saved.topLeft()).manhattanLength() < 10
    assert window.preferences.value('Layout/windows/SiteDialog')
    manager = SitesDialog(window)
    assert not window.preferences.value('Layout/windows/SitesDialog')
    second.reject()
    manager.reject()


def test_main_window_geometry_saved_and_smoke_does_not_overwrite(window):
    window.show()
    window.resize(1200, 780)
    window.move(QPoint(60, 70))
    QTest.qWait(300)
    raw = window.preferences.value('Layout/windows/main')
    assert raw
    window._smoke_test = True
    window.resize(1250, 800)
    QTest.qWait(300)
    assert window.preferences.value('Layout/windows/main') == raw


@pytest.mark.parametrize('route', ['Download', 'Upload'])
def test_successful_transfer_clears_after_completion(window, route):
    job = job_for(window, lambda _: None, route)
    window.start_next()
    settle(window)
    assert job not in window.queue_jobs
    assert window.queue.topLevelItemCount() == 0
    assert 'item' in window.transfer_log.toPlainText()


@pytest.mark.parametrize('route', ['Download', 'Upload'])
def test_non_success_remains(window, route):
    for state, outcome in [('failed', 'failed'), ('cancelled', 'cancelled'),
                           ('complete', 'skipped'), ('complete', 'failed')]:
        job = job_for(window, None, route)
        window.pending.remove(job)
        job.update(state=state, transfer_outcome=outcome)
        window.clear_finished_download(job)
        assert job in window.queue_jobs and not job['row'].isHidden()
        window.discard_queue_jobs([job])


@pytest.mark.parametrize('route', ['Download', 'Upload'])
def test_folder_successes_hide_then_group_clears(window, tmp_path, route):
    parent, children = folder(window, tmp_path)
    for job in [parent, *children]:
        job['route'] = route
    first, second = children
    window.pending.remove(first)
    first.update(state='complete', transfer_outcome='complete')
    window.clear_finished_download(first)
    assert first['row'].isHidden()
    assert not parent['row'].isHidden() and not second['row'].isHidden()
    window.pending.remove(second)
    second.update(state='complete', transfer_outcome='complete')
    window.clear_finished_download(second)
    assert not window.queue_jobs and window.queue.topLevelItemCount() == 0


def test_successful_upload_does_not_clear_failed_sibling(window, tmp_path):
    parent, children = folder(window, tmp_path)
    window.pending.clear()
    for job in [parent, *children]:
        job['route'] = 'Upload'
    children[0].update(state='complete', transfer_outcome='complete')
    children[1].update(state='failed', transfer_outcome='failed')
    window.clear_finished_download(children[0])
    assert children[0]['row'].isHidden()
    assert children[1] in window.queue_jobs and not children[1]['row'].isHidden()
    assert parent in window.queue_jobs


def test_manual_collapsed_queue_not_expanded_for_transfer(window):
    window.show()
    window._manual_panel_layout = True
    index = window.work_area.indexOf(window.queue_panel)
    sizes = [300, 300, 300]
    sizes[index] = 0
    window.work_area.setSizes(sizes)
    window.show_transfer_queue()
    assert window.work_area.sizes()[index] == 0
