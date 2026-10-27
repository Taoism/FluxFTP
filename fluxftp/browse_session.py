"""One serialized browsing connection per pane, with idle FTP keepalive."""
import errno
import ftplib
import ssl
import threading
import time


def disconnected(error):
    # Paramiko converts an SFTP EOF to SSHException at its request boundary.
    from paramiko import SSHException
    if isinstance(error, SSHException) and str(error).startswith('Server connection dropped:'):
        return True
    if isinstance(error, OSError) and str(error) == 'Socket is closed':
        return True
    return isinstance(error, (EOFError, ConnectionError, TimeoutError, ssl.SSLEOFError, ssl.SSLZeroReturnError)) or (
        isinstance(error, OSError) and error.errno in
        (errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED, errno.ENOTCONN, errno.ETIMEDOUT)) or (
        isinstance(error, ftplib.error_temp) and str(error).startswith('421'))


class BrowseSession:
    def __init__(self):
        self.connection = None
        self.key = None
        self.last_activity = time.monotonic()
        self.noop_supported = True
        self._operation = threading.RLock()
        self._generation = 0

    @property
    def generation(self):
        return self._generation

    def _discard(self):
        connection, self.connection = self.connection, None
        self.key = None
        self.noop_supported = True
        if connection is not None:
            connection.close()

    def close(self):
        # UI invalidation never waits for a listing or a network probe. The
        # worker holding the operation lock disposes of its connection on exit.
        self._generation += 1
        if self._operation.acquire(blocking=False):
            try:
                self._discard()
            finally:
                self._operation.release()

    def due(self, interval, now=None):
        if not self._operation.acquire(blocking=False):
            return False
        try:
            return bool(self.connection and getattr(self.connection, 'ftp', None)
                        and self.noop_supported and
                        (time.monotonic() if now is None else now) - self.last_activity >= interval)
        finally:
            self._operation.release()

    def keepalive(self, interval):
        if not self._operation.acquire(blocking=False):
            return False
        generation = self._generation
        try:
            if not self.due(interval):
                return False
            connection = self.connection
            ftp = connection.ftp
            sock = ftp.sock
            timeout = sock.gettimeout()
            try:
                # Keep an idle probe from delaying navigation/shutdown for the
                # longer transfer reply timeout. Never share this socket with a transfer.
                sock.settimeout(min(timeout, 5) if timeout is not None else 5)
                connection.phase = 'idle browsing keepalive'
                ftp.voidcmd('NOOP')
                return True
            except ftplib.error_perm as error:
                if str(error)[:3] in ('500', '502', '504'):
                    self.noop_supported = False
                    connection.trace.event('Idle NOOP unsupported; reconnect on next navigation if needed')
                else:
                    connection.trace.error(connection.phase, error)
                    self._discard()
                return False
            except Exception as error:
                connection.trace.error(connection.phase, error)
                self._discard()
                return False
            finally:
                self.last_activity = time.monotonic()
                if self.connection is connection:
                    sock.settimeout(timeout)
        finally:
            if generation != self._generation:
                self._discard()
            self._operation.release()

    def list(self, key, factory, path, expected_generation=None):
        generation = self._generation if expected_generation is None else expected_generation
        with self._operation:
            if generation != self._generation:
                from .core import Cancelled
                raise Cancelled('Browsing request belongs to a disconnected session')
            if key != self.key:
                self._discard()
            reused = self.connection is not None
            try:
                for attempt in range(2):
                    try:
                        if self.connection is None:
                            self.connection = factory()
                            self.connection.connect()
                            self.key = key
                        result = self.connection.list(path), self.connection.security
                        if generation != self._generation:
                            from .core import Cancelled
                            raise Cancelled('Browsing session was disconnected')
                        self.last_activity = time.monotonic()
                        return result
                    except Exception as error:
                        if self.connection is not None:
                            self.connection.trace.error(self.connection.phase, error)
                        self._discard()
                        if not (reused and attempt == 0 and generation == self._generation and disconnected(error)):
                            raise
            finally:
                if generation != self._generation:
                    self._discard()
