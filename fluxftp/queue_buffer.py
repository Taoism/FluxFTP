"""Private disk spools; portable queue snapshots remain credential-free JSON."""
import json
import os
import sqlite3
import tempfile
from threading import Lock

BUFFER_LIMIT = 256
BUFFER_ROLE = 306


class BufferReader:
    def __init__(self, owner):
        self.owner = owner
        self.db = sqlite3.connect(owner.path, check_same_thread=False)
        self.db.execute('PRAGMA query_only=ON')
        with owner._reader_lock:
            owner._readers += 1

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
            with self.owner._reader_lock:
                self.owner._readers -= 1
                self.owner._retire_path()


class DiskBuffer:
    def __init__(self, rank=None):
        fd, self.path = tempfile.mkstemp(prefix='fluxftp-queue-', suffix='.sqlite')
        os.close(fd)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self._reader_lock = Lock()
        self._readers = 0
        self._retired_path = None
        self.db.execute('PRAGMA cache_size=-512')
        self.db.execute('CREATE TABLE items (id INTEGER PRIMARY KEY, priority INTEGER, position INTEGER, payload TEXT)')
        self.db.execute('CREATE INDEX ordering ON items(priority, position, id)')
        self.count = 0
        self.bytes = 0
        self.rank = rank or (lambda item: 0)

    def append(self, item):
        rank = self.rank(item)
        if not isinstance(rank, tuple): rank = (1, rank)
        self.db.execute('INSERT INTO items(priority,position,payload) VALUES (?,?,?)',
                        (*rank, json.dumps(item, ensure_ascii=False)))
        self.count += 1
        self.bytes += self.item_size(item)
        if self.count % 256 == 0:
            self.db.commit()

    def __len__(self):
        return self.count

    def waiting_counts(self):
        """Count pending flags on disk without loading descriptors into Python."""
        held, scheduled = self.db.execute("""SELECT
            SUM(COALESCE(json_extract(payload, '$.held'), 0) != 0),
            SUM(COALESCE(json_extract(payload, '$.scheduled_at'), 0) != 0)
            FROM items""").fetchone()
        return held or 0, scheduled or 0

    def __iter__(self):
        self.db.commit()
        for payload, in self.db.execute('SELECT payload FROM items ORDER BY priority,position,id'):
            yield json.loads(payload)

    def take(self, limit, *, eligible=False, release=False, unschedule=False):
        import time
        where, parameters = '', []
        if eligible:
            conditions = []
            if not release:
                conditions.append("COALESCE(json_extract(payload,'$.held'),0)=0")
            if not unschedule:
                conditions.append("COALESCE(json_extract(payload,'$.scheduled_at'),0)<=?")
                parameters.append(time.time())
            if conditions: where = ' WHERE ' + ' AND '.join(conditions)
        rows = self.db.execute('SELECT id,payload FROM items' + where + ' ORDER BY priority,position,id LIMIT ?', (*parameters, limit)).fetchall()
        # Decode before removal. The caller owns these descriptors until snapshot.
        items = [json.loads(payload) for _, payload in rows]
        try:
            self.db.executemany('DELETE FROM items WHERE id=?', ((key,) for key, _ in rows))
            self.db.commit()
        except sqlite3.Error:
            self.db.rollback()
            raise
        self.count -= len(rows)
        self.bytes -= sum(self.item_size(item) for item in items)
        return items

    @staticmethod
    def item_size(item):
        return (item.get('size') if isinstance(item, dict) else item[2]) or 0

    def open_reader(self):
        self.db.commit()
        return BufferReader(self)

    def _retire_path(self):
        if not self._readers and self._retired_path:
            try:
                os.unlink(self._retired_path)
            except FileNotFoundError:
                pass
            self._retired_path = None

    def close(self):
        db = getattr(self, 'db', None)
        if db is not None:
            db.close()
            self.db = None
        path = getattr(self, 'path', None)
        if path:
            with self._reader_lock:
                self._retired_path = path
                self.path = None
                self._retire_path()

    def __del__(self):
        self.close()


