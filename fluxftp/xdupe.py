"""Mode-3 XDUPE hints, scoped to exact filenames in one destination directory."""
import ftplib
import posixpath
import re
from functools import wraps
from inspect import signature


def names(response):
    from .core import safe_name
    result = set()
    if not response.startswith('553'):
        return result
    for line in response.splitlines():
        match = re.match(r'^553[- ]\s*X-DUPE: (.+)$', line, re.I)
        if match:
            try:
                result.add(safe_name(match.group(1)))
            except ValueError:
                pass
    return result


def configure(connection):
    from .site_options import decode
    connection.xdupes = set()
    if not decode(connection.site.site_options).get('Transfer/xdupe'):
        return
    try:
        connection.ftp.sendcmd('SITE XDUPE 3')
    except ftplib.error_perm as error:
        if str(error).startswith(('500', '501', '502', '504')):
            connection.trace.event('Server does not support XDUPE mode 3; normal file-exists handling remains active.')
            return
        raise
    def received(response, command):
        if command.upper().startswith('STOR '):
            parent = posixpath.dirname(command[5:])
            # Application transfers use absolute paths; do not guess a server CWD.
            if parent.startswith('/'):
                connection.xdupes.update(posixpath.join(parent, name) for name in names(response))
    connection.ftp.xdupe_received = received


def guard(function):
    spec = signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = spec.bind(*args, **kwargs)
        bound.apply_defaults()
        values = bound.arguments
        destination = values['destination']
        target = values['dst']
        def duplicate():
            return not values['is_dir'] and destination and target in getattr(destination, 'xdupes', set())
        def skipped():
            callback = values['skipped']
            if callback:
                callback(values['src'], 'XDUPE: server reports an existing destination')
            return False
        if duplicate():
            return skipped()
        try:
            return function(*args, **kwargs)
        except ftplib.error_perm:
            if not duplicate():
                raise
            # FXP cancels both sockets on failure; restore them before the folder walk continues.
            for connection in (values['source'], destination):
                if connection and getattr(connection, 'ftp', None) is not None and getattr(connection.ftp, 'sock', None) is None:
                    connection.connect()
            return skipped()
    return wrapped
