"""Asynchronous rclone remote-control job execution and cancellation."""
import time
import uuid
from pathlib import Path
from threading import Event

from .cloud_registry import operation_error


class CloudJobs:
    """Run, monitor and stop cloud backend jobs."""

    def job(self, endpoint, parameters, cancel=None, progress=None, total=0, timeout=300):
        manager = getattr(self, 'bandwidth_manager', None)
        if manager and endpoint == 'operations/copyfile':
            self.start()
            directions = []
            if not Path(parameters.get('dstFs', '')).is_absolute(): directions.append(0)
            if not Path(parameters.get('srcFs', '')).is_absolute(): directions.append(1)
            with manager.lease('cloud', directions):
                return self._job(endpoint, parameters, cancel, progress, total, timeout)
        return self._job(endpoint, parameters, cancel, progress, total, timeout)

    def _job(self, endpoint, parameters, cancel=None, progress=None, total=0, timeout=300):
        cancel = cancel or Event()
        from .core import Cancelled
        if cancel.is_set():
            raise Cancelled('Cancelled')
        group = 'fluxftp-' + uuid.uuid4().hex
        launch = self.call(endpoint, {**parameters, '_async': True, '_group': group})
        job = launch['jobid']
        process = self.process
        started = time.monotonic()
        try:
            while True:
                if self.process is not process or process.poll() is not None:
                    raise OSError('The cloud backend stopped during this operation. Retry the transfer.')
                if cancel.is_set():
                    raise Cancelled('Cancelled')
                status = self._request('job/status', {'jobid': job})
                if launch.get('executeId') and status.get('executeId') != launch['executeId']:
                    raise OSError('The cloud backend restarted during this operation. Retry the transfer.')
                if status.get('finished'):
                    if not status.get('success'):
                        raise OSError(operation_error(status.get('error', '')))
                    if progress:
                        progress(total, total)
                    return status.get('output', {})
                if progress:
                    stats = self._request('core/stats', {'group': group})
                    progress(min(int(stats.get('bytes', 0)), total), total)
                if timeout and time.monotonic() - started > timeout:
                    raise TimeoutError('Cloud operation timed out.')
                cancel.wait(.2)
        except BaseException:
            if self.process is not process or process.poll() is not None:
                raise
            try:
                if endpoint.startswith('config/'):
                    try:
                        self._request('config/oauthstop', {}, timeout=2)
                    except OSError:
                        pass
                self._request('job/stop', {'jobid': job})
                # Do not release a destination while the backend still writes to it.
                until = time.monotonic() + 20
                while time.monotonic() < until:
                    if self._request('job/status', {'jobid': job}).get('finished'):
                        break
                    time.sleep(.1)
                else:
                    self.close()
            except OSError:
                self.close()
            raise
        finally:
            try:
                self._request('core/stats-delete', {'group': group}, 2)
            except OSError:
                pass
