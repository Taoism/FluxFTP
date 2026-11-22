"""Small TLS-first IRC receiver for allowlisted trading channels."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import socket
import ssl
import threading
import time

from PySide6.QtCore import QThread, Signal


MAX_IRC_LINE = 8192


def parse_irc_line(line):
    """Parse IRCv3 tags, prefix, command, middle params and trailing text."""
    if not isinstance(line, str) or len(line.encode('utf-8', 'replace')) > MAX_IRC_LINE:
        raise ValueError('IRC line exceeds the 8192-byte limit.')
    tags = {}
    if line.startswith('@'):
        tag_text, separator, line = line.partition(' ')
        if not separator:
            raise ValueError('Malformed IRC tag line.')
        for pair in tag_text[1:].split(';'):
            key, equals, value = pair.partition('=')
            if key:
                tags[key] = value if equals else ''
    prefix = ''
    if line.startswith(':'):
        prefix, separator, line = line[1:].partition(' ')
        if not separator:
            raise ValueError('Malformed IRC prefix.')
    middle, separator, trailing = line.partition(' :')
    params = middle.split()
    if separator:
        params.append(trailing)
    if not params:
        raise ValueError('Malformed IRC command.')
    return tags, prefix, params[0].upper(), params[1:]


class IRCNetworkWorker(QThread):
    event_received = Signal(object)
    status = Signal(str)

    def __init__(self, network, password='', parent=None):
        super().__init__(parent)
        self.network = dict(network)
        self.password = password
        self._stop_event = threading.Event()
        self._socket = None

    def stop(self):
        self._stop_event.set()
        try:
            if self._socket:
                self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def run(self):
        delay = 1.0
        while not self._stop_event.is_set():
            try:
                self._receive_session()
                delay = 1.0
            except (OSError, ssl.SSLError, ValueError) as error:
                if self._stop_event.is_set():
                    break
                self.status.emit(f'IRC {self.network["id"]} disconnected: {str(error)[:180]}')
            finally:
                current, self._socket = self._socket, None
                if current:
                    try:
                        current.close()
                    except OSError:
                        pass
            if not self._stop_event.is_set():
                self.status.emit(f'IRC {self.network["id"]} reconnecting in {int(delay)} seconds.')
                self._stop_event.wait(delay)
                delay = min(60.0, delay * 2)

    def _send(self, stream, text):
        if any(char in text for char in '\r\n\x00'):
            raise ValueError('Refusing unsafe IRC command text.')
        stream.sendall((text + '\r\n').encode('utf-8'))

    def _receive_session(self):
        spec = self.network
        raw = socket.create_connection((spec['host'], spec['port']), timeout=15)
        raw.settimeout(2)
        if spec['tls']:
            stream = ssl.create_default_context().wrap_socket(raw, server_hostname=spec['host'])
        else:
            if self.password:
                raw.close()
                raise ValueError('An IRC network password requires TLS.')
            stream = raw
        self._socket = stream
        stream.settimeout(2)
        if self.password:
            self._send(stream, 'PASS :' + self.password)
        self._send(stream, 'NICK ' + spec['nickname'])
        self._send(stream, 'USER ' + spec['username'] + ' 0 * :FluxFTP auto trader')
        buffer = bytearray()
        registered = False
        while not self._stop_event.is_set():
            try:
                block = stream.recv(4096)
            except socket.timeout:
                continue
            if not block:
                raise OSError('IRC server closed the connection.')
            buffer.extend(block)
            if len(buffer) > MAX_IRC_LINE * 2:
                raise ValueError('IRC server sent an overlong line.')
            while b'\n' in buffer:
                raw_line, _, tail = buffer.partition(b'\n')
                buffer[:] = tail
                raw_line = raw_line.rstrip(b'\r')
                if len(raw_line) > MAX_IRC_LINE:
                    raise ValueError('IRC server sent an overlong line.')
                try:
                    line = raw_line.decode('utf-8')
                except UnicodeDecodeError:
                    line = raw_line.decode('utf-8', 'replace')
                tags, prefix, command, params = parse_irc_line(line)
                if command == 'PING':
                    self._send(stream, 'PONG :' + (params[-1] if params else ''))
                elif command == '001' and not registered:
                    registered = True
                    for channel in spec['channels']:
                        self._send(stream, 'JOIN ' + channel)
                    self.status.emit(f'IRC {spec["id"]} connected to configured channels.')
                elif command == 'PRIVMSG' and registered and len(params) >= 2:
                    target, message = params[0], params[-1]
                    if (target.casefold() not in {item.casefold() for item in spec['channels']} or
                            message.startswith('\x01') or len(message) > 2048):
                        continue
                    sender = prefix.split('!', 1)[0]
                    if not sender:
                        continue
                    msgid = tags.get('msgid')
                    if msgid:
                        event_id = spec['id'] + ':msgid:' + msgid
                    else:
                        server_time = tags.get('time', '')
                        if not server_time:
                            server_time = datetime.now(timezone.utc).isoformat(timespec='seconds')
                        event_id = spec['id'] + ':' + server_time + ':' + hashlib.sha256(raw_line).hexdigest()
                    self.event_received.emit(dict(network=spec['id'], channel=target,
                        sender=sender, message=message, event_id=event_id, source_client='builtin'))
                elif command == 'ERROR':
                    raise OSError('IRC server rejected the connection.')
