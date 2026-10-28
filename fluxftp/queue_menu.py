"""Tools/Queue actions share the durable queue and normal transfer scheduler."""
import fnmatch
import os
import sqlite3
import posixpath
from pathlib import Path
import time
from collections import Counter

from PySide6.QtCore import QObject, Qt, QSignalBlocker, QTimer, QDateTime
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (QFileDialog, QInputDialog, QMessageBox, QDialog, QFormLayout,
    QLineEdit, QSpinBox, QCheckBox, QDialogButtonBox, QLabel, QDateTimeEdit, QMenu, QApplication, QStyle)

from .queue_manager import read_queue
from .core import Site, Cancelled
from .queue_tree import selected_rows
from shiboken6 import isValid


class QueueMenu(QObject):
    def __init__(self, window, tools_menu):
        super().__init__(window)
        self.window = window
        self.menu = tools_menu.addMenu('Queue')
        self.menu.setStyleSheet('QMenu::item:disabled { color: #9ba2aa; }')
        self.actions = {}
        self.cleared = []
        self.add(self.menu, 'Transfer Queue', window.play_queue)
        self.add(self.menu, 'Pause Queue Between Files', self.pause_queue)
        self.add(self.menu, 'Pause Selected Transfers', self.pause_selected)
        self.add(self.menu, 'Resume Selected Transfers', self.resume_selected)
        self.add(self.menu, 'Pause All Transfers', self.pause_all)
        self.add(self.menu, 'Stop Selected Transfers', window.cancel_selected)
        self.add(self.menu, 'Stop All Transfers', window.stop_queue)
        self.actions['Pause Selected Transfers'].setToolTip('Pause native file payloads at the next checkpoint. Active FXP, cloud and deployment jobs cannot be paused this way.')
        self.actions['Pause All Transfers'].setToolTip('Hold queued jobs and pause active native file payloads. Active FXP, cloud and deployment jobs continue.')
        self.actions['Stop All Transfers'].setToolTip('Cancel pending jobs and request cancellation of active transfers; partial files may remain.')
        self.add(self.menu, 'Add the selected to queue', self.add_selected, 'Ctrl+Q')
        self.add(self.menu, 'Conditional Transfer…', self.conditional)
        self.add(self.menu, 'Clear Current Queue Files', self.clear_current)
        move = self.move_menu = self.menu.addMenu('Move Queues')
        for label, key, direction in (
            ('Move Up Selected Queue', 'Ctrl+U', 'up'), ('Move Down Selected Queue', 'Ctrl+Y', 'down'),
            ('Move Selected Queue to Top', 'Ctrl+O', 'top'), ('Move Selected Queue to Bottom', 'Ctrl+M', 'bottom')):
            action = self.add(move, label, lambda direction=direction: self.move(direction), key)
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.menu.addSeparator()
        self.add(self.menu, 'Edit Queue…', self.edit)
        failed = self.failed_menu = self.menu.addMenu('Failed Queue')
        self.add(failed, 'Retry Selected', lambda: self.retry(False))
        self.add(failed, 'Retry All', lambda: self.retry(True))
        self.add(failed, 'Remove Selected', lambda: self.clear_failed(False))
        self.add(failed, 'Clear Failed', lambda: self.clear_failed(True))
        self.add(self.menu, 'Queue Information…', self.information)
        self.menu.addSeparator()
        self.add(self.menu, 'Restore Queue…', self.restore, 'Ctrl+Shift+L')
        self.add(self.menu, 'Load Queue from file…', self.load)
        self.add(self.menu, 'Save Queue to file…', window.export_queue_list)
        self.add(self.menu, 'Schedule Queue to Task…', self.schedule)
        self.menu.addSeparator()
        for route in ('Upload', 'Download'):
            action = self.add(self.menu, 'Limit ' + route + ' Speed', lambda route=route.lower(): self.limit(route))
            action.setCheckable(True)
        self.menu.aboutToShow.connect(self.update)
        move.aboutToShow.connect(self.update)
        failed.aboutToShow.connect(self.update)

    def add(self, menu, label, callback, shortcut=''):
        action = QAction(label, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(lambda checked=False: callback())
        menu.addAction(action)
        self.actions[label] = action
        return action

    def install(self):
        self.delete_action = QAction('Delete', self)
        self.delete_action.setShortcut('Del')
        self.delete_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.delete_action.triggered.connect(self.window.remove_queue_items)
        self.window.queue.addAction(self.delete_action)
        self.window.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.window.queue.customContextMenuRequested.connect(self.show_context_menu)
        for action in self.move_menu.actions():
            self.window.queue.addAction(action)
        self.window.queue.itemSelectionChanged.connect(self.update)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def selected(self):
        chosen = {id(row) for row in selected_rows(self.window.queue)}
        return [job for job in self.window.queue_jobs if id(job['row']) in chosen and (job.get('buffered') or not job['row'].childCount()) and not job['row'].isHidden()]

    def show_context_menu(self, position):
        tree = self.window.queue
        row = tree.itemAt(position)
        if position.x() < 0:
            row = tree.currentItem()
            position = tree.visualItemRect(row).center() if row else tree.rect().center()
        elif row is None:
            tree.clearSelection()
        if row is not None and not row.isSelected():
            tree.clearSelection()
            tree.setCurrentItem(row)
            row.setSelected(True)
        menu = self.context_menu()
        menu.exec(tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def context_menu(self):
        self.update()
        w = self.window
        menu = QMenu(w.queue)
        menu.addAction(self.actions['Transfer Queue'])
        for label in ('Pause Queue Between Files', 'Pause Selected Transfers', 'Resume Selected Transfers', 'Pause All Transfers', 'Stop Selected Transfers', 'Stop All Transfers'):
            menu.addAction(self.actions[label])
        self.actions['Transfer Queue'].setIcon(w.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        menu.addSeparator()
        menu.addAction(self.actions['Clear Current Queue Files'])
        menu.addSeparator()
        menu.addAction(self.delete_action)
        menu.addAction(self.actions['Edit Queue…'])
        # Popup menus are deleted after use. Never attach the persistent Tools
        # submenus here: PySide can transfer their ownership to the popup.
        # Share the long-lived actions, but give each popup its own containers.
        for source in (self.move_menu, self.failed_menu):
            submenu = menu.addMenu(source.title())
            submenu.addActions(source.actions())
            submenu.aboutToShow.connect(self.update)
        menu.addAction(self.actions['Queue Information…'])
        menu.addSeparator()
        for title in ('Load Queue from file…', 'Save Queue to file…', 'Schedule Queue to Task…'):
            menu.addAction(self.actions[title])
        complete = menu.addMenu('On Transfer Completed')
        group = QActionGroup(complete)
        for title in ('Idle', 'Disconnect', 'Close FluxFTP'):
            action = complete.addAction(title)
            action.setCheckable(True)
            action.setChecked(w.preferences.value('Transfer/completed', 'Idle', type=str) == title)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, value=title: w.preferences.setValue('Transfer/completed', value))
        chosen = self.selected()
        action = menu.addAction('Transfer selected queue', self.transfer_selected)
        action.setEnabled(any(j.get('state') in ('queued', 'failed') and not j.get('editing') and not self.active_job(j) for j in chosen))
        menu.addSeparator()
        action = menu.addAction('Open Source Folder', self.open_source)
        action.setEnabled(len(chosen) == 1 and 'src' in chosen[0])
        for title, field in [('Copy Source Path', 'src'), ('Copy Destination Path', 'dst')]:
            action = menu.addAction(title, lambda checked=False, key=field: self.copy_paths(key))
            action.setEnabled(any(j.get(field) for j in chosen))
        menu.addAction(self.actions['Restore Queue…'])
        return menu

    def copy_paths(self, key):
        QApplication.clipboard().setText('\n'.join(job[key] for job in self.selected() if job.get(key)))

    def open_source(self):
        jobs = self.selected()
        if len(jobs) == 1 and 'src' in jobs[0]:
            self.window.enter_queue_source(jobs[0])

    def pause_queue(self):
        if not self.window.paused: self.window.toggle_pause()

    def pause_selected(self):
        from .transfer_control import pause_jobs
        pause_jobs(self.window, self.selected())
        self.update()

    def pause_all(self):
        self.pause_queue()
        from .transfer_control import pause_jobs
        pause_jobs(self.window, self.window.queue_jobs)
        self.update()

    def resume_selected(self):
        from .transfer_control import resume_active
        resume_active(self.window, self.selected())
        self.transfer_selected()
        self.window.save_queue_state()
        self.update()

    def transfer_selected(self):
        w = self.window
        jobs = [j for j in self.selected() if j.get('state') in ('queued', 'failed') and not j.get('editing') and not self.active_job(j)]
        if not jobs or not w.prepare_restored_jobs(jobs):
            return
        jobs = [j for j in jobs if j in w.queue_jobs and j.get('state') in ('queued', 'failed') and not j.get('editing') and not self.active_job(j)]
        if not jobs:
            return
        for job in jobs:
            job.update(state='queued', scheduled_at=None, suppress_retry=False, retries=0, crc_attempt=0)
            w.release_queue_buffer(job, unschedule=True)
            if job not in w.pending:
                w.pending.append(job)
            job['row'].setText(7, f"{job['route']} · Queued")
        self.release(jobs)
        w.save_queue_state()
        w.start_next()

    def active_job(self, job):
        return any(record.get('row') is job['row'] for record in self.window.active.values())

    def source(self):
        pane = self.window.mark_commands.pane()
        if pane is None or pane.busy:
            return None
        workspace = pane.parentWidget()
        other = workspace.widget(1 if workspace.widget(0) is pane else 0)
        if other.busy or any(p.mode == 'Remote' and p.site is None for p in (pane, other)):
            return None
        return pane, other

    def update(self, *_):
        w = self.window
        if not hasattr(w, 'queue'):
            return
        selected = self.selected()
        if hasattr(self, 'delete_action'):
            # Summary and legacy skipped rows can outlive their transfer jobs.
            # They still need Delete; include hidden descendants in worker guards.
            row_ids = {id(row) for row in selected_rows(w.queue)}
            jobs = [job for job in w.queue_jobs if id(job['row']) in row_ids]
            self.delete_action.setEnabled(bool(row_ids) and all(
                job.get('state') != 'active' and not job.get('editing') for job in jobs)
                and not any(id(record['row']) in row_ids for record in w.active.values()))
        movable = selected and all(job.get('state') == 'queued' and not job.get('editing') for job in selected)
        waiting = [job for job in w.queue_jobs if job.get('state') == 'queued' and not job.get('editing')]
        selected_ids = {id(job) for job in selected}
        indices = [index for index, job in enumerate(waiting) if id(job) in selected_ids]
        up = bool(movable) and indices != list(range(len(indices)))
        down = bool(movable) and indices != list(range(len(waiting) - len(indices), len(waiting)))
        for action, enabled in zip(self.move_menu.actions(), (up, down, up, down)):
            action.setEnabled(enabled)
        self.actions['Edit Queue…'].setEnabled(len(selected) == 1 and selected[0].get('state') in ('queued', 'failed')
                                             and not selected[0].get('editing') and not selected[0].get('crc_job')
                                             and not selected[0].get('buffered') and not self.active_job(selected[0]))
        if len(selected) == 1 and selected[0].get('sync'):
            self.actions['Edit Queue…'].setEnabled(False)
        source = self.source()
        can_add = bool(source and source[0].selected())
        self.actions['Add the selected to queue'].setEnabled(can_add)
        self.actions['Conditional Transfer…'].setEnabled(can_add)
        self.actions['Transfer Queue'].setEnabled(any(job.get('state') in ('queued', 'failed') or job.get('held') for job in w.queue_jobs))
        self.actions['Pause Queue Between Files'].setEnabled(not w.paused)
        eligible = lambda j: j.get('state') in ('queued', 'active') and not j.get('editing')
        from .transfer_control import can_pause
        self.actions['Pause Selected Transfers'].setEnabled(any(can_pause(j) and not j.get('held') for j in selected))
        self.actions['Resume Selected Transfers'].setEnabled(any(j.get('held') or j.get('state') == 'failed' for j in selected))
        self.actions['Pause All Transfers'].setEnabled(any(can_pause(j) and not j.get('held') for j in w.queue_jobs))
        self.actions['Stop Selected Transfers'].setEnabled(any(eligible(j) for j in selected))
        self.actions['Stop All Transfers'].setEnabled(bool(w.pending or w.active))
        current = [job for job in w.queue_jobs if job['workspace'] is w.tabs.currentWidget()]
        self.actions['Clear Current Queue Files'].setEnabled(bool(current) and not any(
            job.get('state') == 'active' or job.get('editing') or self.active_job(job) for job in current))
        self.actions['Restore Queue…'].setEnabled(bool(self.cleared) and not w.active)
        self.actions['Load Queue from file…'].setEnabled(not w.active)
        self.actions['Save Queue to file…'].setEnabled(bool(getattr(w, 'deferred_queue', [])) or
            any(job.get('state') in ('queued', 'active', 'failed') for job in w.queue_jobs))
        self.actions['Schedule Queue to Task…'].setEnabled(bool(movable))
        failed = [job for job in w.queue_jobs if job.get('state') == 'failed' and not job.get('editing') and not self.active_job(job)]
        for label in ('Retry All', 'Clear Failed'):
            self.actions[label].setEnabled(bool(failed))
        for label in ('Retry Selected', 'Remove Selected'):
            self.actions[label].setEnabled(any(job in failed for job in selected))
        for route in ('Upload', 'Download'):
            action = self.actions['Limit ' + route + ' Speed']
            with QSignalBlocker(action):
                action.setChecked(w.preferences.value('Transfer/limit_' + route.lower(), False, type=bool))

    @staticmethod
    def schedule_key(job, rules):
        from .queue_engine import schedule_key
        return schedule_key(job, rules)

    def tick(self):
        if getattr(self.window, '_queue_operation_depth', 0) or getattr(self.window, '_queue_commit', False): return
        from .queue_tree import SPEED_ROLE
        from .transfer_reporting import update_group
        for index in range(self.window.queue.topLevelItemCount()):
            row = self.window.queue.topLevelItem(index)
            if row.data(0, SPEED_ROLE) is not None: update_group(self.window, row)
        self.update()
        w = self.window
        if not w.paused and any((job.get('buffered') or (job.get('scheduled_at') and job['scheduled_at'] <= time.time()))
                and not job.get('held') and not job.get('needs_credentials') for job in w.pending):
            w.start_next()

    def add_selected(self):
        pair = self.source()
        if pair:
            self.window.enqueue_items(*pair, pair[0].selected(), start=False)
        self.update()

    def conditional(self):
        pair = self.source()
        if pair is None:
            return
        source, destination = pair
        entries = [entry for entry in source.selected() if not entry.is_dir and not entry.is_link]
        state = [(p.path, p.site, p.generation) for p in pair]
        dialog = QDialog(self.window)
        dialog.setWindowTitle('Conditional Transfer')
        form = QFormLayout(dialog)
        form.addRow(QLabel('Filter selected files; folders and symbolic links are excluded.'))
        masks = QLineEdit('*')
        lower, upper = QSpinBox(), QSpinBox()
        for control in (lower, upper):
            control.setRange(0, 2147483647)
            control.setSuffix(' KiB')
        upper.setSpecialValueText('No maximum')
        immediate = QCheckBox('Begin transfer immediately')
        form.addRow('Filename masks (semicolon-separated)', masks)
        form.addRow('Minimum size', lower)
        form.addRow('Maximum size', upper)
        form.addRow(immediate)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        if any(p.busy or (p.path, p.site, p.generation) != old for p, old in zip(pair, state)):
            return
        if upper.value() and upper.value() < lower.value():
            self.window.error('Maximum size must be at least the minimum size.')
            return
        patterns = [part.strip().casefold() for part in masks.text().split(';') if part.strip()]
        matches = [entry for entry in entries if entry.size >= lower.value() * 1024
                   and (not upper.value() or entry.size <= upper.value() * 1024)
                   and any(pattern in ('*', '*.*') or fnmatch.fnmatchcase(entry.name.casefold(), pattern) for pattern in patterns)]
        if matches:
            self.window.enqueue_items(source, destination, matches, start=immediate.isChecked())
        else:
            self.window.statusBar().showMessage('No selected files match the conditions.', 6000)
        self.update()

    def move(self, direction):
        w = self.window
        selected = self.selected()
        if not selected or any(job.get('state') != 'queued' or job.get('editing') for job in selected):
            return
        movable = [job for job in w.queue_jobs if job.get('state') == 'queued' and not job.get('editing')]
        chosen = {id(job) for job in selected}
        if direction in ('top', 'bottom'):
            picked = [job for job in movable if id(job) in chosen]
            others = [job for job in movable if id(job) not in chosen]
            ordered = picked + others if direction == 'top' else others + picked
        else:
            ordered = movable[:]
            positions = range(1, len(ordered)) if direction == 'up' else range(len(ordered) - 2, -1, -1)
            delta = -1 if direction == 'up' else 1
            for index in positions:
                other = index + delta
                if id(ordered[index]) in chosen and id(ordered[other]) not in chosen:
                    ordered[index], ordered[other] = ordered[other], ordered[index]
        if all(a is b for a, b in zip(ordered, movable)):
            return
        ids = {id(job) for job in movable}
        iterator = iter(ordered)
        w.queue_jobs[:] = [next(iterator) if id(job) in ids else job for job in w.queue_jobs]
        for index, job in enumerate(ordered):
            job['manual_order'] = index
        positions = {id(job): index for index, job in enumerate(w.queue_jobs)}
        w.pending.sort(key=lambda job: positions.get(id(job), len(positions)))
        scroll = w.queue.verticalScrollBar().value()
        with QSignalBlocker(w.queue):
            expansion = {id(w.queue.topLevelItem(i)): w.queue.topLevelItem(i).isExpanded()
                         for i in range(w.queue.topLevelItemCount())}
            rows = [w.queue.takeTopLevelItem(0) for _ in range(w.queue.topLevelItemCount())]
            rank = {id(job['row']): index for index, job in enumerate(w.queue_jobs)}
            selected_rows = {id(job['row']) for job in selected}
            for row in sorted(rows, key=lambda row: rank.get(id(row), len(rank))):
                w.queue.addTopLevelItem(row)
                row.setSelected(id(row) in selected_rows)
                expanded = expansion[id(row)]
                children = row.takeChildren()
                for child in sorted(children, key=lambda child: rank.get(id(child), len(rank))):
                    row.addChild(child)
                    child.setSelected(id(child) in selected_rows)
                row.setExpanded(expanded)
        w.queue.verticalScrollBar().setValue(scroll)
        w.save_queue_state()
        self.update()

    def edit(self):
        selected = self.selected()
        if len(selected) == 1:
            self.window.edit_mouse_queue(selected[0])
        self.update()

    def clear_jobs(self, jobs):
        w = self.window
        if not jobs or any(job.get('state') == 'active' or job.get('editing') or self.active_job(job) for job in jobs):
            return
        if not w.confirm_action('delete_queue_items', 'Clear queue items',
                f'Remove {len(jobs)} queue item(s)? Source and destination files will be kept.'):
            return
        if any(job not in w.queue_jobs or job.get('state') == 'active' or job.get('editing') or self.active_job(job) for job in jobs):
            return
        self.cleared = [(w.queue_jobs.index(job), dict(job)) for job in jobs]
        w.discard_queue_jobs(jobs)
        w.save_queue_state()
        self.update()

    def clear_current(self):
        self.clear_jobs([job for job in self.window.queue_jobs if job['workspace'] is self.window.tabs.currentWidget()])

    def clear_failed(self, all_jobs):
        self.clear_jobs([job for job in (self.window.queue_jobs if all_jobs else self.selected())
                         if job.get('state') == 'failed' and not job.get('editing')])

    def restore(self):
        w = self.window
        if not self.cleared or w.active:
            return
        w.paused = True
        for index, job in self.cleared:
            if not isValid(job['workspace']) or w.tabs.indexOf(job['workspace']) < 0:
                job['workspace'] = w.tabs.currentWidget()
            job = w.new_queue_job(job)
            if 'src' in job:
                job['task'] = w.make_queue_task(job)
            w.queue_jobs.insert(min(index, len(w.queue_jobs)), job)
            if job.get('queue_group'):
                w.attach_queue_row(job)
            else:
                w.queue.insertTopLevelItem(min(index, w.queue.topLevelItemCount()), job['row'])
            if job.get('state') == 'queued':
                w.pending.append(job)
        self.cleared = []
        ranks = {id(job): index for index, job in enumerate(w.queue_jobs)}
        w.pending.sort(key=lambda job: ranks.get(id(job), len(ranks)))
        w.save_queue_state()
        w.statusBar().showMessage('Cleared queue restored, paused. Transfer Queue resumes it.', 8000)
        self.update()

    def load(self):
        w = self.window
        if w.active:
            return
        if getattr(w, 'deferred_queue', []):
            w.show_saved_queue()
            self.update()
            return
        path, _ = QFileDialog.getOpenFileName(w, 'Load Queue from file', '', 'FluxFTP queue lists (*.json)')
        if not path:
            return
        try:
            from .queue_io import background, import_file
            saved = background(w, 'Reading queue list…', lambda worker: import_file(path, worker.cancel))
            if w.active or any(job.get('editing') for job in w.queue_jobs):
                raise ValueError('Wait for transfers and queue editors before loading a queue.')
            w.restore_queue_items(saved)
            w.save_queue_state()
            self.update()
        except Cancelled:
            w.statusBar().showMessage('Queue import cancelled.', 5000)
        except (OSError, ValueError, TypeError, sqlite3.Error) as error:
            w.error(str(error))

    def release(self, jobs):
        w = self.window
        if w.paused:
            selected = {id(job) for job in jobs}
            for job in w.pending:
                if id(job) not in selected:
                    job['held'] = True
        w.paused = False
        for job in jobs:
            job['held'] = False
            w.release_queue_buffer(job)

    def retry(self, all_jobs):
        w = self.window
        jobs = [job for job in (w.queue_jobs if all_jobs else self.selected())
                if job.get('state') == 'failed' and not job.get('editing') and not self.active_job(job)]
        if not jobs or not w.prepare_restored_jobs(jobs):
            return
        jobs = [job for job in jobs if job in w.queue_jobs and job.get('state') == 'failed' and not job.get('editing') and not self.active_job(job)]
        if not jobs:
            return
        for job in jobs:
            job.update(state='queued', retries=0, crc_attempt=0, suppress_retry=False, batch_active=True, scheduled_at=None)
            if job not in w.pending:
                w.pending.append(job)
            job['row'].setText(7, f"{job['route']} · Queued")
        self.release(jobs)
        w.save_queue_state()
        w.start_next()
        self.update()

    def schedule(self):
        w = self.window
        jobs = self.selected()
        if not jobs or any(job.get('state') != 'queued' or job.get('editing') for job in jobs):
            return
        dialog = QDialog(w)
        dialog.setWindowTitle('Schedule Queue to Task')
        form = QFormLayout(dialog)
        form.addRow(QLabel('Runs once while FluxFTP is open. Recovered queues stay paused until resumed.'))
        when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(60))
        when.setCalendarPopup(True)
        when.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        form.addRow('Start time (local)', when)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        timestamp = when.dateTime().toSecsSinceEpoch()
        if timestamp <= time.time():
            w.error('Choose a future start time.')
            return
        if any(job not in w.queue_jobs or job.get('state') != 'queued' or job.get('editing') for job in jobs):
            return
        if not w.prepare_restored_jobs(jobs):
            return
        if any(job not in w.queue_jobs or job.get('state') != 'queued' or job.get('editing') for job in jobs):
            return
        self.release(jobs)
        for job in jobs:
            job['scheduled_at'] = timestamp
            job['row'].setText(7, f"Scheduled {when.text()}")
        w.tasks_log.appendPlainText(f'Scheduled {len(jobs)} queue item(s) for {when.text()} (local time).')
        w.save_queue_state()
        self.update()

    def limit(self, route):
        w = self.window
        action = self.actions['Limit ' + route.title() + ' Speed']
        enabled = action.isChecked()
        speed = w.preferences.value('Transfer/' + route + '_kib', 10, type=int)
        if enabled:
            speed, accepted = QInputDialog.getInt(w, 'Limit ' + route.title() + ' Speed',
                'Shared limit in KiB/s (existing local-address bypass still applies):', speed, 1, 1048576)
            if not accepted:
                self.update()
                return
        w.preferences.setValue('Transfer/limit_' + route, enabled)
        w.preferences.setValue('Transfer/' + route + '_kib', speed)
        getattr(w, route + '_limiter').configure(speed * 1024 if enabled else 0)
        if route == 'upload' and hasattr(w.cloud_service, 'drive_upload_limiter'):
            w.cloud_service.drive_upload_limiter.configure(speed * 1024 if enabled else 0)
        self.update()

    def information(self):
        from .queue_buffer import BUFFER_ROLE
        w = self.window
        counts = Counter()
        buffered = held = scheduled = 0
        for job in w.queue_jobs:
            state = job.get('state', 'unknown')
            if job.get('buffered'):
                spool = job['buffer']
                buffered += len(spool)
                counts[state] += len(spool)
                meta = job['row'].data(0, BUFFER_ROLE) or {}
                counts['complete'] += meta.get('completed', 0)
                if state == 'queued' and len(spool):
                    disk_held, disk_scheduled = spool.waiting_counts()
                    held += len(spool) if job.get('held') else 0 if job.get('buffer_release') else disk_held
                    scheduled += (int(bool(job['scheduled_at'])) * len(spool) if job.get('scheduled_at') is not None
                                  else 0 if job.get('buffer_unschedule') else disk_scheduled)
            elif not job.get('queue_group_parent'):
                counts[state if state in ('queued', 'active') else job.get('transfer_outcome', state)] += 1
                if state == 'queued':
                    held += bool(job.get('held'))
                    scheduled += bool(job.get('scheduled_at'))
        counts = Counter({state: count for state, count in counts.items() if count})
        text = '\n'.join(f'{state.title()}: {count}' for state, count in sorted(counts.items()))
        if not text:
            text = 'Saved queue awaiting recovery.' if getattr(w, 'deferred_queue', []) else 'Queue is empty.'
        text += f'\nBuffered on disk: {buffered}\nHeld: {held}\nScheduled: {scheduled}'
        text += f"\nSaved for recovery: {len(getattr(w, 'deferred_queue', []))}"
        text += f"\nSelected rows: {len(self.selected())}\nQueue {'paused' if w.paused else 'running'}"
        QMessageBox.information(w, 'Queue Information', text)
