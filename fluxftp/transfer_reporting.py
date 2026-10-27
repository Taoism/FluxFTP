"""Release progress and concise, plain-text transfer summaries."""
import time

from PySide6.QtCore import Qt

from .diagnostics import redact
from .queue_tree import GROUP_ROLE, STATE_ROLE, RATE_ROLE, PROGRESS_ROLE, SUMMARY_ROLE, RETRY_ROLE, SPEED_ROLE
from .queue_buffer import BUFFER_ROLE


def append(window, text):
    window.transfer_log.appendPlainText(redact(text))


def update_group(window, row):
    parent = row if row.data(0, GROUP_ROLE) else row.parent()
    if parent is None or not parent.data(0, GROUP_ROLE):
        return
    unique = {}
    for i in range(parent.childCount()):
        child = parent.child(i)
        key = child.text(5), child.text(6)
        if key not in unique or child.data(0, RETRY_ROLE):
            unique[key] = child
    meta = parent.data(0, BUFFER_ROLE) or {}
    done = processed = meta.get('bytes', 0)
    total = done + meta.get('remaining_bytes', 0)
    complete = meta.get('completed', 0)
    rate = 0
    states = []
    for child in unique.values():
        state = child.data(0, STATE_ROLE) or 'queued'
        states.append(state)
        progress = child.data(3, Qt.ItemDataRole.UserRole) or [0, 0]
        size = max(0, child.data(4, Qt.ItemDataRole.UserRole) or progress[1])
        received = min(size, max(0, progress[0]))
        total += size
        done += received
        processed += size if state in ('complete', 'skipped') else received
        complete += state in ('complete', 'skipped')
        if state == 'active': rate += child.data(0, RATE_ROLE) or 0
    count = len(states) + meta.get('completed', 0) + meta.get('remaining', 0)
    percent = min(100, int(processed * 100 / total)) if total else (100 if count and complete == count else 0)
    if meta.get('remaining') or any(state not in ('complete', 'skipped') for state in states): percent = min(99, percent)
    parent.setText(1, f'{percent}%')
    parent.setData(1, PROGRESS_ROLE, percent)
    parent.setData(3, Qt.ItemDataRole.UserRole, [done, total])
    parent.setData(4, Qt.ItemDataRole.UserRole, total)
    from .transfer_speed import RollingSpeed
    meter = parent.data(0, SPEED_ROLE)
    if meter is None:
        meter = RollingSpeed()
        parent.setData(0, SPEED_ROLE, meter)
    overall_rate = meter.sample(done, fallback=rate * 1024)
    active = 'active' in states
    waiting = meta.get('remaining') or 'queued' in states
    if not active and (not waiting or window.paused): overall_rate = 0
    parent.setText(2, window.file_size(round(overall_rate)) + '/s')
    parent.setToolTip(2, 'Combined transfer speed for this folder (rolling 5-second average).')
    parent.setToolTip(1, 'Overall progress, including completed and skipped files.')
    parent.setText(3, f'{window.file_size(done)} / {window.file_size(total)}')
    parent.setText(4, window.file_size(total))
    parent.setText(7, f'{complete}/{count} files' + (f" · {meta['remaining']} buffered" if meta.get('remaining') else '') + (' · Failed' if 'failed' in states else
                   ' · Cancelled' if 'cancelled' in states else ' · Complete' if count and complete == count else ''))


def finish(window, job, worker):
    row = job['row']
    state = job.get('transfer_outcome') or job.get('state', 'failed')
    if job.get('directory') and state in ('failed', 'cancelled'):
        append(window, f"[{job.get('source_side', 1)}] {state.upper()} [{row.text(0)}] {row.toolTip(7)}")
    if not job.get('directory'):
        side = job.get('source_side', 1) if job['route'] != 'Upload' else job.get('destination_side', 2)
        size = row.data(4, Qt.ItemDataRole.UserRole) or 0
        elapsed = max(.001, time.monotonic() - worker.started_at)
        name = job.get('name', row.text(0))
        if state == 'complete':
            append(window, f'[{side}] {name} {size} bytes/{elapsed:.2f}s/{size / elapsed / 1024:.2f} KBps')
        elif state == 'skipped':
            append(window, f'[{side}] Skip [{name}] {row.toolTip(7)}')
        else:
            append(window, f'[{side}] {state.upper()} [{name}] {row.toolTip(7)}')
    row.setData(0, STATE_ROLE, job.get('state') if job.get('state') in ('queued', 'active') else state)
    row.setData(0, RATE_ROLE, 0)
    update_group(window, row)
    parent = row.parent()
    if parent is None: return
    meta = parent.data(0, BUFFER_ROLE) or {}
    if meta.get('remaining'): return
    group_id = parent.data(0, GROUP_ROLE)
    if meta and any(record.get('job', {}).get('queue_group', {}).get('id') == group_id for record in window.active.values()):
        return
    members = [j for j in window.queue_jobs if j.get('queue_group', {}).get('id') == group_id and not j.get('directory')]
    if not members or any(j.get('state') in ('active', 'queued') for j in members): return
    signature = repr([(id(j), j.get('state'), j.get('transfer_outcome')) for j in members])
    if parent.data(0, SUMMARY_ROLE) == signature: return
    parent.setData(0, SUMMARY_ROLE, signature)
    latest = {}
    for member in members:
        key = member['src'], member['dst']
        if key not in latest or member.get('crc_job'): latest[key] = member
    successes = [j for j in latest.values() if j.get('state') == 'complete' and j.get('transfer_outcome', 'complete') == 'complete']
    failures = sum(j.get('state') in ('failed', 'cancelled') for j in latest.values())
    total = sum(j['row'].data(4, Qt.ItemDataRole.UserRole) or 0 for j in successes) + meta.get('bytes', 0)
    started = min(j.get('transfer_started', time.monotonic()) for j in members)
    if meta.get('started'): started = min(started, meta['started'])
    elapsed = max(.001, time.monotonic() - started)
    side = job.get('source_side', 1) if job['route'] != 'Upload' else job.get('destination_side', 2)
    append(window, f'[{side}] [{parent.text(0)}] transferred {len(successes) + meta.get("completed", 0)} file(s) - Total: {total} bytes in {elapsed:.2f}s ({total / elapsed / 1024:.2f} KBps); {failures} failed/cancelled')
