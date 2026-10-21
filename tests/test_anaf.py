import asyncio
import json

import httpx
import pytest
from conftest import anaf_item, bilant_body, mock_client

from romania_mcp import anaf


@pytest.mark.parametrize("raw", ["14399840", "RO14399840", " ro 14399840 ", 14399840, "18547290"])
def test_clean_cui_accepts_valid(raw):
    assert anaf.clean_cui(raw) in (14399840, 18547290)


@pytest.mark.parametrize("raw", ["14399841", "abc", "", "RO", "12345678901"])
def test_clean_cui_rejects_bad(raw):
    with pytest.raises(ValueError):
        anaf.clean_cui(raw)


def test_parse_company_active():
    c = anaf.parse_company(anaf_item())
    assert c["name"] == "DANTE INTERNATIONAL SA"
    assert c["vat_registered"] is True
    assert c["vat_periods"] == [{"from": "2002-02-01", "to": None, "note": None}]
    assert c["deregistered"] is False
    assert c["fiscally_inactive"] is False
    assert c["warnings"] == []


def test_parse_company_inactive_and_deregistered_warn():
    c = anaf.parse_company(anaf_item(status="RADIERE din data 20.07.2016", inactive=True))
    assert c["deregistered"] is True
    assert any("deregistered" in w for w in c["warnings"])
    assert any("fiscally inactive since 2024-05-01" in w for w in c["warnings"])


def test_lookup_batches_by_100_and_collects_not_found():
    bodies = []

    def handler(request):
        batch = json.loads(request.content)
        bodies.append(batch)
        found = [anaf_item(cui=b["cui"]) for b in batch if b["cui"] != 1]
        return httpx.Response(200, json={"found": found, "notFound": [b["cui"] for b in batch if b["cui"] == 1]})

    async def go():
        async with mock_client(handler) as client:
            return await anaf.lookup(client, [1, *range(1000, 1150)], on="2026-10-01")

    found, not_found = asyncio.run(go())
    assert [len(b) for b in bodies] == [100, 51]
    assert bodies[0][0] == {"cui": 1, "data": "2026-10-01"}
    assert len(found) == 150
    assert not_found == [1]


def test_parse_bilant_maps_labels():
    year = anaf.parse_bilant(bilant_body())
    assert year["revenue"] == 8992961799
    assert year["employees"] == 3191
    assert year["debts"] == 2316131718
    assert year["equity"] == 2390301600
    assert year["fixed_assets"] == 2770685187
    assert year["cash"] == 291756487
    assert year["net_result"] == -191463916
    assert "other" not in year


def test_parse_bilant_empty_and_bank_layout():
    assert anaf.parse_bilant({"an": 2012, "i": []}) is None
    bank = anaf.parse_bilant({"an": 2024, "i": [
        {"indicator": "I23", "val_indicator": 5, "val_den_indicator": "Cifra de afaceri neta"},
        {"indicator": "I8", "val_indicator": 7, "val_den_indicator": "Imobilizări corporale"},
    ]})
    assert bank["revenue"] == 5
    assert bank["other"] == {"Imobilizări corporale": 7}


def test_financials_marks_missing_years():
    def handler(request):
        year = int(request.url.params["an"])
        if year == 2025:
            return httpx.Response(200, json={"an": 2025, "cui": 14399840, "deni": "", "i": []})
        return httpx.Response(200, json=bilant_body(year=year))

    async def go():
        async with mock_client(handler) as client:
            return await anaf.financials(client, 14399840, [2025, 2024])

    res = asyncio.run(go())
    assert res["name"] == "Dante International SA"
    assert res["currency"] == "RON"
    assert res["years"][0] == {"year": 2025, "filed": False}
    assert res["years"][1]["revenue"] == 8992961799
