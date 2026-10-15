"""Bounded single-node login protection. Identifiers are HMACs, not raw IPs."""
from collections import deque
import hashlib
import hmac
from threading import Lock
import time


class LoginLimiter:
    def __init__(self, secret, limit=20, window=60, capacity=4096):
        self.secret = secret.encode()
        self.limit = limit
        self.window = window
        self.capacity = capacity
        self.entries = {}
        self.lock = Lock()

    def allow(self, identifier, at=None):
        now = time.monotonic() if at is None else at
        key = hmac.new(self.secret, identifier.encode(), hashlib.sha256).hexdigest()
        with self.lock:
            for expired in [k for k, values in self.entries.items() if not values or values[-1] <= now-self.window]:
                del self.entries[expired]
            if key not in self.entries:
                if len(self.entries) >= self.capacity:
                    return False
                self.entries[key] = deque()
            values = self.entries[key]
            while values and values[0] <= now-self.window:
                values.popleft()
            if len(values) >= self.limit:
                return False
            values.append(now)
            return True
