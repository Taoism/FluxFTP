"""A short rolling throughput window that survives gaps between small files."""
import time
from collections import deque


class RollingSpeed:
    def __init__(self):
        self.samples = deque(maxlen=64)
        self.previous = 0

    def sample(self, total, now=None, fallback=0):
        now = time.monotonic() if now is None else now
        if total < self.previous: self.samples.clear()
        self.previous = total
        while len(self.samples) > 1 and self.samples[1][0] <= now - 5:
            self.samples.popleft()
        if not self.samples:
            self.samples.append((now, total))
            return fallback
        started, baseline = self.samples[0]
        if now - self.samples[-1][0] >= .1:
            self.samples.append((now, total))
        elapsed = now - started
        return max(0, total - baseline) / elapsed if elapsed >= .25 else fallback
