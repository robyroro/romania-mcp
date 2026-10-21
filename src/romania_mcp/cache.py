"""Small sqlite cache so we don't ask ANAF, BNR & co the same thing twice.

ROMANIA_MCP_CACHE=off        disable it
ROMANIA_MCP_CACHE_PATH=...   where the db lives (default ~/.cache/romania-mcp/cache.sqlite3)

Each source picks its own TTL (company status a few hours, old exchange rates a month).
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_fresh = ContextVar("fresh", default=False)
_observations = ContextVar("cache_observations", default=None)


@contextmanager
def cache_scope(refresh=False):
    """Track the age of source data without sharing state between concurrent requests."""
    observations = []
    a = _fresh.set(refresh or _fresh.get())
    b = _observations.set(observations)
    try:
        yield observations
    finally:
        _observations.reset(b)
        _fresh.reset(a)


def _observe(key, stored_at, hit):
    observations = _observations.get()
    if observations is not None:
        observations.append({"key": key, "stored_at": stored_at, "cache_hit": hit})


class Cache:
    def __init__(self, path):
        self.db = None
        if path is None:
            return
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "create table if not exists entries (key text primary key, value text, expires real)"
        )
        if "stored_at" not in {r[1] for r in self.db.execute("pragma table_info(entries)")}:
            self.db.execute("alter table entries add column stored_at real")
            self.db.commit()

    def get(self, key):
        if self.db is None or _fresh.get():
            return None
        row = self.db.execute("select value, expires, stored_at from entries where key = ?", (key,)).fetchone()
        if row is None or row[1] < time.time():
            return None
        _observe(key, row[2], True)
        return json.loads(row[0])

    def set(self, key, value, ttl):
        stored_at = time.time()
        _observe(key, stored_at, False)
        if self.db is None:
            return
        self.db.execute(
            "insert or replace into entries (key, value, expires, stored_at) values (?, ?, ?, ?)",
            (key, json.dumps(value), stored_at + ttl, stored_at),
        )
        self.db.commit()


_cache = None


def get_cache():
    global _cache
    if _cache is None:
        if os.environ.get("ROMANIA_MCP_CACHE", "").lower() in ("off", "0", "false", "no"):
            _cache = Cache(None)
        else:
            path = os.environ.get("ROMANIA_MCP_CACHE_PATH") or str(
                Path.home() / ".cache" / "romania-mcp" / "cache.sqlite3"
            )
            _cache = Cache(path)
    return _cache


def set_cache(cache):
    global _cache
    _cache = cache


async def cached(key, ttl, fetch):
    """The cached value for key, or await fetch() and remember what it returns.
    None results aren't cached."""
    cache = get_cache()
    hit = cache.get(key)
    if hit is not None:
        return hit
    value = await fetch()
    if value is not None:
        cache.set(key, value, ttl)
    return value
