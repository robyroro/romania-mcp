import asyncio

from romania_mcp.server import mcp

TOOLS = asyncio.run(mcp.list_tools())


def test_all_tools_registered():
    assert sorted(t.name for t in TOOLS) == [
        "company_financials", "company_lookup", "court_cases", "exchange_rate", "iban_check", "shop_check",
    ]


def test_every_parameter_has_a_description():
    # clients only see the schema, an undocumented parameter is a guess for the model
    for tool in TOOLS:
        for name, prop in tool.input_schema["properties"].items():
            assert prop.get("description"), f"{tool.name}.{name} has no description"


def test_tools_are_marked_read_only():
    for tool in TOOLS:
        assert tool.title, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


def test_iban_tool_works_offline():
    res = asyncio.run(mcp.call_tool("iban_check", {"iban": "DE89 3704 0044 0532 0130 00"}))
    assert '"valid": true' in res.content[0].text
