"""VIES checks for a customer's intra-EU VAT registration, on explicit request."""

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .evidence import collect

URL = "https://ec.europa.eu/taxation_customs/vies/services/checkVatService"
NS = "urn:ec.europa.eu:taxud:vies:services:checkVat:types"
COUNTRIES = set("AT BE BG HR CY CZ DE DK EE EL ES FI FR HU IE IT LT LU LV MT NL PL PT RO SE SI SK XI".split())


def normalize(country, number):
    country = country.strip().upper()
    country = {"GR": "EL"}.get(country, country)
    number = re.sub(r"\s+", "", number).upper()
    if country not in COUNTRIES:
        raise ValueError("Use an EU country code or XI for Northern Ireland")
    if number.startswith(country):
        number = number[2:]
    if not re.fullmatch(r"[0-9A-Z+*.]{2,12}", number):
        raise ValueError("The VAT number has an unexpected format")
    return country, number


async def check(client, country, number):
    country, number = normalize(country, number)

    async def fetch():
        body = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                f'<s:Body><checkVat xmlns="{NS}"><countryCode>{country}</countryCode>'
                f'<vatNumber>{escape(number)}</vatNumber></checkVat></s:Body></s:Envelope>')
        response = await client.post(URL, content=body.encode(),
                                     headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'})
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            response.raise_for_status()
            raise ValueError("VIES returned an unreadable answer") from exc
        fault = root.find(".//faultstring")
        if fault is not None:
            raise ValueError(f"VIES could not complete the check: {fault.text}")
        response.raise_for_status()
        item = root.find(f".//{{{NS}}}checkVatResponse")
        if item is None:
            raise ValueError("VIES returned no validation result")
        fields = {x.tag.rsplit("}", 1)[-1]: (x.text or "").strip() for x in item}
        if fields.get("countryCode") != country or fields.get("vatNumber") != number:
            raise ValueError("VIES returned a result for a different VAT number")
        if fields.get("valid") not in ("true", "false", "1", "0"):
            raise ValueError("VIES returned no valid/invalid status")
        return {"country": country, "vat_number": number, "valid": fields["valid"] in ("true", "1"),
                "request_date": fields.get("requestDate"),
                "name": fields.get("name") or None, "address": fields.get("address") or None}

    result = await collect("VIES", URL, fetch)
    result["note"] = ("Checks current intra-EU VAT registration for a trading partner. Unavailable is not invalid. "
                      "This does not reconstruct a past registration or confirm a bank account owner.")
    return result
