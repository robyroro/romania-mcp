import logging
from datetime import datetime
from typing import Annotated

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import anaf, bnr, just, shop
from .iban import check as validate_iban
from .common import describe_error, make_client

# httpx logs every request at INFO, way too chatty
logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer(
    "romania",
    instructions=(
        "Romanian public data, no API keys: company status from ANAF (VAT, fiscally "
        "inactive, deregistered), filed financial statements, court cases from Portal Just, "
        "BNR exchange rates and IBAN checks. Companies are identified by CUI (fiscal code). "
        "For 'is this online shop legit?' start with shop_check."
    ),
)

# every tool only reads public data, nothing gets changed anywhere
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

CUI = Annotated[str, Field(
    description='Company fiscal code (CUI / CIF), with or without the RO prefix, e.g. "14399840" or "RO14399840".'
)]


def _cui(value):
    try:
        return anaf.clean_cui(value)
    except ValueError as e:
        raise ToolError(str(e))


def _date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    raise ToolError(f"can't read the date {value!r}, use YYYY-MM-DD or DD.MM.YYYY")


async def _run(coro_fn, *args, **kwargs):
    async with make_client() as client:
        try:
            return await coro_fn(client, *args, **kwargs)
        except httpx.HTTPError as e:
            raise ToolError(describe_error(e))
        except ValueError as e:
            raise ToolError(str(e))


@mcp.tool(title="Company lookup (ANAF)", annotations=READ_ONLY)
async def company_lookup(
    cuis: Annotated[list[str], Field(
        min_length=1, max_length=500,
        description='One or more CUIs, with or without RO, e.g. ["14399840", "RO18547290"]. Up to 500 in one call.',
    )],
    date: Annotated[str | None, Field(
        description="Check the status as it was on this day (YYYY-MM-DD or DD.MM.YYYY), e.g. an invoice "
                    "date to see if the supplier was VAT registered then. Defaults to today."
    )] = None,
) -> dict:
    """Official status of Romanian companies from ANAF's public registry: name, address,
    registration number (J...), CAEN code, registration date, whether it's deregistered
    (radiată), VAT registered (and since when), VAT on collection, split VAT, declared
    fiscally inactive, and in the RO e-Factura registry. Each company comes with a
    'warnings' list (deregistered, fiscally inactive, suspended).

    Use it to vet suppliers or clients, e.g. checking a list of suppliers for fiscally
    inactive ones, since buyers generally can't deduct expenses or VAT on their invoices.
    Only searches by CUI, not by name. CUIs with a wrong control digit are returned
    under 'invalid' without asking ANAF. ANAF allows about one request per second, so
    every 100 CUIs take a second. Cached for 6h."""
    on = _date(date)
    valid, invalid = [], []
    for raw in cuis:
        try:
            valid.append(anaf.clean_cui(raw))
        except ValueError as e:
            invalid.append({"input": raw, "error": str(e)})
    found, not_found = ([], [])
    if valid:
        found, not_found = await _run(anaf.lookup, valid, on.isoformat() if on else None)
    return {"companies": found, "not_found": not_found, "invalid": invalid}


@mcp.tool(title="Company financials (bilanț)", annotations=READ_ONLY)
async def company_financials(
    cui: CUI,
    years: Annotated[int, Field(
        ge=1, le=10,
        description="How many years to fetch, counting back from last year. Statements for a "
                    "year are filed by mid next year, so the most recent one can still be missing.",
    )] = 5,
) -> dict:
    """Financial statements a Romanian company filed with ANAF, year by year: revenue
    (cifra de afaceri), total income and expenses, net profit or loss (net_result),
    average employees, debts, equity, cash, receivables, inventory and fixed assets.
    Amounts are in RON. Years with nothing filed are marked filed=false.

    Use it to see if a company is real and how it's doing: a shop with zero revenue and
    no employees, or with negative equity, is worth a closer look. Banks and insurers
    file a different layout, their lines come through under 'other'. Data starts
    around 2020 for some companies. Takes about a second per year. Cached for 24h."""
    cui = _cui(cui)
    last = datetime.now().year - 1
    return await _run(anaf.financials, cui, list(range(last, last - years, -1)))


