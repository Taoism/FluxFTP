"""HexChat add-on that forwards channel PRIVMSG events to FluxFTP."""
import hashlib
import json
import queue
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import hexchat


TOKEN = '__BRIDGE_TOKEN__'
PORT = __BRIDGE_PORT__
ENDPOINT = f'http://127.0.0.1:{PORT}/v1/trading/irc-event'
PENDING = queue.Queue(maxsize=64)
_last_notice = 0.0


def _sender(prefix):
    return prefix.split('!', 1)[0].lstrip(':')


def _post_events():
    global _last_notice
    while True:
        event = PENDING.get()
        try:
            body = urlencode(event).encode('utf-8')
            request = Request(ENDPOINT, data=body, method='POST', headers={
                'Authorization': 'Bearer ' + TOKEN,
                'Content-Type': 'application/x-www-form-urlencoded',
            })
            with urlopen(request, timeout=2) as response:
                response.read(1024)
        except Exception:
            now = time.monotonic()
            if now - _last_notice >= 60:
                hexchat.prnt('FluxFTP trading bridge is unavailable; IRC events are being ignored.')
                _last_notice = now
        finally:
            PENDING.task_done()


threading.Thread(target=_post_events, name='FluxFTP-trading-bridge', daemon=True).start()


def _on_privmsg(word, word_eol, _userdata, attrs):
    try:
        prefix = str(word[0])
        command = str(word[1]).upper()
        target = str(word[2])
        message = str(word_eol[3]).lstrip(':')
        if command != 'PRIVMSG' or not target.startswith(('#', '&', '+', '!')) or len(message) > 2048:
            return hexchat.EAT_NONE
        if message.startswith('\x01'):
            return hexchat.EAT_NONE
        network = hexchat.get_info('network') or ''
        sender = _sender(prefix)
        stamp = getattr(attrs, 'time', 0) or ''
        identity = f'{network}:{target}:{sender}:{stamp}:{time.time_ns()}:{hashlib.sha256(message.encode("utf-8")).hexdigest()}'
        PENDING.put_nowait({
            'network': network,
            'channel': target,
            'sender': sender,
            'message': message,
            'event_id': identity,
            'source_client': 'hexchat',
        })
    except queue.Full:
        hexchat.prnt('FluxFTP trading bridge queue is full; this IRC event was dropped.')
    except Exception:
        pass
    return hexchat.EAT_NONE


hexchat.hook_server_attrs('PRIVMSG', _on_privmsg)
hexchat.prnt('FluxFTP trading bridge loaded. Transfers remain controlled by FluxFTP rules.')
