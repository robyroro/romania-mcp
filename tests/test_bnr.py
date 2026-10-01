import asyncio
from datetime import date

import httpx
import pytest

from romania_mcp import bnr

from conftest import mock_client


def feed(*cubes, ns="https://www.bnr.ro/xsd"):
    body = "".join(
        f'<Cube date="{d}"><Rate currency="EUR">{eur}</Rate><Rate currency="HUF" multiplier="100">1.4406</Rate></Cube>'
        for d, eur in cubes
    )
    return f'<?xml version="1.0" encoding="utf-8"?><DataSet xmlns="{ns}"><Body>{body}</Body></DataSet>'


def test_parse_rates_handles_multiplier_and_old_namespace():
    rates = bnr.parse_rates(feed(("2025-03-14", "4.9763"), ns="http://www.bnr.ro/xsd"))
    assert rates == {"2025-03-14": {"EUR": 4.9763, "HUF": 0.014406}}


def run(day, files, today=date(2026, 10, 1)):
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if url not in files:
            return httpx.Response(404)
        return httpx.Response(200, text=files[url])

    async def go():
        async with mock_client(handler) as client:
            return await bnr.rates_on(client, day, today=today)

    return asyncio.run(go()), requested


def test_weekend_falls_back_to_friday():
    year = bnr.YEAR_URL.format(year=2025)
    (when, rates), _ = run(date(2025, 3, 16), {year: feed(("2025-03-13", "4.97"), ("2025-03-14", "4.98"))})
    assert when == "2025-03-14"
    assert rates["EUR"] == 4.98


def test_recent_day_uses_ten_day_feed():
    (when, _), requested = run(date(2026, 9, 29), {bnr.TEN_DAYS_URL: feed(("2026-09-29", "5.27"))})
    assert when == "2026-09-29"
    assert requested == [bnr.TEN_DAYS_URL]


def test_new_year_falls_back_to_last_year():
    files = {
        bnr.YEAR_URL.format(year=2025): feed(("2025-01-03", "4.97")),
        bnr.YEAR_URL.format(year=2024): feed(("2024-12-31", "4.9741")),
    }
    (when, _), _ = run(date(2025, 1, 1), files)
    assert when == "2024-12-31"


def test_latest_and_bad_input():
    (when, _), _ = run(None, {bnr.TODAY_URL: feed(("2026-09-30", "5.2785"))})
    assert when == "2026-09-30"
    with pytest.raises(ValueError, match="future"):
        run(date(2027, 1, 1), {})
    with pytest.raises(ValueError, match="2005"):
        run(date(1999, 1, 1), {})


def test_pick():
    rates = {"EUR": 5.0}
    assert bnr.pick(rates, " eur ") == 5.0
    assert bnr.pick(rates, "RON") == 1.0
    with pytest.raises(ValueError, match="doesn't publish"):
        bnr.pick(rates, "XYZ")
