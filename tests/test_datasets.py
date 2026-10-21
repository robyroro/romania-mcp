import asyncio
import csv
import zipfile

import httpx
import pytest
from conftest import mock_client
from openpyxl import Workbook

from romania_mcp import datasets
from romania_mcp.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data.sqlite3")


def write_csv(tmp_path, name, rows, delimiter="^"):
    path = tmp_path / name
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        writer.writerows(rows)
    return path


def test_real_onrc_headers_diacritics_filters_and_caen_join(tmp_path, store):
    path = write_csv(tmp_path, "onrc.csv", [
        ["DENUMIRE", "CUI", "COD_INMATRICULARE", "ADR_JUDET", "ADR_LOCALITATE"],
        ["Ștefan Comerț SRL", "14399840", "J40/1/2002", "București", "București"],
        ["Invalid", "0", "", "", ""],
    ])
    imported = datasets.import_file(store, path, "onrc", "https://data.gov.ro/onrc.csv", "2026-10-19")
    assert imported["rows"] == 1 and imported["skipped"] == 1
    result = datasets.search(store, "stefan comert", county="BUCURESTI")
    assert result["total"] == 1
    assert result["companies"][0]["published_on"] == "2026-10-19"
    caen = write_csv(tmp_path, "caen.csv", [["COD_INMATRICULARE", "COD_CAEN_AUTORIZAT", "VER_CAEN_AUTORIZAT"],
                                          ["J40/1/2002", "6201", "2"]])
    datasets.import_file(store, caen, "caen", "https://data.gov.ro/caen.csv", "2026-10-19")
    assert datasets.search(store, "stefan", caen="6201")["total"] == 1
    assert datasets.search(store, "stefan", caen="6201", caen_version="2")["total"] == 1
    assert datasets.search(store, "stefan", caen="6201", caen_version="3")["total"] == 0
    assert datasets.search(store, "stefan", caen="9999")["total"] == 0


def test_caen_filters_do_not_mix_snapshots_or_return_omitted_old_companies(tmp_path, store):
    old = write_csv(tmp_path, "old.csv", [["name", "cui", "registration_number"],
                                         ["Omitted SRL", "18547290", "J1/1/2000"],
                                         ["Current SRL", "14399840", "J1/2/2000"]])
    datasets.import_file(store, old, "onrc", "https://data.gov.ro/old.csv", "2026-09-01")
    current = write_csv(tmp_path, "new.csv", [["name", "cui", "registration_number"],
                                             ["Current SRL", "14399840", "J1/2/2000"]])
    datasets.import_file(store, current, "onrc", "https://data.gov.ro/new.csv", "2026-10-19")
    caen = write_csv(tmp_path, "caen.csv", [["COD_INMATRICULARE", "COD_CAEN_AUTORIZAT", "VER_CAEN_AUTORIZAT"],
                                          ["J1/2/2000", "6201", "2"]])
    datasets.import_file(store, caen, "caen", "https://data.gov.ro/caen.csv", "2026-09-01")
    assert datasets.search(store, "omitted")["total"] == 0
    assert datasets.search(store, "current", caen="6201")["total"] == 0


def test_failed_reimport_rolls_back_and_keeps_previous_rows(tmp_path, store):
    path = write_csv(tmp_path, "onrc.csv", [["name", "cui"], ["Good SRL", "14399840"]])
    datasets.import_file(store, path, "onrc", "https://data.gov.ro/onrc.csv", "2026-10-19")
    broken = write_csv(tmp_path, "bad.csv", [["wrong", "header"], ["bad", "data"]])
    with pytest.raises(ValueError, match="previous data was kept"):
        datasets.import_file(store, broken, "onrc", "https://data.gov.ro/onrc.csv", "2026-10-20")
    assert datasets.search(store, "good")["total"] == 1
    assert datasets.manifests(store)[0]["published_on"] == "2026-10-19"


def test_latest_record_wins_and_reimport_is_idempotent(tmp_path, store):
    path = write_csv(tmp_path, "onrc.csv", [["name", "cui"], ["Old SRL", "14399840"]])
    datasets.import_file(store, path, "onrc", "https://data.gov.ro/old.csv", "2026-09-01")
    path = write_csv(tmp_path, "onrc.csv", [["name", "cui"], ["New SRL", "14399840"]])
    for _ in range(2):
        datasets.import_file(store, path, "onrc", "https://data.gov.ro/new.csv", "2026-10-19")
    assert datasets.search(store, "old")["total"] == 0
    assert datasets.search(store, "new")["total"] == 1
    assert len(datasets.manifests(store)) == 2


