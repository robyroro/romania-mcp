"""BNR (National Bank of Romania) reference exchange rates, from its public XML feeds."""

import xml.etree.ElementTree as ET
from datetime import date

from .cache import cached

TODAY_URL = "https://curs.bnr.ro/nbrfxrates.xml"
TEN_DAYS_URL = "https://curs.bnr.ro/nbrfxrates10days.xml"
YEAR_URL = "https://curs.bnr.ro/files/xml/years/nbrfxrates{year}.xml"
FIRST_YEAR = 2005  # the yearly files start with the RON redenomination


def parse_rates(xml_text):
    """{'2026-09-30': {'EUR': 5.2785, 'HUF': 0.014406, ...}, ...}, rates in RON for 1 unit."""
    root = ET.fromstring(xml_text)
    out = {}
    # the namespace changed from http to https at some point, so ignore it
    for cube in root.iter():
        if not cube.tag.endswith("Cube"):
            continue
        rates = {}
        for rate in cube:
            if not rate.tag.endswith("Rate") or not rate.text:
                continue
            multiplier = int(rate.get("multiplier") or 1)
            rates[rate.get("currency")] = round(float(rate.text) / multiplier, 8)
        out[cube.get("date")] = rates
    return out


async def _feed(client, url, ttl):
    async def fetch():
        r = await client.get(url, timeout=30.0)
        r.raise_for_status()
        return parse_rates(r.text)
    return await cached(f"bnr:{url}", ttl, fetch)


async def rates_on(client, day=None, today=None):
    """(rate_date, rates) for the last BNR fixing on or before day (default: latest).
    Weekends and holidays fall back to the previous fixing, same as invoices do."""
    today = today or date.today()
    if day is None:
        cubes = await _feed(client, TODAY_URL, 3600)
        latest = max(cubes)
        return latest, cubes[latest]
    if day > today:
        raise ValueError(f"{day} is in the future, BNR only publishes a rate on the day")
    if day.year < FIRST_YEAR:
        raise ValueError(f"rates are available from {FIRST_YEAR} on")

    feeds = []
    if (today - day).days < 9:
        feeds.append((TEN_DAYS_URL, 3600))
    # past years never change, the current one grows every day
    feeds.append((YEAR_URL.format(year=day.year), 3600 if day.year == today.year else 30 * 86400))

    key = day.isoformat()
    for url, ttl in feeds:
        cubes = await _feed(client, url, ttl)
        earlier = [d for d in cubes if d <= key]
        if earlier:
            when = max(earlier)
            return when, cubes[when]

    if day.year > FIRST_YEAR:
        # first days of January, the last fixing is in last year's file
        return await rates_on(client, date(day.year - 1, 12, 31), today)
    raise ValueError(f"no BNR rate on or before {key}")


def pick(rates, currency):
    code = currency.strip().upper()
    if code == "RON":
        return 1.0
    if code not in rates:
        raise ValueError(f"BNR doesn't publish a rate for {code!r}. available: {', '.join(sorted(rates))}")
    return rates[code]
