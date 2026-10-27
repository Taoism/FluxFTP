"""Short-lived, memory-only browsing snapshots; transfers always list the server."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import posixpath
import time


class DirectoryCache:
    def __init__(self, ttl=30, capacity=128, max_entries=50000):
        self.ttl, self.capacity = ttl, capacity
        self.max_entries = max_entries
        self.records = OrderedDict()
        self.epoch = 0

    @staticmethod
    def site_key(site):
        return tuple(asdict(site).items())

    def key(self, site, password, path):
        return self.site_key(site), sha256(password.encode('utf-8')).digest(), posixpath.normpath(path)

    def get(self, key):
        record = self.records.get(key)
        if record is None:
            return None
        timestamp, result = record
        if time.monotonic() - timestamp >= self.ttl:
            del self.records[key]
            return None
        self.records.move_to_end(key)
        return deepcopy(result)

    def put(self, key, result, epoch):
        if epoch != self.epoch:
            return
        if len(result[0]) > self.max_entries:
            self.records.pop(key, None)
            return
        self.records[key] = (time.monotonic(), deepcopy(result))
        self.records.move_to_end(key)
        while len(self.records) > self.capacity or sum(len(record[1][0]) for record in self.records.values()) > self.max_entries:
            self.records.popitem(last=False)

    def clear(self, site=None):
        self.epoch += 1
        keys = list(self.records) if site is None else [key for key in self.records if key[0] == self.site_key(site)]
        for key in keys:
            del self.records[key]
        return len(keys)
