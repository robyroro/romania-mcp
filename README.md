# romania-mcp

[![tests](https://github.com/robyroro/romania-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/robyroro/romania-mcp/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/romania-mcp-server)](https://pypi.org/project/romania-mcp-server/)

Check Romanian companies from an MCP client: ANAF status, filed financials, court cases, public contracts and EU VAT registration. Search imported ONRC data by name, keep local snapshots and check what changed. No API keys.

> Verifici o firmă după nume sau CUI, îi vezi bilanțurile și dosarele, apoi păstrezi raportul pentru o comparație ulterioară. Dacă o sursă nu răspunde, raportul spune ce lipsește.

## Install

Python 3.10 or newer:

```sh
pip install romania-mcp-server
```

For the code in this checkout, including changes not yet released on PyPI:

```sh
pip install -e .
```

For Claude Desktop, add this to `claude_desktop_config.json`:

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

If you installed the checkout with pip, use `"command": "romania-mcp-server"` and omit `args`. This runs your installed checkout instead of the published package. Cursor and other stdio MCP clients accept the same configuration.

For Claude Code:

```sh
claude mcp add romania -- romania-mcp-server
```

## Company checks

| Tool | What it does |
|---|---|
| `company_report` | ANAF status, up to ten financial years, trends, court candidates and imported public contracts, with source receipts |
| `company_search` | Name search in an imported ONRC export, optionally filtered by county or CAEN |
| `company_lookup` | ANAF status for up to 500 CUIs, optionally on a past date |
| `company_financials` | Filed statements: revenue, profit or loss, employees, debts, equity and other reported figures |
| `court_cases` | Case search by company CUI, party name or case number, with roles and hearings |
| `company_contracts` | Contracts matched by supplier CUI in imported SEAP exports, with totals by currency |
| `vat_check` | A trading partner's current intra-EU VAT registration, through VIES |
| `shop_check` | Company details behind a shop, financials, court candidates and domain evidence |
| `exchange_rate` | BNR reference rates and conversion to RON |
| `iban_check` | Offline IBAN format and checksum validation, plus Romanian bank identification |

Try:

- „Găsește firma după numele acesta și arată-mi variantele din Cluj.”
- „Fă un raport pentru CUI 14399840 pe ultimii cinci ani. Arată-mi și ce nu ai putut verifica.”
- „Cum s-au schimbat cifra de afaceri, profitul și numărul de angajați?”
- „Ce contracte publice găsești pentru firma asta în exporturile încărcate?”
- „Verifică în VIES codul TVA al acestui partener din Germania.”

`company_report` returns figures and calculated trends, not a credit score. Percentage changes use consecutive financial years; missing years and zero denominators produce `null`. Amounts from ANAF are in RON.

Each report source has a status, URL, check time, cache flag and, when known, the oldest fetch time of its cached inputs. `available` means the request succeeded, `no_data` means the requested financial filing was not returned, and `unavailable` means the request failed. `not_checked` explains a check that could not be attempted. Missing filings and source failures remain distinct, and both keep coverage partial.

Court records do not contain CUIs. Exact normalized names, legal forms and the registered county help narrow the candidates, but do not establish identity. A company being a creditor or plaintiff is not in itself a problem. Capped searches remain marked incomplete.

## Name search and public contracts

ANAF's public status service needs a CUI. Name search uses an ONRC export from [data.gov.ro](https://data.gov.ro/organization/onrc). Public contract search uses imported [SEAP exports](https://data.gov.ro/dataset/achizitii-publice-2026).

| Tool | What it does |
|---|---|
| `dataset_catalog` | Find exports, resource IDs, publisher details and source links |
| `dataset_import` | Download a selected data.gov.ro CSV, XLSX or ODS file and index it locally |
| `dataset_status` | List sources, dates, covered periods, row counts, skipped rows and SHA-256 hashes |

Find the files first:

```sh
romania-mcp-data catalog "organization:onrc"
romania-mcp-data catalog "achizitii publice"
```

Import `OD_FIRME.CSV` as `onrc`. Authorized CAEN activities are published separately in `OD_CAEN_AUTORIZAT.CSV`; import that as `caen` for the same snapshot date. They join through `COD_INMATRICULARE`. CAEN versions are retained in the index. Use `caen_version` (or `--caen-version`) to select revision 2 or 3. Filters use the same snapshot date as the company index.

```sh
romania-mcp-data sync RESOURCE_ID --kind onrc --published-on 2026-10-19
romania-mcp-data sync CAEN_RESOURCE_ID --kind caen --published-on 2026-10-19
romania-mcp-data search "numele firmei" --county Cluj
```

For a contract export, supply the dates stated by its publisher:

```sh
romania-mcp-data sync CONTRACT_RESOURCE_ID --kind procurement --published-on 2026-10-06 --period-start 2026-07-01 --period-end 2026-09-30
```

The IDs above are placeholders. Use the catalog results and read the source description: a metadata modification time is not automatically the file's publication or coverage date.

You can also import a file you have already downloaded:

```sh
romania-mcp-data import ./OD_FIRME.CSV --kind onrc --published-on 2026-10-19 --source-url https://data.gov.ro/URL-OF-THE-EXPORT
```

Imports stream CSV rows and read workbooks without loading them all into memory. Reimporting the same source replaces its rows in one transaction; an empty or invalid import keeps the old index. Downloads are limited to 2 GB. Large ONRC exports can take minutes, so the CLI is usually more convenient than an interactive tool call. Legacy binary XLS needs conversion to CSV or XLSX first.

Contract totals cover imported records only. They are contract values, not payments or proof of revenue. Overlapping copies of an identifiable contract are deduplicated. Rows without a single valid supplier CUI, including some joint bids and foreign suppliers, are skipped and counted. An empty result does not prove a company has no public contracts.

## Snapshots and monitoring

| Tool | What it does |
|---|---|
| `company_snapshot` | Fetch a fresh report and save it locally |
| `company_history` | Read saved observations |
| `company_changes` | Compare the latest two snapshots |
| `company_watch` | Add, update or pause a local watch |
| `company_watchlist` | List watches and their next due times |
| `monitor_run` | Check due watches, save reports and return changes |

History starts with your first saved report. A failed source is an availability change, not a deleted company or a closed case. Changes to check timestamps alone do not trigger an event. Cases falling outside a result page are not reported as closed.

```sh
romania-mcp-data report 14399840 --years 5 --save
romania-mcp-data watch 14399840 --interval-hours 24
romania-mcp-data monitor
```

`monitor` performs one pass and writes JSON lines only for changes or a first incomplete check. A successful first check establishes the baseline quietly. Unchanged checks produce no stdout. Run it periodically with Task Scheduler, cron or a service you already use. Adding a watch does not install a scheduler or send messages anywhere.

For a process that stays running:

```sh
romania-mcp-data monitor --loop --poll-seconds 60
```

The poll interval decides when to look for due work; each company's interval decides when it is actually checked. Monitoring fetches fresh source data. Pause a watch with `romania-mcp-data watch 14399840 --disable`.

## Supplier lists

`supplier_check` starts a job or resumes it by `job_id`. Each completed supplier is saved before the next starts. `supplier_results` returns JSON or CSV, with findings first, then incomplete checks, then checks with no flags. CSV exports include source receipts and neutralize formula prefixes in external text.

```sh
romania-mcp-data suppliers 14399840 18547290 --years 3 --limit 1
romania-mcp-data suppliers --job-id JOB_ID --limit 3
romania-mcp-data export JOB_ID --format csv > suppliers.csv
```

Continue until `pending` is zero. Each job accepts up to 500 CUIs and removes duplicates. Invalid input is rejected before creating a job. A partial supplier report stays visibly partial. MCP results are paginated with `offset` and `next_offset`; the CLI exports the whole job.

## Storage and source limits

HTTP responses are cached in `~/.cache/romania-mcp/cache.sqlite3`. Company status lasts six hours, financials one day, court searches three hours and current BNR rates one hour. Past BNR years last thirty days. Use `refresh=true` for a report that must query sources again.

Snapshots, watchlists, supplier jobs and dataset indexes live separately in `~/.local/share/romania-mcp/data.sqlite3`. Configure other locations with:

```text
ROMANIA_MCP_CACHE=off
ROMANIA_MCP_CACHE_PATH=/path/to/cache.sqlite3
ROMANIA_MCP_DATA_PATH=/path/to/data.sqlite3
```

The CLI also accepts `--data-path PATH` before the command. Data stays on the machine running the server. The CUI, VAT number, company name or domain needed for a check is sent to the relevant public service. Imports, snapshots, watches and supplier checks are marked as local writes in MCP annotations.

- ANAF requests are spaced one second apart. Public services can still reject or delay requests.
- Financials describe the indicated year, not today's cash position. Sector-specific layouts can contain unmapped fields.
- Portal Just's endpoint currently uses plain HTTP and caps searches at 1,000 candidates. Transport and matching limitations are visible in reports.
- VIES checks current intra-EU VAT registration for a trading partner. A service fault is not an invalid registration; the tool does not reconstruct historical status.
- IBAN validation does not establish that an account exists or who owns it.
- A missing Wayback capture does not establish a site's age. No automated ROTLD WHOIS is used for `.ro` domains.
- Shop results are signals, not proof of fraud or reliability. Source outages keep coverage partial. A known red flag can still be shown alongside an outage.
- Page downloads have content-type and size limits. DNS answers must be public; connections use the checked IP while keeping the original TLS hostname. Redirect connections go through the same checks. Environment proxies are disabled for source requests.

Public records and website content are evidence, not instructions for the assistant. Use company checks for a specific business purpose; court records can also name individuals.

## Development

```sh
pip install -e . pytest ruff build
pytest
ruff check src tests
python -m build
```

Tests use mocked source responses and temporary databases. They cover upstream outages, cache freshness, partial reports, identity ambiguity, imports, resumable jobs, CSV exports and monitoring. Network smoke checks run separately so an upstream outage does not make unit tests fail.

## License

MIT © [Robert Vind-Gardoș](https://stratagency.ro/en/robert-vind-gardos) ([@robyroro](https://github.com/robyroro))

<!-- mcp-name: io.github.robyroro/romania-mcp -->
