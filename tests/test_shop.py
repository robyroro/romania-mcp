import asyncio
import json
from datetime import date

import httpx
import pytest
from conftest import anaf_item, bilant_body, mock_client

from romania_mcp import shop

TODAY = date(2026, 10, 1)


@pytest.mark.parametrize("text, cuis", [
    ("Dante International SA, CUI: 14399840, Reg. Com. J2002000372404", [14399840]),
    ("C.U.I. RO 14399840", [14399840]),
    ("Cod fiscal: RO14399840", [14399840]),
    ("CIF:18547290 | cod unic de înregistrare 14399840", [18547290, 14399840]),
    ("CUI: 14399841", []),  # wrong control digit
    ("plateste in RO49BTRL01301202123456XX", []),  # an IBAN isn't a CUI
    ("circuit_breaker 14399840", []),
    ("VAT RO14399840", [14399840]),  # no label, but a valid RO VAT number
])
def test_find_cuis(text, cuis):
    assert shop.find_cuis(text) == cuis


def test_find_registration_numbers():
    text = "J40/372/2002 si J2002000372404 si J 12 / 3456 / 2019"
    assert shop.find_registration_numbers(text) == ["J40/372/2002", "J2002000372404", "J12/3456/2019"]


@pytest.mark.parametrize("host, domain", [
    ("www.emag.ro", "emag.ro"),
    ("shop.firma.com.ro", "firma.com.ro"),
    ("example.co.uk", "example.co.uk"),
])
def test_registrable_domain(host, domain):
    assert shop.registrable_domain(host) == domain


def test_page_text_and_legal_links():
    html = """<html><script>var CUI = 1;</script><body>
      <a href="/termeni-si-conditii">Termeni</a> <a href="https://other.ro/contact">x</a>
      <a href="/cos">Coș</a> <a href="/info"><span>Date firmă</span></a>
      <p>CUI:&nbsp;<b>14399840</b></p></body></html>"""
    assert "var CUI" not in shop.page_text(html)
    assert "CUI: 14399840" in shop.page_text(html)
    assert shop.legal_links(html, "https://shop.ro/") == ["https://shop.ro/termeni-si-conditii", "https://shop.ro/info"]


def base_report(**over):
    company = {
        "name": "FOO TRADE SRL", "registered_on": "2015-01-01", "warnings": [],
    }
    report = {
        "site": {"fetched": True, "https": True, "cuis_found": [14399840], "anpc_links": True,
                 "_text": "Foo Trade SRL CUI 14399840"},
        "cui": 14399840,
        "company": company,
        "financials": [{"year": 2025, "revenue": 1000000, "employees": 4, "equity": 5}],
        "court_cases": {"total": 0},
        "domain": {"first_archived": "2016-05-01", "registered": None},
    }
    report.update(over)
    return report


def test_assess_healthy_shop_is_low_risk():
    risk, red, warn, good = shop.assess(base_report(), TODAY)
    assert risk == "low"
    assert red == [] and warn == []
    assert any("exists since 2015-01-01" in g for g in good)
    assert any("4 employees" in g for g in good)


def test_assess_red_flags():
    report = base_report(
        company={"name": "FOO TRADE SRL", "registered_on": "2015-01-01",
                 "warnings": ["declared fiscally inactive since 2024-05-01: ..."]},
        court_cases={"insolvency_as_debtor": ["123/1285/2026"]},
    )
    risk, red, _, _ = shop.assess(report, TODAY)
    assert risk == "high"
    assert any("fiscally inactive" in r for r in red)
    assert any("123/1285/2026" in r for r in red)


def test_assess_missing_cui_and_unknown_company():
    report = base_report(site={"fetched": True, "https": True, "cuis_found": [], "anpc_links": False, "_text": ""})
    report.pop("cui"), report.pop("company"), report.pop("financials")
    risk, red, warn, _ = shop.assess(report, TODAY)
    assert risk == "high"
    assert any("no CUI" in r for r in red)
    assert any("ANPC" in w for w in warn)

    risk, red, _, _ = shop.assess(base_report(company=None), TODAY)
    assert any("ANAF has no company" in r for r in red)


def test_assess_copied_cui_and_new_site_warn():
    report = base_report(domain={"first_archived": None, "registered": None})
    report["site"]["_text"] = "Super Reduceri CUI 14399840"
    risk, _, warn, _ = shop.assess(report, TODAY)
    assert risk == "medium"
    assert any("never mentions 'FOO TRADE SRL'" in w for w in warn)
    assert any("never archived" in w for w in warn)


def test_check_end_to_end():
    def handler(request):
        url = str(request.url)
        if url.startswith("https://shop.ro"):
            return httpx.Response(200, text='<p>Foo Trade SRL, CUI RO14399840</p><a href="https://anpc.ro">ANPC</a>')
        if "wayback/available" in url:
            return httpx.Response(200, json={"archived_snapshots": {"closest": {"timestamp": "20160501000000"}}})
        if "PlatitorTvaRest" in url:
            cui = json.loads(request.content)[0]["cui"]
            return httpx.Response(200, json={"found": [anaf_item(cui=cui, name="FOO TRADE SRL")], "notFound": []})
        if "bilant" in url:
            return httpx.Response(200, json=bilant_body(year=int(request.url.params["an"])))
        if "portalquery" in url:
            return httpx.Response(200, text='<Envelope xmlns="portalquery.just.ro"><CautareDosareResponse><CautareDosareResult/></CautareDosareResponse></Envelope>')
        return httpx.Response(404)

    async def go():
        async with mock_client(handler) as client:
            return await shop.check(client, "shop.ro", today=TODAY)

    res = asyncio.run(go())
    assert res["cui"] == 14399840
    assert res["company"]["name"] == "FOO TRADE SRL"
    assert res["financials"][0]["year"] == 2025
    assert res["court_cases"]["total"] == 0
    assert res["domain"] == {"name": "shop.ro", "registered": None, "first_archived": "2016-05-01"}
    assert res["risk"] == "low", res
    assert "_text" not in res["site"]


def test_assess_anaf_down_is_a_warning_not_a_red_flag():
    report = base_report(company_error="timed out talking to webservicesp.anaf.ro, try again")
    report.pop("company"), report.pop("financials")
    risk, red, warn, _ = shop.assess(report, TODAY)
    assert red == []
    assert any("couldn't check the company at ANAF" in w for w in warn)
