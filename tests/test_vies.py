import asyncio

import httpx
import pytest
from conftest import mock_client

from romania_mcp import vies


def check(response):
    async def go():
        async with mock_client(lambda request: response) as client:
            return await vies.check(client, "RO", "RO 14399840")
    return asyncio.run(go())


@pytest.mark.parametrize("valid", ["true", "false"])
def test_valid_and_invalid_vat_results_are_distinct(valid):
    body = (f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            f'<checkVatResponse xmlns="{vies.NS}"><countryCode>RO</countryCode><vatNumber>14399840</vatNumber>'
            f'<requestDate>2026-10-21+02:00</requestDate><valid>{valid}</valid>'
            '<name>COMPANY SA</name></checkVatResponse></s:Body></s:Envelope>')
    result = check(httpx.Response(200, text=body))
    assert result["status"] == "available"
    assert result["data"]["valid"] is (valid == "true")


@pytest.mark.parametrize("fault", ["MS_UNAVAILABLE", "TIMEOUT", "MS_MAX_CONCURRENT_REQ"])
def test_service_faults_are_unavailable_not_invalid(fault):
    body = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            f'<s:Fault><faultstring>{fault}</faultstring></s:Fault></s:Body></s:Envelope>')
    result = check(httpx.Response(500, text=body))
    assert result["status"] == "unavailable"
    assert result["data"] is None
    assert fault in result["error"]


def test_malformed_vies_answer_is_not_an_invalid_registration():
    result = check(httpx.Response(200, text="unavailable"))
    assert result["status"] == "unavailable" and result["data"] is None


def test_mismatched_vat_identity_is_rejected():
    body = (f'<root><checkVatResponse xmlns="{vies.NS}"><countryCode>RO</countryCode>'
            '<vatNumber>18547290</vatNumber><valid>true</valid></checkVatResponse></root>')
    result = check(httpx.Response(200, text=body))
    assert result["status"] == "unavailable" and result["data"] is None


def test_greece_alias_and_rejected_countries():
    assert vies.normalize("gr", "EL123456789") == ("EL", "123456789")
    with pytest.raises(ValueError):
        vies.normalize("US", "12345")
    with pytest.raises(ValueError):
        vies.normalize("RO", "<xml>")
