"""Small sqlite cache so we don't ask ANAF, BNR & co the same thing twice.

ROMANIA_MCP_CACHE=off        disable it
ROMANIA_MCP_CACHE_PATH=...   where the db lives (default ~/.cache/romania-mcp/cache.sqlite3)

Each source picks its own TTL (company status a few hours, old exchange rates a month).
"""

import json
import os
import sqlite3
import time
from pathlib import Path


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

    def get(self, key):
        if self.db is None:
            return None
        row = self.db.execute("select value, expires from entries where key = ?", (key,)).fetchone()
        if row is None or row[1] < time.time():
            return None
        return json.loads(row[0])

    def set(self, key, value, ttl):
        if self.db is None:
            return
        self.db.execute(
            "insert or replace into entries values (?, ?, ?)",
            (key, json.dumps(value), time.time() + ttl),
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