@mcp.tool(title="Court cases (Portal Just)", annotations=READ_ONLY)
async def court_cases(
    cui: Annotated[str | None, Field(
        description="A company's CUI. Its exact name and county come from ANAF, so only that "
                    "company's cases are kept and insolvency is checked at the right court. Best for companies."
    )] = None,
    party: Annotated[str | None, Field(
        description='Name of a party to search for, e.g. "Dante International". "SC" and the legal form '
                    "(SRL, SA) are dropped so different spellings match. Diacritics don't matter."
    )] = None,
    case_number: Annotated[str | None, Field(
        description='Case number, e.g. "17902/3/2023". Use instead of or together with party.'
    )] = None,
    court: Annotated[str | None, Field(
        description='Only cases at this court, e.g. "Tribunalul Cluj" or "Judecatoria Sectorul 4". '
                    "Ambiguous names return an error listing the matching courts."
    )] = None,
    since: Annotated[str | None, Field(
        description="Only cases registered on or after this day (YYYY-MM-DD or DD.MM.YYYY)."
    )] = None,
    limit: Annotated[int, Field(
        ge=1, le=100,
        description="How many cases to return in detail, most recently updated first. Counts cover all of them.",
    )] = 20,
) -> dict:
    """Court cases from Portal Just, the public portal of every Romanian court: case
    number, court, category (civil, criminal, insolvency...), stage, subject, parties
    and their roles, latest outcome and next hearing. For a party it also counts its
    roles (defendant, creditor...) and lists insolvency cases where it's the debtor,
    the strongest red flag for a company.

    Prefer cui for companies: the portal has no fiscal codes, so a name search also
    returns other companies with similar names. A big company being creditor or
    plaintiff in many cases is normal, look at the roles. Portal Just caps a search at
    1000 cases. Usually answers in 1-5s. Cached for 3h."""
    if not (cui or party or case_number):
        raise ToolError("pass a cui, a party name or a case number")
    try:
        court_code = just.match_court(court) if court else None
    except ValueError as e:
        raise ToolError(str(e))
    start = _date(since)
    since_iso = start.isoformat() if start else None

    if cui and not case_number:
        found, _ = await _run(anaf.lookup, [_cui(cui)])
        if not found:
            raise ToolError(f"ANAF has no company with CUI {cui}")
        company = found[0]
        result = await _run(just.company_cases, company["name"], company["county"],
                            limit=limit, court=court_code, since=since_iso)
        return {"company": company["name"], "county": company["county"], **result}

    cases = await _run(just.search, party=party, case_number=case_number, court=court_code, since=since_iso)
    return just.report(cases, party=party, limit=limit)


@mcp.tool(title="BNR exchange rate", annotations=READ_ONLY)
async def exchange_rate(
    currency: Annotated[str, Field(
        description='ISO code like "EUR", "USD", "GBP", or "ALL" for every currency BNR publishes.'
    )] = "EUR",
    date: Annotated[str | None, Field(
        description="Day to get the rate for (YYYY-MM-DD or DD.MM.YYYY), from 2005 on. "
                    "Weekends and holidays give the last rate before. Defaults to the latest rate."
    )] = None,
    amount: Annotated[float | None, Field(
        description="Amount in that currency to convert to RON, e.g. 1500 for 1500 EUR."
    )] = None,
) -> dict:
    """Official National Bank of Romania (BNR) reference rate: how many RON one unit of a
    currency is worth on a day, and optionally an amount converted to RON. rate_date is
    the day of the fixing that was used.

    BNR publishes once per working day around 13:00. For a VAT invoice in foreign
    currency the usual rule is the last rate published before the invoice date, so
    ask for the day before. Cached for 1h (past years for 30 days)."""
    day = _date(date)
    rate_date, rates = await _run(bnr.rates_on, day)
    if currency.strip().upper() == "ALL":
        return {"rate_date": rate_date, "base": "RON", "rates": rates}
    try:
        rate = bnr.pick(rates, currency)
    except ValueError as e:
        raise ToolError(str(e))
    out = {"currency": currency.strip().upper(), "rate_date": rate_date, "ron_per_unit": rate}
    if day:
        out["requested_date"] = day.isoformat()
    if amount is not None:
        out["amount"] = amount
        out["amount_ron"] = round(amount * rate, 2)
    return out


@mcp.tool(title="Online shop check", annotations=READ_ONLY)
async def shop_check(
    url: Annotated[str, Field(
        description='The shop\'s address, e.g. "https://example.ro" or just "example.ro".'
    )],
    cui: Annotated[str | None, Field(
        description="The company's CUI, if you already know it or the site hides it from bots."
    )] = None,
) -> dict:
    """Is an online shop legit? Reads the CUI off the site (homepage, then terms /
    contact pages), then checks the company at ANAF, its last two years of financials,
    insolvency and other cases on Portal Just, and how long the site has been online
    (Wayback Machine). Returns a risk level (high / medium / low / unknown) with
    red_flags, warnings and good_signs, plus all the data behind them.

    Use this first for "e țeapă?" questions, then the other tools to dig deeper. The
    result is signals, not proof: a scam can copy a real company's CUI, which is why it
    checks that the company name appears on the site. Fetches a few pages of the shop
    like a browser would. Takes 5-20s."""
    return await _run(shop.check, url, cui)


@mcp.tool(title="IBAN check", annotations=READ_ONLY)
async def iban_check(
    iban: Annotated[str, Field(
        description='IBAN to check, spaces allowed, e.g. "RO49 AAAA 1B31 0075 9384 0000".'
    )],
) -> dict:
    """Check that an IBAN is well formed (length and mod-97 check digits, catches typos)
    and, for Romanian IBANs, which bank issued it and whether it's a State Treasury
    account. Works for any country's IBAN, offline, nothing is sent anywhere.

    A valid IBAN only means the number is well formed: it doesn't prove the account
    exists or who owns it. Use company_lookup to check the company you're paying."""
    return validate_iban(iban)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
