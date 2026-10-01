# romania-mcp

[![tests](https://github.com/robyroro/romania-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/robyroro/romania-mcp/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/romania-mcp-server)](https://pypi.org/project/romania-mcp-server/)

MCP server for Romanian public data. Lets Claude (or any MCP client) check a company at ANAF, read its financial statements, find its court cases, get BNR exchange rates, validate IBANs, and put all of it together to tell you if an online shop looks like a scam. No API keys, everything comes from official public sources.

> **Pe scurt:** verifici o firmă după CUI (activă, radiată, inactivă fiscal, plătitoare de TVA), îi vezi bilanțurile, dosarele din instanță și cursul BNR, direct din Claude. Și îl poți întreba „e țeapă magazinul ăsta?”.

## Tools

| tool | what it does | source |
|---|---|---|
| `shop_check` | is this online shop legit? reads the CUI off the site, checks the company, its financials, insolvency cases and how old the site is, returns red flags / warnings / good signs | everything below + Wayback Machine |
| `company_lookup` | status of up to 500 companies by CUI: name, address, Reg. Com. number, CAEN, deregistered, VAT registered, VAT on collection, split VAT, fiscally inactive, e-Factura registry. optionally as of a past date | ANAF |
| `company_financials` | filed balance sheets year by year: revenue, profit/loss, employees, debts, equity, cash | ANAF |
| `court_cases` | cases in every Romanian court by party name or case number: stage, parties and roles, latest outcome, next hearing, insolvency as debtor | Portal Just |
| `exchange_rate` | BNR reference rate for any day since 2005, with conversion | BNR |
| `iban_check` | is an IBAN well formed, which Romanian bank issued it, is it a Treasury account | offline |

## Install

Needs Python 3.10+.

```
pip install romania-mcp-server
```

or if you use uv you don't need to install anything, just point the client at `uvx` (see below).

### Claude Desktop

Add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "romania": {
      "command": "uvx",
      "args": ["romania-mcp-server"]
    }
  }
}
```

If you installed with pip, `"command": "romania-mcp-server"` with no args works too.

### Claude Code

```
claude mcp add romania -- uvx romania-mcp-server
```

Cursor, Windsurf etc. take the same JSON as Claude Desktop.

## Example prompts

- "e țeapă magazinul ăsta? https://..."
- "here are my 40 suppliers' CUIs, which ones are fiscally inactive or deregistered?"
- "was RO18547290 VAT registered on 15.03.2016?"
- "how did 14399840 do in the last 5 years? revenue, profit, employees"
- "does Foo Trade SRL have any insolvency cases? when's the next hearing?"
- "cât fac 2.450 EUR în lei la cursul BNR din 14 martie 2025?"
- "is this IBAN right? RO49 AAAA 1B31 0075 9384 0000"

## How `shop_check` decides

It's a list of signals, not a verdict from above:

- **red flags** (risk = high): no CUI anywhere on the site, CUI unknown to ANAF, company deregistered or fiscally inactive, company is the debtor in an insolvency case
- **warnings** (two or more = medium): company or site younger than a few months, never archived by the Wayback Machine, no financial statements, zero revenue or employees, negative equity, no ANPC link, the company name ANAF returns never appears on the site (scam shops sometimes copy a real company's CUI)
- **good signs**: company and site have been around for years, real revenue and employees, ANPC links present

It can't see reviews, delivery times or whether the products exist, so a "low" doesn't mean the shop is good, just that the company behind it checks out.

## Caching

Responses are cached in sqlite at `~/.cache/romania-mcp/cache.sqlite3` so ANAF and friends don't get asked the same thing twice: company status 6h, financials 24h, court cases 3h, current exchange rates 1h, past years' rates 30 days.

```
ROMANIA_MCP_CACHE=off           # disable
ROMANIA_MCP_CACHE_PATH=/tmp/x.db
```

## Notes

- ANAF only searches by CUI, not by company name. Name search would need the ONRC dataset, maybe later.
- ANAF asks for at most one request per second. The server spaces requests out, so 500 CUIs take about 5 seconds and 5 years of financials about 5.
- Financial statements for a year are filed by mid next year, and some companies only have data from around 2020 on.
- Portal Just's API only answers over plain http (their https is broken), and caps a search at 1000 cases.
- `.ro` domains have no public RDAP and ROTLD's whois terms forbid automated queries, so for `.ro` shops the site age comes from the Wayback Machine only.
- `iban_check` knows the main Romanian banks. A valid IBAN doesn't prove who owns the account.

## Development

```
git clone https://github.com/robyroro/romania-mcp
cd romania-mcp
pip install -e . pytest
pytest
```

Tests don't hit the network, HTTP calls are mocked.

To poke at it with the MCP inspector:

```
npx @modelcontextprotocol/inspector romania-mcp
```

## Be reasonable

All of this is public data, but court records name real people. Use it to vet companies you do business with, not to dig into individuals.

## License

MIT © [Robert Vind-Gardoș](https://stratagency.ro/en/robert-vind-gardos) ([@robyroro](https://github.com/robyroro))

<!-- mcp-name: io.github.robyroro/romania-mcp -->
