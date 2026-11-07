"""Transfer submission and queue lifecycle operations for the desktop window."""
import os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QTreeWidgetItem

from .core import Entry, safe_name
from .site_options import transfer_settings


class TransferQueue:
    """Queue-facing window behavior, implemented against the window interface."""
    def enqueue(self, right):
        split = self.tabs.currentWidget()
        source, dest = (split.widget(0), split.widget(1)) if right else (split.widget(1), split.widget(0))
        self.enqueue_items(source, dest, source.selected())

    def enqueue_local_paths(self, dest, paths, start=None):
        entries, sources = [], {}
        try:
            for path in dict.fromkeys(paths):
                item = Path(path)
                if not item.is_absolute() or item.is_symlink() or not (item.is_file() or item.is_dir()):
                    raise ValueError("Drop existing local files or folders; symbolic links are not supported.")
                safe_name(item.name)
                if item.name in sources:
                    raise ValueError("Dropped items must have distinct names in the destination folder.")
                sources[item.name] = str(item)
                entries.append(Entry(item.name, item.is_dir(), item.stat().st_size))
        except (OSError, ValueError) as error:
            self.error(str(error))
            return
        source = SimpleNamespace(mode="Local", site=None, password="", busy=False, join=sources.__getitem__)
        self.enqueue_items(source, dest, entries, start=start)

    def enqueue_items(self, source, dest, entries, start=None, confirmed=False):
        split = dest.parentWidget()
        if source.busy or dest.busy or not entries:
            return
        if dest.mode == 'Remote' and dest.site:
            self.directory_cache.clear(dest.site)
        source_remote, dest_remote = source.mode == "Remote", dest.mode == "Remote"
        route = "FXP" if source_remote and dest_remote else "Upload" if dest_remote else "Download" if source_remote else "Copy"
        try:
            options = transfer_settings(self.preferences, source.site if source_remote else None, dest.site if dest_remote else None)
            names = [options.filename(entry.name, route) for entry in entries]
            compared = [name.casefold() for name in names] if not dest_remote and os.name == "nt" else names
            if len(compared) != len(set(compared)):
                raise ValueError("Filename conversion would overwrite another selected file.")
        except ValueError as error:
            self.error(str(error))
            return
        if source_remote and source.site is None:
            self.error("Connect the source remote pane before transferring files.")
            return
        if dest_remote and dest.site is None:
            self.error("Connect the destination remote pane before transferring files.")
            return
        if any(e.is_link for e in entries):
            self.error("Symbolic links are not transferred in this build.")
            return
        if not confirmed and self.preferences.value("Confirm/queue", True, type=bool) and QMessageBox.question(self, "Queue transfers", f"Transfer {len(entries)} file(s)? FluxFTP checks existing content before resuming and will ask when a match cannot be verified.") != QMessageBox.StandardButton.Yes:
            return
        if not self.active and not self.pending:
            self.queue_batch_failed = False
            for previous_job in self.queue_jobs:
                previous_job['batch_active'] = False
            if start is not False:
                self.reset_failed_jobs(automatic=True)
        from .queue_buffer import DiskBuffer, BUFFER_LIMIT
        large_selection = len(entries) + len(self.pending) > BUFFER_LIMIT
        selection_buffer = DiskBuffer(rank=lambda item: options.priority(item['name'])) if large_selection else None
        buffer_parent = None
        created_jobs = []
        for entry, target_name in zip(entries, names):
            try:
                src, dst = source.join(entry.name), dest.join(target_name)
            except ValueError as e:
                self.error(str(e))
                continue
            src_site = source.site if source_remote else None
            dst_site = dest.site if dest_remote else None
            if src_site and dst_site and (src_site.host.casefold(), src_site.port, src_site.username) == (dst_site.host.casefold(), dst_site.port, dst_site.username) and (src == dst or (entry.is_dir and dst.startswith(src.rstrip("/") + "/"))):
                self.error("Source and destination overlap on the same server.")
                continue
            if not source_remote and not dest_remote:
                source_path, destination_path = Path(src).resolve(), Path(dst).resolve()
                if source_path == destination_path or (entry.is_dir and destination_path.is_relative_to(source_path)):
                    self.error("The local destination cannot be the source itself or a folder inside the source.")
                    continue
            src_password, dst_password = source.password, dest.password
            resume = self.resume.isChecked()
            from .protocol_adapter import transfer_route
            route = transfer_route(src_site, dst_site)
            row = QTreeWidgetItem([entry.name, "0%", "—", "—", self.file_size(entry.size), src, dst, f"{route} · Queued"])
            row.setData(4, Qt.ItemDataRole.UserRole, entry.size)
            if selection_buffer is None or entry.is_dir:
                self.queue.addTopLevelItem(row)
            job = {
                "row": row, "route": route, "workspace": split,
                "source_site": src_site, "destination_site": dst_site,
                "source_password": src_password, "destination_password": dst_password,
                "source_side": int(getattr(source, "number", 1)), "destination_side": int(getattr(dest, "number", 2)),
                "src": src, "dst": dst, "directory": entry.is_dir, "resume": resume,
                "name": entry.name, "options": options, "state": "queued", "retries": 0, "held": start is False,
            }
            if selection_buffer is not None and not entry.is_dir:
                selection_buffer.append(dict(src=src, dst=dst, name=entry.name, size=entry.size,
                    directory=False, resume=resume, state='queued', retries=0, held=start is False,
                    source_site=asdict(src_site) if src_site else None,
                    destination_site=asdict(dst_site) if dst_site else None,
                    workspace=self.tabs.indexOf(split), source_side=job['source_side'], destination_side=job['destination_side']))
                if buffer_parent is None:
                    buffer_parent = self.new_queue_job(job)
                    buffer_parent.update(name='Selected files', src=source.path, dst=dest.path)
                    row.setText(0, 'Selected files')
                    row.setText(5, source.path)
                    row.setText(6, dest.path)
                    self.queue.addTopLevelItem(row)
                    self.queue_jobs.append(buffer_parent)
                    created_jobs.append(buffer_parent)
                continue
            job = self.new_queue_job(job)
            job["task"] = self.make_queue_task(job)
            self.queue_jobs.append(job)
            self.pending.append(job)
            created_jobs.append(job)
            if start is False:
                row.setText(7, f'{route} · Held — press Play')
        if buffer_parent is not None:
            self.install_queue_buffer(buffer_parent, selection_buffer)
        elif selection_buffer is not None:
            selection_buffer.close()
        self.save_queue_state()
        if start is True:
            self.paused = False
        self.show_transfer_queue()
        self.start_next()
        return created_jobs

    @staticmethod
    def _transfer_site_key(site):
        from .queue_engine import site_key
        return site_key(site)

    def _transfer_record(self, job):
        record = self.queue_engine.transfer_record(job)
        record.update(row=job['row'], task=job['task'])
        return record

    def _can_start_transfer(self, job):
        return self.queue_engine.can_start(job, self.queue_controller.policy())

    def start_next(self):
        return self.queue_controller.start_next()

    def transfer_finished(self, transfer_id):
        return self.queue_controller.transfer_finished(transfer_id)

    def _apply_completion_action(self):
        return self.queue_controller._apply_completion_action()

    def play_queue(self):
        return self.queue_controller.play_queue()

    def stop_queue(self):
        return self.queue_controller.stop_queue()

    def toggle_pause(self):
        return self.queue_controller.toggle_pause()

    def cancel_selected(self):
        return self.queue_controller.cancel_selected()

