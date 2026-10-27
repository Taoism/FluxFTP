"""Cloud operations exposed through the existing file-pane/queue interface."""
import os
import posixpath
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from threading import Event

from .cloud import cloud_path
from .core import Connection, Entry, Cancelled, safe_name


class CloudConnection(Connection):
    is_cloud = True

    def __init__(self, site, service, transfer_options=None, bandwidth=(0, 0)):
        super().__init__(site, transfer_options=transfer_options)
        self.service = service
        self.fs = ''
        self.bandwidth = bandwidth
        from .transfer_options import RateLimiter
        with self.service.lock if hasattr(self.service, 'lock') else nullcontext():
            if not hasattr(self.service, 'drive_upload_limiter'):
                self.service.drive_upload_limiter = RateLimiter()
            self.upload_limiter = self.service.drive_upload_limiter
            self.upload_limiter.configure(bandwidth[0])

    def connect(self):
        self.phase = 'cloud account access'
        self.fs = self.service.fs(self.site)
        self.service.start()
        upload, download = self.bandwidth
        manager = getattr(self.service, 'bandwidth_manager', None)
        if manager:
            manager.legacy_cloud = self.bandwidth
            manager.refresh()
        else:
            self.service.call('core/bwlimit', {'rate': ':'.join(f'{rate}B' if rate else 'off' for rate in (upload, download))})
        self.security = self.site.protocol + ' · cloud backend'
        self.trace.event('Cloud account selected: ' + self.site.name)

    def close(self):
        pass  # Shared backend remains available for other panes and transfer workers.

    def request(self, endpoint, path, *, cancel=None, **parameters):
        if not self.fs:
            self.connect()
        return self.service.job(endpoint, dict(fs=self.fs, remote=cloud_path(path), **parameters), cancel=cancel)

    def list(self, path, cancel=None):
        self.phase = 'cloud directory listing'
        result = self.request('operations/list', path, cancel=cancel, opt={'recurse': False})
        entries = []
        names = set()
        for item in result.get('list', []):
            name = item['Name']
            safe_name(name)
            if name in names:
                raise ValueError('This folder has duplicate names. Resolve them in the provider website before transferring.')
            names.add(name)
            entries.append(Entry(name, bool(item['IsDir']), max(0, int(item.get('Size', 0))), item.get('ModTime', '')))
        return entries

    def stat(self, path):
        return self.request('operations/stat', path).get('item')

    def size(self, path):
        item = self.stat(path)
        if item is None or item['IsDir']:
            raise OSError('Cloud source is not a file.')
        return max(0, int(item['Size']))

    def mkdir(self, path):
        if self.transfer_options.deny_parent_name and posixpath.basename(path.rstrip('/')).casefold() == posixpath.basename(posixpath.dirname(path.rstrip('/'))).casefold():
            raise ValueError('New directory cannot have the same name as its parent')
        return self.request('operations/mkdir', path)

    def remove(self, path, is_dir=False):
        if not cloud_path(path):
            raise ValueError('Cannot delete the cloud account root.')
        return self.request('operations/rmdir' if is_dir else 'operations/deletefile', path)

    def rename(self, old, new):
        old, new = cloud_path(old), cloud_path(new)
        if not old or not new or old == new or new.startswith(old.rstrip('/') + '/'):
            raise ValueError('Invalid cloud rename destination.')
        if self.stat(new) is not None:
            raise ValueError('The cloud rename destination already exists.')
        source = self.stat(old)
        if source is None:
            raise OSError('Cloud source no longer exists.')
        if source['IsDir']:
            if '/' not in old and self.site.protocol in ('Amazon S3', 'Backblaze B2', 'Google Cloud Storage', 'Microsoft Azure Blob Storage', 'OpenStack Swift'):
                raise ValueError('Rename folders inside a bucket/container; bucket names cannot be renamed here.')
            return self.service.job('sync/move', dict(srcFs=self.fs + old, dstFs=self.fs + new,
                createEmptySrcDirs=True, deleteEmptySrcDirs=True), timeout=0)
        return self.service.job('operations/movefile', dict(srcFs=self.fs, srcRemote=old, dstFs=self.fs, dstRemote=new))

    def chmod(self, path, mode):
        raise ValueError('Cloud providers do not expose FTP/Unix CHMOD here.')

    def set_modified(self, path, timestamp):
        raise ValueError('Manual cloud timestamp changes are not supported.')

    def read_file_bytes(self, path, cancel, limit):
        import tempfile
        if self.size(path) > limit:
            raise ValueError('Remote file exceeds the read limit.')
        with tempfile.TemporaryDirectory(prefix='fluxftp-cloud-read-') as directory:
            self.service.job('operations/copyfile', dict(srcFs=self.fs, srcRemote=cloud_path(path),
                dstFs=directory, dstRemote='content'), cancel=cancel)
            with (Path(directory) / 'content').open('rb') as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Remote file exceeds the read limit.')
            return data

    def transfer(self, local, remote, upload, cancel, progress, resume=False):
        manager = getattr(self.service, 'bandwidth_manager', None)
        if manager and upload and self.site.protocol == 'Google Drive':
            from .bandwidth import ManagedLimiter
            previous = self.upload_limiter
            self.upload_limiter = ManagedLimiter(manager, 0, previous)
            try:
                with manager.lease('native', [0]):
                    return self._transfer(local, remote, upload, cancel, progress, resume)
            finally: self.upload_limiter = previous
        return self._transfer(local, remote, upload, cancel, progress, resume)

    def _transfer(self, local, remote, upload, cancel, progress, resume=False):
        if not self.fs:
            self.connect()
        local, remote = Path(local), cloud_path(remote)
        if cancel.is_set():
            raise Cancelled('Cancelled')
        self.phase = 'cloud upload' if upload else 'cloud download'
        if upload:
            if local.is_symlink() or not local.is_file():
                raise ValueError('Cloud uploads require a regular local file.')
            if self.site.protocol == 'Google Drive':
                from .drive_upload import DriveUpload
                DriveUpload(self).upload(local, remote, cancel, progress)
                return
            if resume and self.stat(remote) is not None:
                raise ValueError('Cloud transfers restart whole files; byte-offset resume is not supported.')
            total = local.stat().st_size
            self.service.job('operations/copyfile', dict(srcFs=str(local.parent.resolve()), srcRemote=local.name,
                dstFs=self.fs, dstRemote=remote, _config={'IgnoreTimes': True}),
                cancel, progress, total, timeout=0)
        else:
            total = self.size(remote)
            partial = local.with_name(local.name + '.fluxpart')
            if local.is_symlink() or partial.is_symlink():
                raise ValueError('Cloud downloads cannot write through symbolic links.')
            if resume and partial.exists():
                raise ValueError('Cloud transfers restart whole files; byte-offset resume is not supported.')
            self.transfer_options.check_space(local, total)
            local.parent.mkdir(parents=True, exist_ok=True)
            def report(done, size):
                self.transfer_options.check_space(local, max(0, total - done))
                progress(done, size)
            self.service.job('operations/copyfile', dict(srcFs=self.fs, srcRemote=remote,
                dstFs=str(partial.parent.resolve()), dstRemote=partial.name, _config={'IgnoreTimes': True}),
                cancel, report, total, timeout=0)
            if cancel.is_set():
                raise Cancelled('Cancelled')
            if partial.stat().st_size != total:
                from .verification_reports import evidence
                evidence('size', 'mismatch', total, partial.stat().st_size)
                raise OSError('Cloud download size did not match the source.')
            from .verification_reports import evidence
            evidence('size', 'verified', total, partial.stat().st_size)
            from .crc_check import verify_download
            verify_download(self, partial, local, remote, cancel)
            if self.transfer_options.keep_file_time:
                item = self.stat(remote)
                if item and item.get('ModTime'):
                    stamp = datetime.fromisoformat(item['ModTime'].replace('Z', '+00:00')).timestamp()
                    os.utime(partial, (stamp, stamp))
            partial.replace(local)
        progress(total, total)

    def verify_skip(self, local, remote, cancel):
        if self.site.protocol == 'Google Drive':
            from .drive_upload import DriveUpload
            return DriveUpload(self).verified_same(local, cloud_path(remote), cancel)
        return True


def relay(source, destination, src, dst, cancel=None, progress=None, resume=False):
    cancel, progress = cancel or Event(), progress or (lambda *_: None)
    if resume:
        raise ValueError('Cloud relay transfers restart whole files; resume is unavailable.')
    if getattr(source, 'is_cloud', False) and getattr(destination, 'is_cloud', False) and source.service is destination.service:
        total = source.size(src)
        source.service.job('operations/copyfile', dict(srcFs=source.fs, srcRemote=cloud_path(src),
            dstFs=destination.fs, dstRemote=cloud_path(dst), _config={'IgnoreTimes': True}), cancel, progress, total, timeout=0)
    else:
        import tempfile
        with tempfile.TemporaryDirectory(prefix='fluxftp-cloud-relay-') as directory:
            local = Path(directory) / 'payload'
            total = source.size(src)
            source.transfer(local, src, False, cancel, lambda done, _: progress(done, total * 2))
            destination.transfer(local, dst, True, cancel, lambda done, _: progress(total + done, total * 2))
