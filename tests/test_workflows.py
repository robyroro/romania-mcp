import asyncio
import copy
import json

import pytest
from conftest import mock_client
from test_reports import handler, run_report

from romania_mcp import reports
from romania_mcp.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state.sqlite3")


def test_history_survives_reopening_database(store):
    first = store.save(run_report())
    second = store.save(run_report())
    history = Store(store.path).history(14399840)
    assert [r["id"] for r in history] == [second, first]


def test_supplier_check_resumes_without_repeating_finished_suppliers(store):
    async def go():
        async with mock_client(handler) as client:
            first = await reports.supplier_check(client, store, ["14399840", "18547290", "14399840"], limit=1, years=1)
            assert first["completed"] == 1 and first["pending"] == 1
            reopened = Store(store.path)
            second = await reports.supplier_check(client, reopened, job_id=first["id"], limit=1)
            third = await reports.supplier_check(client, reopened, job_id=first["id"], limit=1)
            assert second["completed"] == third["completed"] == 2
            return first["id"]
    job_id = asyncio.run(go())
    result = reports.supplier_results(store, job_id, limit=1)
    assert result["total"] == 2 and result["next_offset"] == 1
    assert result["suppliers"][0]["sources"]["company"]["url"]


def test_all_cuis_are_validated_before_a_job_is_created(store):
    async def go():
        async with mock_client(handler) as client:
            await reports.supplier_check(client, store, ["14399840", "bad"])
    with pytest.raises(ValueError):
        asyncio.run(go())
    with store.connect() as db:
        assert db.execute("select count(*) from jobs").fetchone()[0] == 0


def test_csv_neutralizes_company_name_formulas_and_keeps_sources(store):
    job_id = store.create_job([14399840], 1)
    report = run_report()
    report["company"]["name"] = "=HYPERLINK(123)"
    store.finish_item(job_id, 14399840, report)
    text = reports.supplier_results(store, job_id, "csv")["csv"]
    assert "'=HYPERLINK" in text
    assert "webservicesp.anaf.ro" in text


def test_incomplete_suppliers_are_visible_and_sort_before_clean_results(store):
    job_id = store.create_job([14399840, 18547290], 1)
    clean = run_report()
    incomplete = copy.deepcopy(clean)
    incomplete.update(cui=18547290, assessment="incomplete", coverage="partial", missing_sources=["courts"])
    store.finish_item(job_id, 14399840, clean)
    store.finish_item(job_id, 18547290, incomplete)
    rows = reports.supplier_results(store, job_id)["suppliers"]
    assert rows[0]["cui"] == 18547290 and rows[0]["missing_sources"] == ["courts"]


def test_monitor_establishes_baseline_and_is_quiet_when_unchanged(store):
    store.watch(14399840, 24)
    async def go():
        async with mock_client(handler) as client:
            assert (await reports.monitor(client, store))["events"] == []
            assert (await reports.monitor(client, store))["checked"] == 0
            with store.connect() as db:
                db.execute("update watches set next_due='2000-01-01'")
            assert (await reports.monitor(client, store))["events"] == []
    asyncio.run(go())
    assert len(store.history(14399840)) == 2


def test_monitor_emits_a_real_status_change_and_keeps_paused_watches(store):
    store.save(run_report(years=3))
    store.watch(14399840)
    store.watch(18547290, enabled=False)
    def changed(request):
        response = handler(request)
        if "PlatitorTvaRest" in str(request.url):
            data = json.loads(response.content)
            data["found"][0]["stare_inactiv"]["statusInactivi"] = True
            return type(response)(200, json=data)
        return response
    async def go():
        async with mock_client(changed) as client:
            return await reports.monitor(client, store)
    result = asyncio.run(go())
    assert result["checked"] == 1
    assert result["events"][0]["type"] == "changed"
    assert any(c["field"] == "company.fiscally_inactive" for c in result["events"][0]["changes"])
    assert len(store.watches()) == 2


def test_cli_empty_monitor_has_no_stdout(tmp_path, capsys):
    from romania_mcp.cli import parser, run
    args = parser().parse_args(["--data-path", str(tmp_path / "cli.sqlite3"), "monitor"])
    asyncio.run(run(args))
    assert capsys.readouterr().out == ""
