import pytest
from PySide6.QtWidgets import QTreeWidgetItem

from fluxftp.queue_tree import rows
from test_queue import window, job_for
from test_queue_folders import folder
from test_ui import settle

MARKER = '( 709M 10F - COMPLETE - Pop 2026 )'


def marker(window, parent, name=MARKER):
    row = QTreeWidgetItem([name, '', '', '', '', '/release/' + name, '', 'Download · Skipped'])
    window.add_skipped_queue_row(parent, row)
    return row


@pytest.mark.parametrize('outcome', ['complete', 'failed', 'cancelled'])
def test_marker_waits_for_own_parent_and_preserves_other_rows(window, tmp_path, outcome):
    parent, children = folder(window, tmp_path)
    own = marker(window, parent)
    ordinary = marker(window, parent, 'notes.txt')
    other = job_for(window, None, name='other-folder')
    other['directory'] = True
    unrelated = marker(window, other)
    window.pending.clear()
    for job in (parent, *children): job['route'] = 'Download'
    children[0].update(state='complete', transfer_outcome='complete')
    window.clear_finished_download(children[0])
    assert own.treeWidget() is window.queue
    children[1].update(state=outcome, transfer_outcome=outcome)
    window.clear_finished_download(children[1])
    assert (own.treeWidget() is None) == (outcome == 'complete')
    assert ordinary.treeWidget() is window.queue and unrelated.treeWidget() is window.queue
    if outcome != 'complete':
        # Explicitly clearing this folder also clears its informational marker.
        window.discard_queue_jobs([parent, *children])
        assert own.treeWidget() is None
        assert unrelated.treeWidget() is window.queue


def test_worker_created_marker_clears_with_completed_parent(window, tmp_path):
    parent = job_for(window, lambda _: {
        'transferred': True, 'skipped': [('/release/' + MARKER, 'Skip List mask'),
                                       ('/release/notes.txt', 'Skip List mask')]}, 'Download', 'release')
    parent.update(directory=True, src='/release', dst=str(tmp_path/'target'), name='release', resume=False)
    window.start_next()
    settle(window)
    assert not window.queue_jobs
    assert [r.text(0) for r in rows(window.queue)] == ['notes.txt']
    assert MARKER in window.transfer_log.toPlainText()
