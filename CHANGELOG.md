# Changelog

## 0.2.0

- Company reports with financial trends, source receipts, cache age and incomplete-coverage handling.
- ONRC name search with county and CAEN filters, plus atomic CSV, XLSX and ODS imports from data.gov.ro.
- Public contract search by supplier CUI, with source periods, decimal totals and overlap deduplication.
- VIES checks that distinguish invalid VAT registrations from unavailable services.
- Local snapshots, comparisons, watchlists and a monitor command suitable for an existing scheduler.
- Resumable supplier checks with JSON and CSV exports.
- Fixed ANAF failure handling in shop checks. Unavailable financial and court checks no longer pass as clean results.
- Public-address DNS validation, pinned connections and bounded web-page downloads.
- Added `romania-mcp-data` for imports, reports, supplier jobs and monitoring. Existing stdio commands remain available.

## 0.1.0

First release: `shop_check`, `company_lookup`, `company_financials`, `court_cases`, `exchange_rate`, `iban_check`.
