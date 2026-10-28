"""Confirmation preferences and the actions they guard."""
from pathlib import Path
import os
import sqlite3
import subprocess
from .queue_tree import selected_rows, detach
from .core import Cancelled

from PySide6.QtWidgets import QMessageBox, QFileDialog

DEFAULTS = dict(exit=True, shutdown=True, close_connected=True, close_queue=True,
                delete_local=False, delete_queue_items=False, delete_queue_files=False)
LABELS = dict(exit='When exit FluxFTP', shutdown='When shutdown Computer',
              close_connected='When close a window with FTP connected',
              close_queue='When close a window has Queue files',
              delete_local='When Delete Local File', delete_queue_items='When Delete Queue Items',
              delete_queue_files='When Delete Queue Files')


def request_system_shutdown():
    if os.name != 'nt':
        raise OSError('Computer shutdown is currently supported on Windows only.')
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'shutdown.exe'
    result = subprocess.run([str(executable), '/s', '/t', '0'], shell=False,
        capture_output=True, text=True, errors='replace', creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    if result.returncode:
        raise OSError(result.stderr.strip() or result.stdout.strip() or 'Windows rejected the shutdown request.')


class ConfirmOptions:
    def shutdown_computer(self):
        if self.workers or self.active or self.pending:
            self.statusBar().showMessage('Finish or cancel outstanding work before shutting down the computer.', 6000)
            return
        if not self.confirm_action('shutdown', 'Shutdown computer', 'Shut down this computer now?'):
            return
        for dialog in list(getattr(self, 'mouse_windows', [])):
            if not dialog.close():
                return
        if self.workers or self.active or self.pending:
            self.statusBar().showMessage('New work started; shutdown cancelled.', 6000)
            return
        self.save_queue_state()
        try:
            request_system_shutdown()
        except (OSError, subprocess.SubprocessError) as error:
            self.error(str(error))

    def confirm_action(self, key, title, message):
        if not self.preferences.value('Confirm/' + key, DEFAULTS[key], type=bool):
            return True
        return QMessageBox.question(self, title, message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def remove_queue_items(self):
        rows = selected_rows(self.queue)
        if not rows:
            return
        jobs = [job for job in self.queue_jobs if job['row'] in rows]
        if any(job.get('state') == 'active' or job.get('editing') for job in jobs):
            self.statusBar().showMessage('Stop active transfers and close queue editors before removing their items.', 6000)
            return
        if not self.confirm_action('delete_queue_items', 'Delete queue items',
                f'Remove {len(rows)} selected queue item(s)? Source and destination files will be kept.'):
            return
        if any(record['row'] in rows for record in self.active.values()) or any(job.get('editing') for job in jobs):
            self.statusBar().showMessage('A selected item became active; removal cancelled.', 6000)
            return
        self.discard_queue_jobs(jobs)
        for row in rows:
            detach(self.queue, row)
        self.save_queue_state()

    def discard_queue_jobs(self, jobs):
        from .queue_tree import MARKER_PARENT_ROLE, MARKER_OWNER_ROLE
        marker_parents = set()
        for job in jobs:
            token = job['row'].data(0, MARKER_PARENT_ROLE)
            if token: marker_parents.add(token)
            job['suppress_retry'] = True
            if job in self.pending:
                self.pending.remove(job)
            if job in self.queue_jobs:
                self.queue_jobs.remove(job)
            detach(self.queue, job['row'])
            from .queue_job import QueueJob
            if isinstance(job, QueueJob):
                self.job_views.pop(job.model.job_id, None)
                job.view.close()
        from .queue_tree import GROUP_ROLE
        from .transfer_reporting import update_group
        owned = {id(job['row']) for job in self.queue_jobs}
        for i in reversed(range(self.queue.topLevelItemCount())):
            row = self.queue.topLevelItem(i)
            if row.data(0, MARKER_OWNER_ROLE) in marker_parents and id(row) not in owned:
                detach(self.queue, row)
            elif row.data(0, GROUP_ROLE) and not row.childCount() and id(row) not in owned:
                detach(self.queue, row)
            elif row.data(0, GROUP_ROLE):
                update_group(self, row)

    def export_queue_list(self):
        from .queue_io import export
        path, _ = QFileDialog.getSaveFileName(self, 'Save queue list', 'fluxftp-queue.json', 'FluxFTP queue lists (*.json)')
        if path:
            try:
                export(self, path)
            except Cancelled:
                self.statusBar().showMessage('Queue export cancelled.', 5000)
            except (OSError, ValueError, sqlite3.Error) as error:
                self.error(str(error))

    def delete_queue_file(self):
        from .queue_manager import read_queue
        path, _ = QFileDialog.getOpenFileName(self, 'Delete saved queue file', '', 'FluxFTP queue lists (*.json)')
        if not path:
            return
        file = Path(path)
        try:
            if file.is_symlink():
                raise ValueError('Select the saved queue-list file itself, not a symbolic link.')
            if hasattr(self, 'queue_file') and file.resolve() == self.queue_file.resolve():
                raise ValueError('The active recovery queue is managed automatically. Select an exported queue list.')
            from .queue_io import background
            background(self, 'Checking queue list…', lambda worker: read_queue(file, worker.cancel))
            if not self.confirm_action('delete_queue_files', 'Delete saved queue file',
                    f'Permanently delete this saved queue list?\n{file}\nThe files listed inside it will be kept.'):
                return
            file.unlink()
            self.statusBar().showMessage('Saved queue list deleted.', 5000)
        except Cancelled:
            return
        except (OSError, ValueError, TypeError) as error:
            self.error(str(error))
