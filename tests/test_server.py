import asyncio

from romania_mcp.server import mcp

TOOLS = asyncio.run(mcp.list_tools())


def test_all_tools_registered():
    assert sorted(t.name for t in TOOLS) == [
        "company_changes", "company_contracts", "company_financials", "company_history", "company_lookup",
        "company_report", "company_search", "company_snapshot", "company_watch", "company_watchlist",
        "court_cases", "dataset_catalog", "dataset_import", "dataset_status", "exchange_rate", "iban_check",
        "monitor_run", "shop_check", "supplier_check", "supplier_results", "vat_check",
    ]


def test_every_parameter_has_a_description():
    # clients only see the schema, an undocumented parameter is a guess for the model
    for tool in TOOLS:
        for name, prop in tool.input_schema["properties"].items():
            assert prop.get("description"), f"{tool.name}.{name} has no description"


def test_tool_annotations_distinguish_local_writes():
    writes = {"company_snapshot", "company_watch", "dataset_import", "monitor_run", "supplier_check"}
    for tool in TOOLS:
        assert tool.title, tool.name
        assert tool.annotations.read_only_hint is (tool.name not in writes), tool.name
        assert tool.annotations.destructive_hint is False, tool.name


def test_iban_tool_works_offline():
    res = asyncio.run(mcp.call_tool("iban_check", {"iban": "DE89 3704 0044 0532 0130 00"}))
    assert '"valid": true' in res.content[0].text
