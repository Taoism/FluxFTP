"""Prepare large queue buffers without touching Qt widgets or settings in workers."""
import json
import posixpath
import time
from itertools import chain
from pathlib import Path
from dataclasses import asdict
from PySide6.QtCore import QTimer, QEventLoop, Qt
from PySide6.QtWidgets import QProgressDialog
from .core import Site, Cancelled
from .queue_buffer import DiskBuffer
from .queue_io import checked, progress_parent
from .site_options import transfer_settings
from .transfer_options import TransferOptions, MISC_DEFAULTS


class Settings:
    def __init__(self, settings):
        defaults = TransferOptions()
        keys = {'Transfer/' + name: getattr(defaults, name) for name in defaults.__dataclass_fields__}
        keys['Transfer/skip_enabled'] = True
        keys.update({'Misc/' + name: value for name, value in MISC_DEFAULTS.items()})
        self.values = {key: settings.value(key, value, type=type(value)) for key, value in keys.items()}

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)


def expanded(template, files, options, cancel):
    spool = DiskBuffer(rank=lambda item: options.priority(item['name']))
    try:
        for src, dst, size in files:
            checked(cancel)
            spool.append(dict(template, src=src, dst=dst, size=size,
                name=posixpath.basename(src) if template['source_site'] else Path(src).name))
        spool.db.commit()
        spool.descriptors = True
        return spool
    except Exception:
        spool.close()
        raise
    finally:
        if isinstance(files, DiskBuffer): files.close()


def expansion_template(parent, workspace):
    return dict(src=parent['src'], dst=parent['dst'], name=parent['name'], directory=False,
        resume=parent['resume'], state='queued', automatic=True, retries=0, held=parent.get('held', False),
        source_site=asdict(parent['source_site']) if parent['source_site'] else None,
        destination_site=asdict(parent['destination_site']) if parent['destination_site'] else None,
        workspace=workspace, source_side=parent.get('source_side', 1), destination_side=parent.get('destination_side', 2),
        crc_root=parent.get('crc_root') or parent['src'])


class Prepared:
    def __init__(self):
        self.groups = {}
        self.singles = []

    def close(self):
        for _, spool in self.groups.values(): spool.close()
        self.groups.clear()


def prepare(saved, settings, cancel):
    prepared = Prepared()
    try:
        for item in saved:
            checked(cancel)
            if item['state'] == 'failed' or item['directory']:
                prepared.singles.append(item)
                continue
            key = json.dumps([item['workspace'], item['source_site'], item['destination_site'], item.get('queue_group')], sort_keys=True)
            if key not in prepared.groups:
                if len(prepared.groups) >= 512: raise ValueError('A queue import can contain at most 512 distinct site/folder groups.')
                options = transfer_settings(settings, Site(**item['source_site']) if item['source_site'] else None,
                                            Site(**item['destination_site']) if item['destination_site'] else None)
                prepared.groups[key] = (item, DiskBuffer(rank=lambda entry, options=options:
                    (0, entry['manual_order']) if entry.get('manual_order') is not None else (1, options.priority(entry['name']))))
            prepared.groups[key][1].append(item)
        for _, spool in prepared.groups.values(): spool.db.commit()
        return prepared
    except Exception:
        prepared.close()
        raise


def install(window, prepared):
    """Only bounded Qt work per event-loop turn; rollback on cancel/error."""
    from .queue_tree import GROUP_ROLE, detach
    existing = {id(job) for job in window.queue_jobs}
    was_paused = window.paused
    window.paused = True
    dialog = QProgressDialog('Adding queue items…', 'Cancel', 0, len(prepared.singles) + len(prepared.groups), progress_parent(window))
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    work = chain((('single', item) for item in prepared.singles), (('group', pair) for pair in prepared.groups.values()))
    errors, count = [], 0
    loop = QEventLoop()
    timer = QTimer(window)
    was_committing = getattr(window, '_queue_commit', False)
    window._queue_commit = True
    window._queue_operation_depth = getattr(window, '_queue_operation_depth', 0) + 1
    def tick():
        nonlocal count
        end = time.monotonic() + .008
        try:
            while time.monotonic() < end:
                if dialog.wasCanceled(): raise Cancelled('Queue operation cancelled')
                kind, value = next(work)
                if kind == 'single':
                    window.restore_queue_items([value])
                else:
                    first, spool = value
                    group = first.get('queue_group')
                    root = dict(first, directory=True, state='queued', name=group['name'] if group else 'Buffered queue',
                                crc_job=False, held=False, scheduled_at=None, manual_order=None)
                    if group: root.update(src=group['src'], dst=group['dst'])
                    window.restore_queue_items([root])
                    parent = window.queue_jobs[-1]
                    detach(window.queue, parent['row'])
                    for index in reversed(range(window.queue.topLevelItemCount())):
                        row = window.queue.topLevelItem(index)
                        if group and row.data(0, GROUP_ROLE) == group['id'] and not row.childCount():
                            window.queue.takeTopLevelItem(index)
                    window.queue.addTopLevelItem(parent['row'])
                    window.install_queue_buffer(parent, spool)
                count += 1
                dialog.setValue(count)
        except StopIteration:
            timer.stop()
            loop.quit()
        except Exception as error:
            errors.append(error)
            timer.stop()
            loop.quit()
    timer.timeout.connect(tick)
    dialog.show()
    timer.start(0)
    loop.exec()
    window._queue_operation_depth -= 1
    window._queue_commit = was_committing
    timer.deleteLater()
    dialog.close()
    dialog.deleteLater()
    if errors:
        window.discard_queue_jobs([job for job in window.queue_jobs if id(job) not in existing])
        window.paused = was_paused
        prepared.close()
        raise errors[0]
    prepared.groups.clear()  # Ownership moved to the live queue.
    window.queue_filter.schedule()
