"""Incremental queue JSON decoding; never parse an entire large array at once."""
import json
from pathlib import Path
from threading import Event
from .core import Cancelled

LIMIT = 256 * 1024 * 1024


class Reader:
    def __init__(self, stream, cancel):
        self.stream, self.cancel = stream, cancel
        self.text, self.index, self.eof = '', 0, False
        self.decoder = json.JSONDecoder()

    def fill(self):
        if self.cancel.is_set(): raise Cancelled('Queue operation cancelled')
        chunk = self.stream.read(65536)
        if self.stream.tell() > LIMIT: raise ValueError('Queue file is too large.')
        self.text = self.text[self.index:] + chunk
        self.index = 0
        self.eof = not chunk

    def peek(self):
        while True:
            if self.cancel.is_set(): raise Cancelled('Queue operation cancelled')
            while self.index < len(self.text) and self.text[self.index] in ' \r\n\t': self.index += 1
            if self.index < len(self.text): return self.text[self.index]
            if self.eof: return ''
            self.fill()

    def expect(self, character):
        if self.peek() != character: raise ValueError('Invalid queue JSON.')
        self.index += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.text, self.index)
                if end == len(self.text) and not self.eof:
                    self.fill()
                    continue
                self.index = end
                return value
            except json.JSONDecodeError:
                if self.eof: raise ValueError('Invalid queue JSON.') from None
                self.fill()


def queue_items(path, cancel=None):
    path = Path(path)
    if path.stat().st_size > LIMIT: raise ValueError('Queue file is too large.')
    with path.open('r', encoding='utf-8') as stream:
        reader = Reader(stream, cancel or Event())
        reader.expect('{')
        seen, version = set(), None
        while reader.peek() != '}':
            key = reader.value()
            if not isinstance(key, str) or key in seen: raise ValueError('Invalid queue object.')
            seen.add(key)
            reader.expect(':')
            if key == 'jobs':
                reader.expect('[')
                if reader.peek() != ']':
                    while True:
                        yield reader.value()
                        if reader.peek() == ']': break
                        reader.expect(',')
                        if reader.peek() == ']': raise ValueError('Invalid trailing comma.')
                reader.expect(']')
            else:
                value = reader.value()
                if key == 'version': version = value
            if reader.peek() == '}': break
            reader.expect(',')
            if reader.peek() == '}': raise ValueError('Invalid trailing comma.')
        reader.expect('}')
        if reader.peek() or 'jobs' not in seen or type(version) is not int or version != 1:
            raise ValueError('Invalid queue file.')
