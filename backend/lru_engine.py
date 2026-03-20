"""
lru_engine.py  ·  Role 2: Core Engine Dev
==========================================
LRU Cache built on collections.OrderedDict.

OrderedDict gives us O(1) promotions via move_to_end() and O(1) LRU
eviction via popitem(last=False), without maintaining a manual
doubly-linked list.

Thread safety: every public method holds asyncio.Lock for the duration
of its mutation, so concurrent FastAPI coroutines never corrupt state.

Capacity: configurable (default 5). Set via LRU_CAPACITY env var or
constructor argument.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import OrderedDict
from typing import Any


class LRUEngine:
    """
    Async-safe LRU key-value store.

    OrderedDict layout
    ------------------
    last=True  → MRU end  (right / newest)
    last=False → LRU end  (left  / oldest)

    move_to_end(key, last=True)   → promote to MRU after a GET or SET
    popitem(last=False)           → evict LRU when capacity exceeded
    """

    def __init__(self, capacity: int = 5) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")

        self.capacity: int = capacity
        self._store: OrderedDict[str, Any] = OrderedDict()
        self._lock = asyncio.Lock()

        # ── Runtime metrics ──────────────────────────────────────
        self.total_sets      = 0
        self.total_gets      = 0
        self.total_hits      = 0
        self.total_misses    = 0
        self.total_evictions = 0
        self.start_time      = time.time()

        # Ring buffer of the last 20 evictions (for /stats + UI)
        self._eviction_log: list[dict] = []

    # ── Core operations ──────────────────────────────────────────────

    async def get(self, key: str) -> Any | None:
        """
        Return the value for *key* and promote it to MRU.
        Returns None on a cache miss.
        """
        async with self._lock:
            self.total_gets += 1
            if key not in self._store:
                self.total_misses += 1
                return None
            self.total_hits += 1
            self._store.move_to_end(key, last=True)   # → MRU
            return self._store[key]

    async def set(self, key: str, value: Any) -> str | None:
        """
        Insert or update *key* → *value*.

        Returns the evicted key if the cache was full, else None.
        The caller (main.py) uses the return value to write a DEL
        record to the AOF for the evicted entry.
        """
        async with self._lock:
            self.total_sets += 1
            evicted: str | None = None

            if key in self._store:
                # Update in-place and promote to MRU
                self._store[key] = value
                self._store.move_to_end(key, last=True)
            else:
                # Evict LRU if at capacity
                if len(self._store) >= self.capacity:
                    evicted_key, _ = self._store.popitem(last=False)
                    evicted = evicted_key
                    self.total_evictions += 1
                    self._eviction_log.append({
                        "key":        evicted_key,
                        "evicted_at": round(time.time(), 3),
                    })
                    if len(self._eviction_log) > 20:
                        self._eviction_log.pop(0)

                self._store[key] = value
                self._store.move_to_end(key, last=True)

            return evicted

    async def delete(self, key: str) -> bool:
        """
        Explicitly remove *key*.
        Returns True if the key existed, False otherwise.
        """
        async with self._lock:
            if key not in self._store:
                return False
            del self._store[key]
            return True

    async def clear(self) -> list[str]:
        """Remove all keys. Returns the list of cleared keys."""
        async with self._lock:
            keys = list(self._store.keys())
            self._store.clear()
            return keys

    # ── Read helpers (no state change) ──────────────────────────────

    async def keys_ordered(self) -> list[str]:
        """Return keys MRU → LRU (most-recent first)."""
        async with self._lock:
            return list(reversed(self._store.keys()))

    async def all_items(self) -> list[dict]:
        """Return all entries as dicts, MRU first, with rank index."""
        async with self._lock:
            items = list(reversed(self._store.items()))
            return [
                {"key": k, "value": v, "rank": i + 1}
                for i, (k, v) in enumerate(items)
            ]

    # ── Metrics ──────────────────────────────────────────────────────

    def memory_bytes(self) -> int:
        """
        Estimate the memory footprint of the store.
        Uses sys.getsizeof on the dict + each key/value pair.
        Sufficient for the dashboard; not a precise allocator measure.
        """
        total = sys.getsizeof(self._store)
        for k, v in self._store.items():
            total += sys.getsizeof(k) + sys.getsizeof(str(v))
        return total

    def snapshot(self) -> dict:
        """
        Return a stats snapshot for the /stats endpoint.
        Called from outside the lock; safe because Python int reads are
        atomic and we only care about approximate values here.
        """
        size = len(self._store)
        hit_rate = (
            round(self.total_hits / self.total_gets * 100, 1)
            if self.total_gets
            else 0.0
        )
        return {
            "capacity":         self.capacity,
            "current_size":     size,
            "fill_pct":         round(size / self.capacity * 100, 1),
            "memory_bytes":     self.memory_bytes(),
            "total_sets":       self.total_sets,
            "total_gets":       self.total_gets,
            "total_hits":       self.total_hits,
            "total_misses":     self.total_misses,
            "total_evictions":  self.total_evictions,
            "hit_rate_pct":     hit_rate,
            "uptime_seconds":   round(time.time() - self.start_time, 1),
            "recent_evictions": list(reversed(self._eviction_log[-5:])),
        }