"""Source receipts for reports. checked_at is not a claim that cached data was fetched now."""

from datetime import datetime, timezone

import httpx

from .cache import cache_scope
from .common import describe_error


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


async def collect(name, url, fetch, refresh=False):
    checked_at = now()
    with cache_scope(refresh) as observations:
        try:
            data = await fetch()
            status, error = "available", None
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            data, status, error = None, "unavailable", describe_error(exc)
    times = [o["stored_at"] for o in observations if o["stored_at"] is not None]
    known_age = bool(times) and all(o["stored_at"] is not None for o in observations)
    return {
        "source": name, "url": url, "status": status, "checked_at": checked_at,
        "oldest_data_fetched_at": datetime.fromtimestamp(min(times), timezone.utc).isoformat() if known_age else None,
        "cache_hit": any(o["cache_hit"] for o in observations),
        "data": data, "error": error,
    }
