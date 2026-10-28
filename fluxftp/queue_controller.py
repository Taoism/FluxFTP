"""Qt worker lifecycle and presentation adapter for QueueEngine."""
import time
import uuid
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from .core import Cancelled, CertificateTrustRequired
from .queue_engine import QueuePolicy
from .queue_tree import STATE_ROLE, selected_rows
from .transfer_reporting import finish as report_transfer, update_group, append as append_transfer_log


class QueueController:
    def __init__(self, window):
        self.window = window
        window.queue_engine.subscribe(self.on_event)

    def policy(self):
        settings = self.window.preferences
        defaults = QueuePolicy()
        values = {}
        for key in defaults.__dataclass_fields__:
            prefix = 'Transfer/' if key in ('single_connection', 'per_tab', 'priority_rules') else 'Queue/'
            default = getattr(defaults, key)
            values[key] = settings.value(prefix + key, default, type=type(default))
        return QueuePolicy(**values)

    def on_event(self, event):
        job = event.job
        if event.kind == 'retry':
            job['row'].setText(7, f"{job['route']} · Retry {job['retries']}/{event.detail} queued")
        elif event.kind == 'reset':
            job['row'].setText(7, f"{job['route']} · Queued")
        elif event.kind == 'cancelled':
            job['row'].setData(0, STATE_ROLE, 'cancelled')
            update_group(self.window, job['row'])
            job['row'].setText(7, f"{job['route']} · Cancelled")

    def start_next(self):
        w = self.window
        if getattr(w, '_queue_operation_depth', 0) or getattr(w, '_queue_commit', False): return
        w.refill_queue_buffers()
        if w.pending:
            w.directory_cache.clear()
        if w.paused:
            return
        while w.pending:
            job_index = w.queue_engine.next_index(w.queue_controller.policy())
            if job_index is None:
                return
            job = w.pending.pop(job_index)
            if job.get('needs_credentials'):
                w.pending.insert(job_index, job)
                w.paused = True
                w.statusBar().showMessage('Restored queue needs credentials. Press Play to continue.')
                return
            w.queue_engine.begin(job)
            job['row'].setData(0, STATE_ROLE, 'active')
            record = w._transfer_record(job)
            record['job'] = job
            row, task, route = record["row"], record["task"], record["route"]
            row.setText(7, f"{route} · " + ('Opening folder…' if job.get('streaming_folder') else 'Scanning folder…' if job.get('directory') else 'Transferring'))
            if job.get('streaming_folder'):
                from .queue_tree import PROGRESS_ROLE, STREAM_ROLE
                row.setData(0, STREAM_ROLE, True)
                row.setData(3, Qt.ItemDataRole.UserRole, [0, 0])
                row.setText(3, '0 B')
                row.setText(4, '—')
                row.setToolTip(3, 'Cumulative bytes uploaded across files in this folder during this attempt.')
                row.setToolTip(4, 'Folder size is not pre-scanned.')
                row.setData(1, PROGRESS_ROLE, 0)
                row.setToolTip(1, 'Current file progress; the full folder size is not calculated before uploading.')
            row.setToolTip(7, '')
            if w.preferences.value('Queue/show_paths', False, type=bool):
                w.log.appendPlainText(f"{route}: {row.text(5)} -> {row.text(6)}")
            def done(result, target=row, transfer_route=route, current=job):
                if isinstance(result, dict) and result.get('paused'):
                    from .queue_tree import PROGRESS_ROLE
                    current['state'] = 'queued'
                    target.setText(7, f'{transfer_route} · Paused before next file')
                    target.setData(1, PROGRESS_ROLE, None)
                    target.setText(1, 'Paused')
                    target.setText(2, '0 B/s')
                    return
                if isinstance(result, dict) and result.get('planned') is not None and current.get('suppress_retry'):
                    close = getattr(result['planned'], 'close', None)
                    if close: close()
                    current['state'] = 'cancelled'
                    target.setText(7, f'{transfer_route} · Cancelled')
                    return
                current['state'] = 'complete'
                if isinstance(result, dict):
                    if result.get('planned') is not None:
                        if result.get('transferred') is False:
                            close = getattr(result['planned'], 'close', None)
                            if close: close()
                        else:
                            w.expand_queue_files(current, result['planned'])
                    for message in result.get('messages', []): w.log.appendPlainText(message)
                    if result.get('messages'): target.setToolTip(7, '\n'.join(result['messages']))
                    if result.get('crc_errors'):
                        current['transfer_outcome'] = 'failed'
                        w.register_crc_failures(current, result['crc_errors'])
                        if not current.get('crc_job'):
                            target.setText(7, f'{transfer_route} · Finished with CRC failures')
                        return
                for old, new in result.get("renamed", []) if isinstance(result, dict) else []:
                    w.log.appendPlainText(f"Auto Rename: {old} -> {new}")
                    if target.text(6) == old:
                        current['actual_dst'] = new
                        target.setText(6, new)
                skipped_items = result.get("skipped", []) if isinstance(result, dict) else []
                skipped_directories = result.get('skipped_directories', set()) if isinstance(result, dict) else set()
                skipped_root = isinstance(result, dict) and result.get("transferred") is False
                color = QBrush(QColor(w.preferences.value("Transfer/skip_color", "#ff0000", type=str)))
                if skipped_root:
                    current['transfer_outcome'] = 'skipped'
                    target.setText(7, f"{transfer_route} · Skipped")
                    target.setText(1, "—")
                    target.setToolTip(7, skipped_items[0][1] if skipped_items else "Skip List")
                    for column in range(target.columnCount()):
                        target.setForeground(column, color)
                for path, reason in skipped_items:
                    if current.get('directory') and not skipped_root:
                        w.transfer_history.skipped(current, path, reason, path in skipped_directories)
                    w.log.appendPlainText(f"Skipped: {path} ({reason})")
                    if current.get('directory'):
                        append_transfer_log(w, f"[{current.get('source_side', 1)}] Skip [{path}] {reason}")
                    if not skipped_root and path not in skipped_directories:
                        skipped_row = QTreeWidgetItem([Path(path).name, "—", "—", "—", "—", path, "", f"{transfer_route} · Skipped"])
                        skipped_row.setToolTip(7, reason)
                        for column in range(skipped_row.columnCount()):
                            skipped_row.setForeground(column, color)
                        w.add_skipped_queue_row(current, skipped_row)
                if skipped_root:
                    return
                if isinstance(result, dict) and result.get('planned') is not None:
                    return
                target.setText(1, "100%")
                if current.get('streaming_folder'):
                    from .queue_tree import PROGRESS_ROLE
                    target.setData(1, PROGRESS_ROLE, 100)
                current['transfer_outcome'] = 'complete'
                total = target.data(4, Qt.ItemDataRole.UserRole) or 0
                if not current.get('streaming_folder'):
                    target.setData(3, Qt.ItemDataRole.UserRole, [total, total])
                else:
                    target.setText(2, '0 B/s')
                target.setText(7, f"{transfer_route} · Complete")
                w.log.appendPlainText(f"Complete: {target.text(0)} ({transfer_route})")
            def failed(message, target=row, transfer_route=route, current=job):
                w.queue_batch_failed = True
                current['state'] = 'cancelled' if 'Cancelled:' in message else 'failed'
                current['transfer_outcome'] = current['state']
                target.setText(7, f"{transfer_route} · " + ("Cancelled" if "Cancelled:" in message else "Failed"))
                target.setToolTip(7, message)
                w.log.appendPlainText(f"{target.text(0)}: {message}")
            transfer_id = uuid.uuid4().hex
            def queue_error(error, current=job, current_record=record, fail=failed, job_id=transfer_id):
                from .folder_sync import PreviewChanged
                from .transfer_reliability import ReliabilityError
                if isinstance(error, (PreviewChanged, ReliabilityError, Cancelled)): current['suppress_retry'] = True
                current_record['handling_failure'] = True
                try:
                    if isinstance(error, CertificateTrustRequired):
                        if w._trust_server_certificate(error) and not current.get('suppress_retry'):
                            current['state'] = 'queued'
                        else:
                            fail('Cancelled: Server certificate was not trusted.')
                    else:
                        fail(f'Cancelled: {error}' if isinstance(error, Cancelled) else str(error))
                finally:
                    current_record['handling_failure'] = False
                    if current_record.get('finish_deferred'):
                        QTimer.singleShot(0, lambda: w.transfer_finished(job_id))
            worker = w.run(task, done, queue_error, raw_failure=True, autostart=False)
            def current_file(src, dst, target=row, transfer_route=route):
                from .queue_tree import PROGRESS_ROLE
                target.setText(7, f'{transfer_route} · {Path(src).name}')
                target.setToolTip(7, f'{src}\n→ {dst}\nProgress is for the current file; folder total is not pre-scanned.')
                target.setText(1, '0%')
                target.setData(1, PROGRESS_ROLE, 0)
            def pause_changed(paused, target=row, current=job, transfer_route=route):
                if current.get('state') != 'active': return
                if paused:
                    target.setText(2, '0 B/s')
                    from .queue_tree import RATE_ROLE
                    target.setData(0, RATE_ROLE, 0)
                    update_group(w, target)
                target.setText(7, f"{transfer_route} · " + ('Paused' if paused else 'Transferring'))
            worker.pause_changed.connect(pause_changed)
            worker.file_started.connect(current_file)
            record["worker"] = worker
            w.queue_engine.register(record, transfer_id)
            worker.progress.connect(lambda percent, rate, done, total, current=job:
                w.job_progress(current, percent, rate, done, total))
            worker.finished.connect(lambda job_id=transfer_id: w.transfer_finished(job_id))
            w.schedule_queue_save()
            worker.start()


    def transfer_finished(self, transfer_id):
        w = self.window
        w.directory_cache.clear()
        record = w.queue_engine.release(transfer_id)
        if record is None:
            return
        job = record.get('job') if record else None
        if job:
            w.transfer_history.record(job, elapsed=time.monotonic() - record['worker'].started_at)
        w.queue_engine.settle(job, w.queue_controller.policy())
        if job:
            report_transfer(w, job, record['worker'])
            w.clear_skipped_folder(job)
            w.retire_buffered_success(job)
            w.clear_finished_download(job)
        w.refill_queue_buffers()
        w.schedule_queue_save()
        w.start_next()
        if not w.active and not w.pending:
            w.conflicts.reset()
            if not w.queue_batch_failed and w.display_value("flash_completed", True):
                QApplication.alert(w, 0)
            if w.preferences.value("Misc/beep", False, type=bool):
                QApplication.beep()
            action = w.preferences.value("Transfer/completed", "Idle", type=str)
            if not w.queue_batch_failed and action in ("Disconnect", "Close FluxFTP"):
                QTimer.singleShot(100, w._apply_completion_action)
                return
            for tab_index in range(w.tabs.count()):
                split = w.tabs.widget(tab_index)
                for pane_index in range(split.count()):
                    pane = split.widget(pane_index)
                    if not pane.busy:
                        pane.refresh()


    def _apply_completion_action(self):
        w = self.window
        action = w.preferences.value("Transfer/completed", "Idle", type=str)
        if w.active or w.pending or w.queue_batch_failed or action == "Idle":
            return
        if w.workers:
            QTimer.singleShot(100, w._apply_completion_action)
        elif action == "Close FluxFTP":
            w.close()
        elif action == "Disconnect":
            for tab_index in range(w.tabs.count()):
                split = w.tabs.widget(tab_index)
                for pane_index in range(split.count()):
                    pane = split.widget(pane_index)
                    if pane.mode == "Remote":
                        pane.browse_session.close()
                        pane.site = pane.remote_site = None
                        pane.password = pane.remote_password = ""
                        pane.tree.clear()
                        pane.title_label.setText(f"({pane.number}) Remote")
                        pane.status.setText("Disconnected")


    def play_queue(self):
        w = self.window
        if not w.prepare_restored_jobs():
            return
        from .transfer_control import resume_active
        resume_active(w, w.queue_jobs)
        w.reset_failed_jobs()
        for job in w.pending:
            job['held'] = False
            w.release_queue_buffer(job)
        w.paused = False
        w.statusBar().showMessage("Transfer queue running · parallel transfers enabled")
        w.start_next()
        w.save_queue_state()


    def toggle_pause(self):
        w = self.window
        if w.paused and not w.prepare_restored_jobs():
            return
        w.paused = not w.paused
        w.statusBar().showMessage("Queue paused between files; active transfers continue" if w.paused else "Queue running")
        w.start_next()
        w.save_queue_state()


    def stop_queue(self):
        w = self.window
        for record in w.active.values():
            if record.get('job') is not None: record['job']['suppress_retry'] = True
            record['worker'].cancel.set()
        w.transfer_history.cancelled(w.pending)
        for record in w.queue_engine.cancel():
            record['worker'].cancel.set()
            record['row'].setText(7, f"{record['route']} · Cancelling…")
        w.statusBar().showMessage('Stopping active transfers and clearing the queue' if w.active else 'Transfer queue stopped')
        w.save_queue_state()

    def cancel_selected(self):
        w = self.window
        rows = selected_rows(w.queue)
        jobs = [job for job in w.queue_jobs if job['row'] in rows]
        for record in w.active.values():
            if record['row'] in rows:
                if record.get('job') is not None: record['job']['suppress_retry'] = True
                record['worker'].cancel.set()
        w.transfer_history.cancelled(job for job in w.pending if job['row'] in rows)
        for record in w.queue_engine.cancel(jobs):
            record['worker'].cancel.set()
            record['row'].setText(7, f"{record['route']} · Cancelling…")
        w.save_queue_state()
