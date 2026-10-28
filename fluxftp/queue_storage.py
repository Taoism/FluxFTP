"""Queue persistence independent of widgets, dialogs and worker threads.

Callers provide captured credential-free job descriptors, keyed by stable job ID.
The desktop adapter owns workspace-to-tab mapping and asynchronous scheduling.
"""
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from dataclasses import asdict
from threading import Lock
from .connection_model import Site, Cancelled
from .site_import import validate_site


def checked(cancel):
    if cancel.is_set(): raise Cancelled('Queue operation cancelled')


class StorageSnapshot:
    """Captured descriptors and pinned disk readers; never holds widget references."""
    def __init__(self):
        self.parts = []
        self.count = 0
        self.closed = False

    def add_items(self, items):
        items = tuple(items)
        if items:
            self.parts.append(('items', items, None))
            self.count += len(items)

    def add_buffer(self, buffer, overrides):
        reader = buffer.open_reader()
        try:
            reader.db.execute('BEGIN')
            reader.db.execute('SELECT id FROM items LIMIT 1').fetchone()
        except Exception:
            reader.close()
            raise
        self.parts.append(('buffer', reader, dict(overrides)))
        self.count += len(buffer)

    def __iter__(self):
        for kind, source, overrides in self.parts:
            if kind == 'items':
                yield from source
            else:
                for payload, in source.db.execute('SELECT payload FROM items ORDER BY priority,position,id'):
                    item = json.loads(payload)
                    item.update(overrides)
                    yield item

    def close(self):
        if self.closed: return
        self.closed = True
        for kind, source, _ in self.parts:
            if kind == 'buffer': source.close()
        self.parts.clear()

    def __enter__(self): return self

    def __exit__(self, *_): self.close()


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
        if isinstance(item, dict) and 'src' in item and 'state' in item:
            from .job_model import job_id
            item = dict(item, job_id=job_id(item.get('job_id')))
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


def read_queue(path, cancel=None):
    from .queue_json import queue_items
    from .job_model import job_id
    jobs = []
    identities = set()
    for raw in queue_items(path, cancel):
        if not isinstance(raw, dict): raise ValueError('Invalid queue job.')
        item = {}
        item['job_id'] = job_id(raw.get('job_id'))
        if item['job_id'] in identities: raise ValueError('Duplicate queue job ID.')
        identities.add(item['job_id'])
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
            from .queue_metadata import validate_spec
            item['sync'] = validate_spec(raw['sync'])
        if 'deployment' in raw:
            from .queue_metadata import validate_job
            item['deployment'] = validate_job(raw['deployment'])
            profile = item['deployment']['profile']
            if item.get('sync') or item['source_site'] is not None or not item['destination_site'] or not item['directory'] or item['src'] != profile['source'] or item['dst'] != profile['destination']:
                raise ValueError('Deployment queue endpoints do not match the approved plan.')
        if 'watch' in raw:
            from .queue_metadata import validate_tag
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


def import_file(path, cancel):
    import os
    import posixpath
    from pathlib import Path
    saved = read_queue(path, cancel)
    def identity(site):
        return (site['host'].casefold(), site['port'], site['username'].casefold(), site['protocol'])
    for item in saved:
        checked(cancel)
        for key in ('source', 'destination'):
            value = item['src' if key == 'source' else 'dst']
            if item[key + '_site']:
                if not value.startswith('/') or '..' in value.split('/'):
                    raise ValueError('Remote queue paths must be absolute without parent-directory segments.')
            elif not Path(value).is_absolute():
                raise ValueError('Local queue paths must be absolute.')
        if not item['source_site'] and not item['destination_site']:
            src, dst = (os.path.normcase(os.path.abspath(item[key])) for key in ('src', 'dst'))
            if src == dst or (item['directory'] and dst.startswith(src.rstrip(os.sep) + os.sep)):
                raise ValueError('Local queue source and destination overlap.')
        elif item['source_site'] and item['destination_site'] and identity(item['source_site']) == identity(item['destination_site']):
            src, dst = (posixpath.normpath(item[key]) for key in ('src', 'dst'))
            if src == dst or (item['directory'] and dst.startswith(src.rstrip('/') + '/')):
                raise ValueError('Remote queue source and destination overlap.')
    return saved
