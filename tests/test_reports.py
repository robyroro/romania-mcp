import asyncio
import copy
import json
from datetime import date

import httpx
import pytest
from conftest import anaf_item, bilant_body, mock_client

from romania_mcp import cache, reports, shop


def handler(request):
    if "PlatitorTvaRest" in str(request.url):
        cuis = [r["cui"] for r in json.loads(request.content)]
        return httpx.Response(200, json={"found": [anaf_item(cui=c) for c in cuis], "notFound": []})
    if "bilant" in str(request.url):
        return httpx.Response(200, json=bilant_body(year=int(request.url.params["an"]), cui=int(request.url.params["cui"])))
    if "portalquery" in str(request.url):
        return httpx.Response(200, text='<Envelope xmlns="portalquery.just.ro"><CautareDosareResponse><CautareDosareResult/></CautareDosareResponse></Envelope>')
    return httpx.Response(200, text='<p>DANTE INTERNATIONAL SA CUI 14399840</p><a href="https://anpc.ro">ANPC</a>')


def run_report(source=handler, years=2, refresh=False):
    async def go():
        async with mock_client(source) as client:
            return await reports.company_report(client, "14399840", years, refresh)
    return asyncio.run(go())


def test_report_has_source_receipts_and_financial_trends():
    result = run_report()
    assert result["coverage"] == "complete"
    assert result["company"]["cui"] == 14399840
    assert len(result["financial_analysis"]) == 2
    assert result["financial_analysis"][0]["revenue_change_percent"] == 0
    assert result["court_cases"]["matching"]["certainty"] == "candidate"
    assert all(s["checked_at"] and s["url"] for s in result["sources"].values())
    assert all("data" not in s for s in result["sources"].values())


@pytest.mark.parametrize("failed", ["PlatitorTvaRest", "bilant", "portalquery"])
def test_failed_source_never_becomes_a_clean_report(failed):
    def fail(request):
        return httpx.Response(503) if failed in str(request.url) else handler(request)
    result = run_report(fail)
    assert result["coverage"] == "partial"
    assert result["assessment"] == "incomplete"
    assert result["missing_sources"]
    if failed == "PlatitorTvaRest":
        assert result["company"] is None
        assert not result["findings"]  # unavailable is not not-found


def test_one_failed_year_does_not_discard_other_financials():
    def partial(request):
        if "bilant" in str(request.url) and request.url.params["an"] == str(date.today().year - 1):
            return httpx.Response(502)
        return handler(request)
    result = run_report(partial)
    assert result["financials"][0]["error"]
    assert result["financials"][1]["revenue"] > 0
    assert result["financial_analysis"][0]["revenue_change_percent"] is None


def test_missing_filing_is_different_from_failed_request():
    def empty(request):
        if "bilant" in str(request.url):
            return httpx.Response(200, json={"an": int(request.url.params["an"]), "cui": 14399840, "i": []})
        return handler(request)
    result = run_report(empty)
    assert result["coverage"] == "partial"
    assert result["assessment"] == "incomplete"
    financial_source = result["sources"][f"financials_{date.today().year - 1}"]
    assert financial_source["status"] == "no_data" and financial_source["error"] is None
    assert result["financials"][0]["filed"] is False
    assert result["financial_analysis"] == []


@pytest.mark.parametrize("broken", [{}, {"error": "service offline"}, {"found": [], "notFound": []}])
def test_malformed_anaf_response_is_unavailable(broken):
    def malformed(request):
        return httpx.Response(200, json=broken) if "PlatitorTvaRest" in str(request.url) else handler(request)
    result = run_report(malformed)
    assert result["sources"]["company"]["status"] == "unavailable"
    assert result["findings"] == []


def test_explicit_not_found_is_a_reported_finding():
    def missing(request):
        if "PlatitorTvaRest" in str(request.url):
            return httpx.Response(200, json={"found": [], "notFound": [14399840]})
        return handler(request)
    result = run_report(missing)
    assert result["sources"]["company"]["status"] == "available"
    assert result["findings"][0]["code"] == "not_found"


