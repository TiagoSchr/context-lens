"""
LRU + TTL cache for expensive operations: FTS queries, file reads, context builds.

All caches are in-process (per MCP server instance or CLI invocation).
Cache keys are normalised to be stable across equivalent calls.
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Any


class _TTLEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl


class LRUCache:
    """Thread-unsafe LRU cache with per-entry TTL."""

    def __init__(self, maxsize: int = 256, default_ttl: float = 60.0):
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        self._store: OrderedDict[str, _TTLEntry] = OrderedDict()

    # ── public API ────────────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        """Return cached value or None if missing/expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        # LRU: move to end
        self._store.move_to_end(key)
        return entry.value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """Store a value with optional TTL override."""
        ttl = ttl if ttl is not None else self._default_ttl
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = _TTLEntry(value, ttl)
        # Evict oldest if over capacity
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


# ── Shared singletons (created lazily per process) ────────────────────────────

_fts_cache: LRUCache | None = None
_context_cache: LRUCache | None = None
_file_cache: LRUCache | None = None


def get_fts_cache() -> LRUCache:
    global _fts_cache
    if _fts_cache is None:
        _fts_cache = LRUCache(maxsize=512, default_ttl=120.0)
    return _fts_cache


def get_context_cache() -> LRUCache:
    global _context_cache
    if _context_cache is None:
        _context_cache = LRUCache(maxsize=128, default_ttl=60.0)
    return _context_cache


def get_file_cache() -> LRUCache:
    global _file_cache
    if _file_cache is None:
        _file_cache = LRUCache(maxsize=256, default_ttl=300.0)
    return _file_cache


# ── Key builders ──────────────────────────────────────────────────────────────

def fts_key(query: str, limit: int) -> str:
    return f"fts:{query.lower().strip()}:{limit}"


def context_key(query: str, task: str, budget: int) -> str:
    digest = hashlib.md5(query.encode(), usedforsecurity=False).hexdigest()[:8]
    return f"ctx:{task}:{budget}:{digest}"


def file_key(path: str, file_hash: str) -> str:
    return f"file:{path}:{file_hash[:8]}"


def invalidate_all() -> None:
    """Clear all caches — call after a reindex."""
    get_fts_cache().clear()
    get_context_cache().clear()
    get_file_cache().clear()