def procurement_file(tmp_path, value="1.234,50", supplier="14399840"):
    return write_csv(tmp_path, "contracts.csv", [
        ["Autoritate contractanta", "CUI ofertant castigator", "Ofertant castigator", "Denumire CPV",
         "Numar anunt atribuire", "Numar contract", "Numar lot", "Data contract", "Valoare contract (RON)"],
        ["Primăria Exemplu", supplier, "Firma SRL", "Servicii IT", "CN1", "123", "1", "2026-07-01", value],
    ], delimiter=";")


def test_procurement_uses_decimal_values_and_deduplicates_overlapping_exports(tmp_path, store):
    path = procurement_file(tmp_path)
    for source in ("q3.csv", "year.csv"):
        datasets.import_file(store, path, "procurement", "https://data.gov.ro/" + source, "2026-10-01",
                             "2026-07-01", "2026-09-30")
    result = datasets.procurement(store, 14399840)
    assert result["total"] == 1
    assert result["value_by_currency"] == {"RON": "1234.50"}
    assert datasets.procurement(store, 14399840, since="2026-08-01")["total"] == 0


def test_contract_update_does_not_double_count_the_original_value(tmp_path, store):
    datasets.import_file(store, procurement_file(tmp_path, "100"), "procurement", "https://data.gov.ro/old.csv", "2026-08-01")
    datasets.import_file(store, procurement_file(tmp_path, "200"), "procurement", "https://data.gov.ro/new.csv", "2026-10-01")
    result = datasets.procurement(store, 14399840)
    assert result["total"] == 1 and result["value_by_currency"] == {"RON": "200"}


def test_empty_index_does_not_claim_no_public_contracts(store):
    assert datasets.procurement(store, 14399840)["status"] == "not_loaded"
    assert datasets.search(store, "firma")["status"] == "not_loaded"


def test_real_seap_xlsx_headers_and_legend_are_supported(tmp_path, store):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Autoritate contractanta", "CUI ofertant castigator", "Ofertant castigator", "Denumire CPV",
                  "Numar contract", "Data contract", "Valoare contract (RON)"])
    sheet.append(["Primăria Exemplu", "RO 14399840", "Firma SRL", "Servicii", "123", "2026-07-01", 910637])
    workbook.create_sheet("Legenda").append(["Not a record"])
    path = tmp_path / "seap.xlsx"
    workbook.save(path)
    result = datasets.import_file(store, path, "procurement", "https://data.gov.ro/seap.xlsx", "2026-10-01")
    assert result["rows"] == 1
    assert datasets.procurement(store, 14399840)["value_by_currency"] == {"RON": "910637"}


def test_ods_reader_handles_repeated_cells_and_ignores_legend(tmp_path, store):
    path = tmp_path / "seap.ods"
    def row(values):
        return '<t:table-row>' + ''.join(f'<t:table-cell><x:p>{v}</x:p></t:table-cell>' for v in values) + '</t:table-row>'
    xml = ('<root xmlns:t="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
           'xmlns:x="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><t:table>' +
           row(["supplier_cui", "buyer", "title", "value", "signed_on"]) +
           row(["14399840", "Primarie", "Servicii", "10,50", "01.07.2026"]) +
           '</t:table><t:table><t:table-row/></t:table></root>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", xml)
    assert datasets.import_file(store, path, "procurement", "https://data.gov.ro/seap.ods", "2026-10-01")["rows"] == 1


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "not money"])
def test_invalid_contract_values_do_not_enter_totals(tmp_path, store, amount):
    with pytest.raises(ValueError, match="No usable rows"):
        datasets.import_file(store, procurement_file(tmp_path, amount), "procurement", "https://data.gov.ro/a.csv", "2026-10-01")
    assert datasets.procurement(store, 14399840)["total"] == 0


def test_remote_import_is_limited_to_official_host(store):
    def handler(request):
        if "resource_show" in str(request.url):
            return httpx.Response(200, json={"success": True, "result": {"package_id": "p", "url": "https://localhost/data.csv"}})
        return httpx.Response(200, json={"success": True, "result": {}})
    async def go():
        async with mock_client(handler) as client:
            return await datasets.sync_resource(client, store, "r", "onrc", "2026-10-01")
    with pytest.raises(ValueError, match="data.gov.ro only"):
        asyncio.run(go())