def test_metrics_handle_zero_revenue_and_year_gaps():
    result = reports.metrics([{"year": 2025, "revenue": 0, "net_result": -10, "employees": 0},
                              {"year": 2023, "revenue": 100, "net_result": 20, "employees": 2}])
    assert result[0]["profit_margin_percent"] is None
    assert result[0]["revenue_change_percent"] is None
    assert result[0]["employees_change"] is None
    assert result[1]["profit_margin_percent"] == 20


def test_failed_source_is_not_a_deleted_company_or_closed_court_case():
    before = run_report()
    after = copy.deepcopy(before)
    after["sources"]["company"]["status"] = "unavailable"
    after["sources"]["courts"]["status"] = "unavailable"
    after["company"] = after["court_cases"] = None
    after["coverage"] = "partial"
    delta = reports.changes(before, after)
    assert {c["field"] for c in delta["changes"]} == {"sources.company.status", "sources.courts.status"}
    assert set(delta["not_comparable"]) == {"company", "courts"}


def test_change_ignores_timestamps_but_detects_company_status():
    before = run_report()
    after = copy.deepcopy(before)
    after["checked_at"] = "later"
    after["sources"]["company"]["checked_at"] = "later"
    assert reports.changes(before, after)["changed"] is False
    after["company"]["fiscally_inactive"] = True
    assert reports.changes(before, after)["changes"][0]["field"] == "company.fiscally_inactive"


def test_refresh_bypasses_cache_and_receipts_keep_original_fetch_date(tmp_path):
    cache.set_cache(cache.Cache(str(tmp_path / "cache.sqlite3")))
    first = run_report()
    second = run_report()
    assert second["sources"]["company"]["cache_hit"] is True
    assert first["sources"]["company"]["oldest_data_fetched_at"] == second["sources"]["company"]["oldest_data_fetched_at"]
    third = run_report(refresh=True)
    assert third["sources"]["company"]["cache_hit"] is False


@pytest.mark.parametrize("failed", ["PlatitorTvaRest", "bilant", "portalquery"])
def test_shop_survives_upstream_failure_and_keeps_it_visible(failed):
    def fail(request):
        url = str(request.url)
        if failed in url:
            return httpx.Response(503)
        if "wayback" in url:
            return httpx.Response(200, json={"archived_snapshots": {"closest": {"timestamp": "20100101000000"}}})
        return handler(request)

    async def go():
        async with mock_client(fail) as client:
            return await shop.check(client, "https://shop.ro")
    result = asyncio.run(go())
    assert result["risk"] == "unknown"
    assert result["coverage"] == "partial"
    assert result["warnings"]


def test_old_cache_schema_upgrades_without_destroying_entries(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("create table entries(key text primary key,value text,expires real)")
        db.execute("insert into entries values ('a','{}',99999999999)")
    old = cache.Cache(str(path))
    assert old.get("a") == {}
    old.set("b", {"n": 1}, 100)
    assert old.get("b") == {"n": 1}


def test_unexpected_court_xml_is_unavailable_not_zero_cases():
    def malformed(request):
        if "portalquery" in str(request.url):
            return httpx.Response(200, text="<error>Temporarily unavailable</error>")
        return handler(request)
    result = run_report(malformed)
    assert result["coverage"] == "partial"
    assert result["sources"]["courts"]["status"] == "unavailable"
    assert result["court_cases"] is None


def test_failed_legal_page_does_not_become_a_missing_cui_red_flag():
    def source(request):
        url = str(request.url)
        if url == "https://shop.ro":
            return httpx.Response(200, text='<a href="/termeni">Termeni</a>')
        if url == "https://shop.ro/termeni":
            return httpx.Response(503)
        if "wayback" in url:
            return httpx.Response(200, json={"archived_snapshots": {}})
        return handler(request)
    async def go():
        async with mock_client(source) as client:
            return await shop.check(client, "https://shop.ro")
    result = asyncio.run(go())
    assert result["coverage"] == "partial"
    assert result["risk"] == "unknown" and result["red_flags"] == []