class BufferedQueue:
    @staticmethod
    def release_queue_buffer(job, unschedule=False):
        if job.get('buffered'):
            job['buffer_release'] = True
            if unschedule:
                job['buffer_unschedule'] = True

    def install_queue_buffer(self, parent, descriptors):
        import uuid
        from .queue_tree import GROUP_ROLE
        group = parent.get('queue_group') or dict(id=uuid.uuid4().hex,
            name=parent['name'], src=parent['src'], dst=parent['dst'])
        parent.update(queue_group=group, queue_group_parent=True, buffer=descriptors,
                      directory=True, state='queued', buffered=True)
        parent['row'].setData(0, GROUP_ROLE, group['id'])
        parent['row'].setExpanded(True)
        parent['row'].setData(0, BUFFER_ROLE, dict(completed=0, bytes=0, remaining=len(descriptors), remaining_bytes=descriptors.bytes))
        parent['row'].setToolTip(7, 'Pending files are buffered on disk. Select this folder to control the whole queue. Completed files are summarized here.')
        if parent not in self.pending:
            self.pending.append(parent)
        self.refill_queue_buffers()

    def refill_queue_buffers(self):
        import time
        from .transfer_reporting import update_group
        active_ids = {id(record.get('job')) for record in self.active.values()}
        capacity = max(0, BUFFER_LIMIT - sum(not j.get('buffered') and
            (j.get('state') in ('queued', 'active') or id(j) in active_ids) for j in self.queue_jobs))
        parents = [j for j in self.queue_jobs if j.get('buffered') and j.get('state') == 'queued']
        last = getattr(self, '_last_buffer_filled', None)
        for index, parent in enumerate(parents):
            if id(parent) == last:
                parents = parents[index + 1:] + parents[:index + 1]
                break
        for parent in parents:
            spool = parent['buffer']
            if not parent.get('held') and not parent.get('needs_credentials') and not parent.get('editing') and (parent.get('scheduled_at') or 0) <= time.time():
                try:
                    items = spool.take(capacity, eligible=True, release=parent.get('buffer_release', False),
                                       unschedule=parent.get('buffer_unschedule', False) or parent.get('scheduled_at') is not None)
                except (OSError, sqlite3.Error) as error:
                    self.paused = True
                    self.log.appendPlainText(f'Queue paused: cannot read its disk buffer: {error}')
                    self.statusBar().showMessage('Queue paused because its disk buffer could not be read.')
                    return
                for item in items:
                    self.restore_buffered_child(parent, item)
                    capacity -= 1
                if items:
                    self._last_buffer_filled = id(parent)
            if not len(spool):
                parent['state'] = 'complete'
                if parent in self.pending:
                    self.pending.remove(parent)
            meta = parent['row'].data(0, BUFFER_ROLE)
            meta['remaining'] = len(spool)
            meta['remaining_bytes'] = spool.bytes
            parent['row'].setData(0, BUFFER_ROLE, meta)
            update_group(self, parent['row'])

    def restore_buffered_child(self, parent, item):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTreeWidgetItem
        from .queue_tree import STATE_ROLE
        row = QTreeWidgetItem([item['name'], '0%', '—', '—', self.file_size(item['size']) if item.get('size') is not None else '—',
                              item['src'], item['dst'], parent['route'] + ' · Queued'])
        row.setData(4, Qt.ItemDataRole.UserRole, item.get('size'))
        job = dict(parent)
        for key in ('buffer', 'buffered', 'task', 'transfer_started', 'suppress_retry', 'transfer_outcome', 'sync'):
            job.pop(key, None)
        job.update({key: value for key, value in item.items() if key not in ('source_site', 'destination_site', 'workspace', 'queue_group')})
        job.update(row=row, queue_group=parent['queue_group'], queue_group_parent=False,
                   batch_active=True, state='failed' if item.get('state') == 'failed' else 'queued')
        if job.get('crc_job'):
            from dataclasses import replace
            job['options'] = replace(job['options'], exists_enabled=False, skip_enabled=False)
        if parent.get('manual_order') is not None:
            job['manual_order'] = parent['manual_order']
        if parent.get('scheduled_at') is not None:
            job['scheduled_at'] = parent['scheduled_at']
        if parent.get('held'):
            job['held'] = True
        elif parent.get('buffer_release'):
            job['held'] = False
        if parent.get('buffer_unschedule') and parent.get('scheduled_at') is None:
            job['scheduled_at'] = None
        job['task'] = self.make_queue_task(job)
        row.setData(0, STATE_ROLE, job['state'])
        parent['row'].addChild(row)
        self.queue_jobs.append(job)
        if job['state'] == 'queued':
            self.pending.append(job)
        return job

    def retire_buffered_success(self, job):
        from PySide6.QtCore import Qt
        from .transfer_reporting import update_group
        if job.get('buffered') or job.get('state') != 'complete' or job.get('transfer_outcome', 'complete') != 'complete':
            return
        row = job['row']
        parent = row.parent()
        if parent is None or parent.data(0, BUFFER_ROLE) is None:
            return
        meta = parent.data(0, BUFFER_ROLE)
        if not meta.get('started'):
            meta['started'] = job.get('transfer_started')
        meta['completed'] += 1
        meta['bytes'] += row.data(4, Qt.ItemDataRole.UserRole) or 0
        parent.setData(0, BUFFER_ROLE, meta)
        # Drop the task closure too: it otherwise retains the removed job/Qt row.
        job.pop('task', None)
        self.discard_queue_jobs([job])
        update_group(self, parent)
