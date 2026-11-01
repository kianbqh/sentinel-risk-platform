import hashlib
import time
from collections import OrderedDict
from threading import Lock


class TTLCache:
    """Small in-process LRU+TTL cache. Swap for Redis when running more than one replica."""

    def __init__(self, max_items: int = 10_000, ttl_s: float = 3600.0):
        self.max_items, self.ttl_s = max_items, ttl_s
        self._d: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = Lock()

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    def get(self, k: str):
        with self._lock:
            item = self._d.get(k)
            if item is None:
                return None
            ts, v = item
            if time.monotonic() - ts > self.ttl_s:
                del self._d[k]
                return None
            self._d.move_to_end(k)
            return v

    def put(self, k: str, v) -> None:
        with self._lock:
            self._d[k] = (time.monotonic(), v)
            self._d.move_to_end(k)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)
