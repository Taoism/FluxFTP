"""Durable, credential-free queue descriptions and desktop queue lifecycle."""
import json
import os
import sqlite3
import uuid
from dataclasses import asdict, replace
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QTreeWidgetItem, QInputDialog, QLineEdit

from .core import Site, Cancelled, transfer_tree, PROTOCOLS
from .site_import import validate_site
from .transfer_options import TransferOptions
from .site_options import transfer_settings
from .queue_tree import GROUP_ROLE
from .queue_tree import STATE_ROLE, RETRY_ROLE
from .transfer_reporting import update_group
from .queue_buffer import BufferedQueue, DiskBuffer, BUFFER_LIMIT


class FolderPaused(Exception):
    """Return a streaming folder to the durable queue at a file boundary."""


def read_queue(path, cancel=None):
    from .queue_json import queue_items
    jobs = []
    for raw in queue_items(path, cancel):
        if not isinstance(raw, dict): raise ValueError('Invalid queue job.')
        item = {}
        for name in ('source_site', 'destination_site'):
            value = raw.get(name)
            if value is not None:
                site = Site(**value)
                validate_site(site)
                value = asdict(site)
            item[name] = value
        for name in ('src', 'dst', 'name'):
            value = raw.get(name)
            if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
                raise ValueError('Invalid queue path or name.')
            item[name] = value
        for name in ('directory', 'resume'):
            if type(raw.get(name)) is not bool: raise ValueError('Invalid queue flag.')
            item[name] = raw[name]
        item['automatic'] = raw.get('automatic', False)
        if type(item['automatic']) is not bool: raise ValueError('Invalid automatic transfer flag.')
        group = raw.get('queue_group')
        if group is not None:
            if not isinstance(group, dict) or set(group) != {'id', 'name', 'src', 'dst'} or any(
                not isinstance(value, str) or not value or len(value) > 32768 or any(ord(c) < 32 for c in value)
                for value in group.values()):
                raise ValueError('Invalid queue folder group.')
            item['queue_group'] = group
        for name in ('workspace', 'retries'):
            value = raw.get(name, 0)
            if type(value) is not int or not 0 <= value <= (99 if name == 'workspace' else 999):
                raise ValueError('Invalid queue counter.')
            item[name] = value
        if raw.get('state') not in ('queued', 'active', 'failed'):
            raise ValueError('Invalid queue state.')
        item['state'] = raw['state']
        item['size'] = raw.get('size')
        if item['size'] is not None and (type(item['size']) is not int or not 0 <= item['size'] <= 2**63 - 1):
            raise ValueError('Invalid queued file size.')
        item['held'] = raw.get('held', False)
        item['manual_order'] = raw.get('manual_order')
        if item['manual_order'] is not None and (type(item['manual_order']) is not int or not 0 <= item['manual_order'] <= 2**63 - 1):
            raise ValueError('Invalid manual queue order')
        item['scheduled_at'] = raw.get('scheduled_at')
        if item['scheduled_at'] is not None and (type(item['scheduled_at']) is not int or not 0 <= item['scheduled_at'] <= 253402300799):
            raise ValueError('Invalid scheduled queue time')
        if type(item['held']) is not bool: raise ValueError('Invalid queue hold flag')
        for key, default in (('source_side', 1), ('destination_side', 2)):
            item[key] = raw.get(key, default)
            if type(item[key]) is not int or item[key] not in (1, 2): raise ValueError('Invalid queue pane side')
        item['crc_job'] = raw.get('crc_job', False)
        item['crc_attempt'] = raw.get('crc_attempt', 0)
        item['crc_root'] = raw.get('crc_root', '')
        if 'sync' in raw:
            from .folder_sync import validate_spec
            item['sync'] = validate_spec(raw['sync'])
        if 'deployment' in raw:
            from .deployments import validate_job
            item['deployment'] = validate_job(raw['deployment'])
            profile = item['deployment']['profile']
            if item.get('sync') or item['source_site'] is not None or not item['destination_site'] or not item['directory'] or item['src'] != profile['source'] or item['dst'] != profile['destination']:
                raise ValueError('Deployment queue endpoints do not match the approved plan.')
        if 'watch' in raw:
            from .watch_upload import validate_tag
            item['watch'] = validate_tag(raw['watch'])
            if not item.get('sync') or item['sync']['source'] != item['watch']['fingerprint'] or item['sync']['relative'] != item['watch']['relative'] or item['sync'].get('operation', 'copy') != 'copy':
                raise ValueError('Watch metadata must match the guarded upload.')
        if type(item['crc_job']) is not bool or type(item['crc_attempt']) is not int or not 0 <= item['crc_attempt'] <= 999 or not isinstance(item['crc_root'], str):
            raise ValueError('Invalid CRC queue metadata')
        jobs.append(item)
    return jobs


def write_queue(path, jobs, cancel=None):
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write('{"version": 1, "jobs": [')
            for index, job in enumerate(jobs):
                if cancel and cancel.is_set(): raise Cancelled('Queue operation cancelled')
                if index: stream.write(',')
                stream.write(json.dumps(job, ensure_ascii=False))
            stream.write(']}\n')
            stream.flush()
            os.fsync(stream.fileno())
        if cancel and cancel.is_set(): raise Cancelled('Queue operation cancelled')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class QueueManager(BufferedQueue):
    def initialize_queue_storage(self, path=None, defer=False):
        self.deferred_queue = []
        if path is None:
            path = self.sites_file.with_name('queue.json')
            if self.preferences.format() == QSettings.Format.IniFormat:
                path = Path(self.preferences.fileName()).with_name('queue.json')
        self.queue_file = Path(path)
        self.queue_storage_enabled = True
        if not self.preferences.value('Queue/reload', True, type=bool) or not self.queue_file.exists():
            return
        try:
            if self.queue_file.stat().st_size > 1024 * 1024:
                from .queue_io import background
                self.queue_storage_enabled = False
                saved = background(self, 'Reading saved queue…', lambda worker: read_queue(self.queue_file, worker.cancel))
                self.queue_storage_enabled = True
            else:
                saved = read_queue(self.queue_file)
        except (OSError, ValueError, TypeError, Cancelled) as error:
            self.queue_storage_enabled = False
            self.log.appendPlainText(f'Queue recovery failed; saved file retained: {error}')
            return
        if defer:
            self.deferred_queue = saved
        else:
            self.restore_queue_items(saved)

    def show_saved_queue(self):
        if getattr(self, 'deferred_queue', []) and not getattr(self, '_smoke_test', False):
            from .queue_recovery import LoadQueueDialog
            LoadQueueDialog(self).exec()

    def restore_queue_items(self, saved):
        if len(saved) > BUFFER_LIMIT:
            self.restore_large_queue(saved)
            return
        options = TransferOptions.from_settings(self.preferences)
        for item in saved:
            while self.tabs.count() <= item['workspace']:
                self.add_workspace()
            source = Site(**item['source_site']) if item['source_site'] else None
            destination = Site(**item['destination_site']) if item['destination_site'] else None
            route = 'FXP' if source and destination else 'Upload' if destination else 'Download' if source else 'Copy'
            if route == 'FXP' and (source.cloud_account or destination.cloud_account or source.protocol == PROTOCOLS[3] or destination.protocol == PROTOCOLS[3]):
                route = 'Relay'
            state = 'failed' if item['state'] == 'failed' else 'queued'
            row = QTreeWidgetItem([item['name'], '0%', '—', '—', '—', item['src'], item['dst'], f'{route} · Restored {state}'])
            self.queue.addTopLevelItem(row)
            if item.get('size') is not None:
                row.setData(4, Qt.ItemDataRole.UserRole, item['size'])
                row.setText(4, self.file_size(item['size']))
            job = dict(deepcopy(item), row=row, source_site=source, destination_site=destination,
                       workspace=self.tabs.widget(item['workspace']), route=route, state=state,
                       needs_credentials=True, options=transfer_settings(self.preferences, source, destination))
            job['task'] = self.make_queue_task(job)
            if job.get('watch'): job['suppress_retry'] = True
            if job.get('crc_job'):
                job['options'] = replace(job['options'], exists_enabled=False, skip_enabled=False)
            self.queue_jobs.append(job)
            if job.get('queue_group'):
                self.attach_queue_row(job)
                row.setData(0, STATE_ROLE, state)
                update_group(self, row)
            if state == 'queued': self.pending.append(job)
        if saved:
            self.paused = True
            self.queue_batch_failed = any(j['state'] == 'failed' for j in self.queue_jobs)
            self.log.appendPlainText(f'Restored {len(saved)} unfinished queue item(s), paused. Press Play to continue.')

    def save_queue_state(self):
        if getattr(self, '_queue_commit', False): return False
        timer = getattr(self, '_queue_save_timer', None)
        if timer is not None:
            timer.stop()
        if not self.queue_storage_enabled or not self.preferences.value('Queue/auto_save', True, type=bool):
            return False
        if self.large_queue() or hasattr(self, '_queue_writer'):
            from .queue_io import writer
            output = writer(self)
            try:
                output.save()
                return True if getattr(self, '_queue_operation_depth', 0) else output.flush()
            except (OSError, ValueError, sqlite3.Error) as error:
                self.log.appendPlainText(f'Cannot save queue: {error}')
                return False
        try:
            write_queue(self.queue_file, self.iter_queue_snapshot())
            return True
        except OSError as error:
            self.log.appendPlainText(f'Cannot save queue: {error}')
            return False

    def schedule_queue_save(self):
        """Coalesce worker events; explicit pause/close/export still flush immediately."""
        if getattr(self, '_queue_commit', False): return
        from PySide6.QtCore import QTimer
        if (self.large_queue() or hasattr(self, '_queue_writer')) and self.queue_storage_enabled and self.preferences.value('Queue/auto_save', True, type=bool):
            from .queue_io import writer
            try: writer(self).save()
            except (OSError, ValueError, sqlite3.Error) as error: self.log.appendPlainText(f'Cannot save queue: {error}')
            return
        if not self.active and not self.pending:
            return self.save_queue_state()
        if not hasattr(self, '_queue_save_timer'):
            self._queue_save_timer = QTimer(self)
            self._queue_save_timer.setSingleShot(True)
            self._queue_save_timer.setInterval(500)
            self._queue_save_timer.timeout.connect(self.save_queue_state)
        if not self._queue_save_timer.isActive():
            self._queue_save_timer.start()

    def large_queue(self):
        return len(getattr(self, 'deferred_queue', ())) + sum(
            len(job['buffer']) if job.get('buffered') and job.get('state') == 'queued' else 1
            for job in self.queue_jobs) > BUFFER_LIMIT

    def queue_snapshot(self):
        return list(self.iter_queue_snapshot())

    def iter_queue_snapshot(self):
        keep_failed = self.preferences.value('Queue/keep_failed', True, type=bool)
        yield from getattr(self, 'deferred_queue', [])
        for job in sorted(self.queue_jobs, key=lambda job: bool(job.get('buffered'))):
            if job.get('buffered'):
                if job.get('state') == 'queued':
                    for item in job['buffer']:
                        item.update(workspace=max(0, self.tabs.indexOf(job['workspace'])))
                        item['queue_group'] = job['queue_group']
                        if job.get('manual_order') is not None: item['manual_order'] = job['manual_order']
                        if job.get('scheduled_at') is not None: item['scheduled_at'] = job['scheduled_at']
                        elif job.get('buffer_unschedule'): item['scheduled_at'] = None
                        if job.get('held'): item['held'] = True
                        elif job.get('buffer_release'): item['held'] = False
                        yield item
                continue
            if 'src' not in job or job.get('state') not in ('queued', 'active', 'failed'):
                continue
            if job['state'] == 'failed' and not keep_failed: continue
            item = {key: job[key] for key in ('src', 'dst', 'name', 'directory', 'resume', 'state')}
            item['retries'] = job.get('retries', 0)
            item['size'] = job['row'].data(4, Qt.ItemDataRole.UserRole)
            item['held'] = job.get('held', False)
            item['automatic'] = job.get('automatic', False)
            if job.get('queue_group'): item['queue_group'] = job['queue_group']
            item['manual_order'] = job.get('manual_order')
            item['scheduled_at'] = job.get('scheduled_at')
            item.update(source_side=job.get('source_side', 1), destination_side=job.get('destination_side', 2))
            item['workspace'] = max(0, self.tabs.indexOf(job['workspace']))
            item.update(crc_job=job.get('crc_job', False), crc_attempt=job.get('crc_attempt', 0), crc_root=job.get('crc_root', ''))
            if job.get('sync'): item['sync'] = job['sync']
            if job.get('watch'): item['watch'] = job['watch']
            if job.get('deployment'): item['deployment'] = job['deployment']
            for key in ('source_site', 'destination_site'):
                item[key] = asdict(job[key]) if job[key] else None
            yield item

    def make_queue_task(self, job):
        from .queue_prepare import expansion_template, expanded
        # Cloud uploads walk and transfer one directory at a time. Keeping the
        # root job active also preserves unvisited subfolders in recovery.
        job['streaming_folder'] = bool(job.get('directory') and not job.get('sync') and not job.get('deployment') and
            not job.get('source_site') and job.get('destination_site') and job['destination_site'].cloud_account)
        template = expansion_template(job, max(0, self.tabs.indexOf(job['workspace']))) if job.get('directory') else None
        def task(worker):
            from contextlib import ExitStack
            from .crc_check import CRCMismatch
            skipped, renamed, crc_errors, messages = [], [], [], []
            skipped_directories = set()
            def renamed_file(old, new):
                from .verification_reports import renamed_destination
                renamed.append((old, new))
                renamed_destination(new)
            def skipped_file(path, reason):
                from .verification_reports import skipped_entry
                skipped.append((path, reason))
                skipped_entry(path, reason)
            planned = DiskBuffer(rank=lambda item: job['options'].priority(Path(item[0]).name)) if job.get('directory') and not job.get('sync') and not job.get('deployment') and not job['streaming_folder'] else None
            def file_started(src, dst):
                if self.paused: raise FolderPaused()
                worker.begin_file()
                worker.file_started.emit(src, dst)
            try:
                if worker.cancel.is_set(): raise Cancelled('Cancelled')
                with ExitStack() as stack:
                    connections = []
                    for key in ('source', 'destination'):
                        site = job[key + '_site']
                        if site and (job.get('sync') or job.get('deployment')): site = replace(site, timezone='')
                        from .site_connections import transfer_connection
                        connection = transfer_connection(self, site, job.get(key + '_password', ''), 'download' if key == 'source' else 'upload') if site else None
                        if connection:
                            if hasattr(connection, 'trace'):
                                connection.trace.display_side = job.get(key + '_side', getattr(connection.trace, 'display_side', 1 if key == 'source' else 2))
                            connection.transfer_options = job['options']
                            connection = stack.enter_context(connection)
                        connections.append(connection)
                        if connection:
                            connection.transfer_notice = messages.append
                    source, destination = connections
                    if job.get('deployment'):
                        from .deployments import execute
                        return execute(self, destination, job, worker)
                    if job.get('sync'):
                        from .folder_sync import execute_sync
                        result = execute_sync(source, destination, job, worker)
                        result.setdefault("messages", []).extend(messages)
                        return result
                    if source and not destination:
                        import posixpath
                        from .crc_check import CRCMismatch
                        source.crc_root = job.get('crc_root') or (job['src'] if job['directory'] else posixpath.dirname(job['src']) or '/')
                    try:
                        result = transfer_tree(*connections, job['src'], job['dst'], job['directory'], worker.cancel,
                            worker.report, job['resume'], depth=1 if job.get('automatic') else 0, options=job['options'],
                            skipped=skipped_file, renamed=renamed_file, planned=planned,
                            skipped_directories=skipped_directories,
                            conflict=lambda details: self.conflicts.ask(details, worker.cancel), notice=messages.append,
                            file_started=file_started if job['streaming_folder'] else None)
                    except CRCMismatch as error:
                        crc_errors.append(dict(src=error.remote, dst=error.local, message=str(error)))
                        result = False
                    if worker.cancel.is_set(): raise Cancelled('Cancelled')
                if planned is not None and len(planned) > BUFFER_LIMIT:
                    planned = expanded(template, planned, job['options'], worker.cancel)
                return {'transferred': result, 'skipped': skipped, 'renamed': renamed, 'crc_errors': crc_errors, 'messages': messages,
                        'planned': planned, 'skipped_directories': skipped_directories}
            except FolderPaused:
                return {'paused': True}
            except Exception:
                if planned is not None: planned.close()
                raise
        from .verification_reports import reported_task
        return reported_task(self, job, task)

    def expand_queue_files(self, parent, files):
        import posixpath
        if len(files) > BUFFER_LIMIT or len(files) + len(self.pending) > BUFFER_LIMIT:
            if getattr(files, 'descriptors', False):
                spool = files
            else:
                from .queue_prepare import expansion_template, expanded
                from .queue_io import background
                template = expansion_template(parent, max(0, self.tabs.indexOf(parent['workspace'])))
                spool = background(self, 'Preparing folder queue…',
                    lambda worker: expanded(template, files, parent['options'], worker.cancel))
            self.install_queue_buffer(parent, spool)
            self.show_transfer_queue()
            self.save_queue_state()
            return
        group = dict(id=uuid.uuid4().hex, name=parent.get('name', parent['row'].text(0)), src=parent['src'], dst=parent['dst'])
        parent.update(queue_group=group, queue_group_parent=True)
        parent['row'].setData(0, GROUP_ROLE, group['id'])
        for src, dst, size in files:
            name = posixpath.basename(src) if parent['source_site'] else Path(src).name
            row = QTreeWidgetItem([name, '0%', '—', '—', self.file_size(size) if size is not None else '—',
                                  src, dst, parent['route'] + ' · Queued'])
            if size is not None: row.setData(4, Qt.ItemDataRole.UserRole, size)
            parent['row'].addChild(row)
            job = dict(parent, row=row, src=src, dst=dst, directory=False, automatic=True,
                       name=name, state='queued', retries=0, batch_active=True,
                       queue_group_parent=False,
                       crc_root=parent.get('crc_root') or parent['src'])
            job['task'] = self.make_queue_task(job)
            self.queue_jobs.append(job)
            self.pending.append(job)
        parent['row'].setText(7, f"{parent['route']} · Expanded: {len(files)} files")
        if isinstance(files, DiskBuffer): files.close()
        parent['row'].setExpanded(True)
        update_group(self, parent['row'])
        self.show_transfer_queue()
        self.save_queue_state()

    def restore_large_queue(self, saved):
        from .queue_io import background
        from .queue_prepare import Settings, prepare, install
        settings = Settings(self.preferences)
        prepared = background(self, 'Preparing recovery queue…', lambda worker: prepare(saved, settings, worker.cancel))
        install(self, prepared)

    def attach_queue_row(self, job):
        from .queue_tree import detach
        row, group = job['row'], job.get('queue_group')
        detach(self.queue, row)
        if not group:
            self.queue.addTopLevelItem(row)
            return
        parent = next((self.queue.topLevelItem(i) for i in range(self.queue.topLevelItemCount())
                       if self.queue.topLevelItem(i).data(0, GROUP_ROLE) == group['id']), None)
        if job.get('queue_group_parent'):
            if parent is not None:
                row.addChildren(parent.takeChildren())
                detach(self.queue, parent)
            self.queue.addTopLevelItem(row)
            row.setExpanded(True)
            return
        if parent is None:
            parent = QTreeWidgetItem([group['name'], '', '', '', '', group['src'], group['dst'], 'Folder'])
            parent.setData(0, GROUP_ROLE, group['id'])
            self.queue.addTopLevelItem(parent)
            parent.setExpanded(True)
        parent.addChild(row)
        update_group(self, parent)

    def show_transfer_queue(self):
        if getattr(self, 'docking', None) and self.docking.active:
            self.docking.show_panel('queue')
            return
        self.queue_panel.show()
        self.queue.show()
        sizes = self.work_area.sizes()
        index = self.work_area.indexOf(self.queue_panel)
        if getattr(self, '_manual_panel_layout', False):
            return
        total = sum(sizes) or self.work_area.height()
        desired = max(240, total // 2)
        if sizes[index] < desired:
            remaining = max(0, total - desired)
            others = sum(size for i, size in enumerate(sizes) if i != index) or 1
            self.work_area.setSizes([desired if i == index else int(remaining * size / others)
                                     for i, size in enumerate(sizes)])

    def clear_finished_download(self, job):
        """Clear successes after reporting, retaining folder totals until it finishes."""
        if job.get('route') != 'Download' or job.get('state') != 'complete' or job.get('transfer_outcome', 'complete') not in ('complete', 'skipped'):
            return
        group = job.get('queue_group')
        if not group:
            if job.get('transfer_outcome', 'complete') == 'complete':
                self.discard_queue_jobs([job])
            return
        members = [member for member in self.queue_jobs if member.get('queue_group', {}).get('id') == group['id']]
        if all(member.get('state') == 'complete' and member.get('transfer_outcome', 'complete') in ('complete', 'skipped') for member in members):
            self.discard_queue_jobs(members)
        elif job in self.queue_jobs and not job.get('queue_group_parent') and job.get('transfer_outcome', 'complete') == 'complete':
            # Keep the row's byte counts for progress and the final release log.
            job['row'].setSelected(False)
            job['row'].setHidden(True)

    def clear_skipped_folder(self, job):
        if job.get('directory') and job.get('state') == 'complete' and job.get('transfer_outcome') == 'skipped':
            self.discard_queue_jobs([job])
            return
        group = job.get('queue_group')
        if not group or job.get('transfer_outcome') != 'skipped':
            return
        from .queue_buffer import BUFFER_ROLE
        members = [member for member in self.queue_jobs if member.get('queue_group', {}).get('id') == group['id']]
        files = [member for member in members if not member.get('directory')]
        if files and all(member.get('state') == 'complete' for member in members) and all(
                member.get('transfer_outcome') == 'skipped' for member in files) and not any(
                (member['row'].data(0, BUFFER_ROLE) or {}).get('completed') for member in members):
            self.discard_queue_jobs(members)

    def register_crc_failures(self, parent, errors):
        import posixpath
        for error in errors:
            if parent.get('crc_job'):
                job = parent
            else:
                row = QTreeWidgetItem([posixpath.basename(error['src']), '—', '—', '—', '—', error['src'], error['dst'], 'Download · CRC failed'])
                job = dict(parent, row=row, src=error['src'], dst=error['dst'], directory=False,
                           name=posixpath.basename(error['src']), resume=False, crc_job=True, crc_attempt=0,
                           crc_root=parent.get('crc_root') or (parent['src'] if parent['directory'] else posixpath.dirname(parent['src']) or '/'),
                           options=replace(parent['options'], exists_enabled=False, skip_enabled=False))
                self.attach_queue_row(job)
                row.setData(4, Qt.ItemDataRole.UserRole, parent['row'].data(4, Qt.ItemDataRole.UserRole))
                row.setData(0, RETRY_ROLE, True)
                job['task'] = self.make_queue_task(job)
                self.queue_jobs.append(job)
            job['state'] = 'failed'
            job['row'].setData(0, STATE_ROLE, 'failed')
            job['row'].setText(7, 'Download · CRC failed')
            job['row'].setToolTip(7, error['message'])
            if not job.get('suppress_retry') and job.get('crc_attempt', 0) < job['options'].crc_retries:
                job['crc_attempt'] = job.get('crc_attempt', 0) + 1
                job['state'] = 'queued'
                job['row'].setData(0, STATE_ROLE, 'queued')
                job['row'].setText(7, f"Download · CRC retry {job['crc_attempt']} queued")
                self.pending.append(job)

    def prepare_restored_jobs(self, jobs=None):
        passwords = {}
        for job in (self.queue_jobs if jobs is None else jobs):
            if not job.get('needs_credentials') or job.get('state') not in ('queued', 'failed'): continue
            for key in ('source', 'destination'):
                site = job[key + '_site']
                if site is None: continue
                identity = self._transfer_site_key(site)
                if identity not in passwords:
                    if not self.ensure_proxy_credentials(self, site.host, site): return False
                    password = ''
                    profile = self.sites.get(site.name)
                    if profile and self._transfer_site_key(self.site_from_profile(profile)) == identity:
                        try: password = self.get_site_password(profile)
                        except RuntimeError: pass
                    if site.username.casefold() == 'anonymous':
                        password = password or self.preferences.value('General/anonymous_email', 'anonymous@fluxftp.local', type=str)
                    elif not password and not site.cloud_account and not (site.managed_key and site.protocol == 'SFTP · SSH'):
                        password, accepted = QInputDialog.getText(self, 'Resume saved queue',
                            f'Password or key passphrase for {site.username}@{site.host}:', QLineEdit.EchoMode.Password)
                        if not accepted: return False
                    passwords[identity] = password
                job[key + '_password'] = passwords[identity]
            job['needs_credentials'] = False
        return True

    def retry_limit(self, job):
        route = job['route'].lower() if job['route'] != 'Copy' else 'download'
        if route == 'relay':
            route = 'fxp'
        default = {'upload': 2, 'download': 5, 'fxp': 0}[route]
        return max(0, min(999, self.preferences.value('Queue/retry_' + route, default, type=int)))

    def retry_job(self, job):
        if job.get('watch') or job.get('deployment'): return False
        if job.get('crc_job'): return False
        if job.get('suppress_retry') or job.get('retries', 0) >= self.retry_limit(job): return False
        job['retries'] = job.get('retries', 0) + 1
        job['state'] = 'queued'
        job['batch_active'] = True
        job['row'].setText(7, f"{job['route']} · Retry {job['retries']}/{self.retry_limit(job)} queued")
        self.pending.append(job)
        return True

    def reset_failed_jobs(self, automatic=False):
        if automatic and not self.preferences.value('Queue/reset_failed', False, type=bool): return
        for job in self.queue_jobs:
            if job.get('state') == 'failed':
                job.update(state='queued', retries=0, crc_attempt=0, suppress_retry=False, batch_active=True)
                self.pending.append(job)
                job['row'].setText(7, f"{job['route']} · Queued")

    def clear_site_queue(self, pane, site, logout=False):
        option = 'clear_logout' if logout else 'clear_switch'
        if site is None or not self.preferences.value('Queue/' + option, False, type=bool): return
        identity = self._transfer_site_key(site)
        for job in self.queue_jobs:
            if identity not in (self._transfer_site_key(job['source_site']), self._transfer_site_key(job['destination_site'])):
                continue
            if job.get('state') == 'active':
                job['suppress_retry'] = True
                continue
            if job.get('state') in ('queued', 'failed'):
                if job.get('state') == 'queued': self.transfer_history.cancelled([job])
                job['state'] = 'cancelled'
                job['row'].setText(7, f"{job['route']} · Cleared")
                if job in self.pending: self.pending.remove(job)
        self.save_queue_state()

    def disconnect_pane(self, pane):
        if pane.busy: return
        pane.browse_session.close()
        if pane.site or pane.remote_site:
            self.directory_cache.clear(pane.site or pane.remote_site)
        self.clear_site_queue(pane, pane.site or pane.remote_site, logout=True)
        pane.site = pane.remote_site = None
        pane.password = pane.remote_password = ''
        pane.mode = 'Remote'
        pane._set_mode_button('Remote')
        pane.generation += 1
        pane.tree.clear()
        pane.title_label.setText(f'({pane.number}) Remote')
        pane.status.setText('Disconnected')
        self.apply_display_settings()
