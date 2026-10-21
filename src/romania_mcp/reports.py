"""Company reports assembled from source receipts, with changes kept separate from missing data."""

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone

from . import anaf, datasets, just
from .evidence import collect, now


def metrics(years):
    result = []
    filed = sorted((y for y in years if y.get("filed", True) and not y.get("error")), key=lambda y: y["year"])
    previous = None
    for year in filed:
        revenue, profit = year.get("revenue"), year.get("net_result")
        item = {"year": year["year"], "revenue": revenue, "net_result": profit,
                "debts": year.get("debts"), "equity": year.get("equity"), "employees": year.get("employees"),
                "profit_margin_percent": round(profit / revenue * 100, 2) if revenue and profit is not None else None,
                "revenue_change_percent": None, "employees_change": None}
        if previous and year["year"] == previous["year"] + 1:
            old = previous.get("revenue")
            if old and revenue is not None:
                item["revenue_change_percent"] = round((revenue - old) / abs(old) * 100, 2)
            if year.get("employees") is not None and previous.get("employees") is not None:
                item["employees_change"] = year["employees"] - previous["employees"]
        result.append(item)
        previous = year
    return list(reversed(result))


async def company_report(client, cui, years=5, refresh=False, store=None):
    cui = anaf.clean_cui(cui)
    if not 1 <= years <= 10:
        raise ValueError("years must be between 1 and 10")
    requested_years = list(range(date.today().year - 1, date.today().year - years - 1, -1))
    company_source = await collect("ANAF company status", anaf.TVA_URL,
                                   lambda: anaf.lookup(client, [cui]), refresh)
    sources = {"company": company_source}
    found = company_source["data"]
    company = found[0][0] if found and found[0] else None
    # Not returned and explicitly notFound are different from an incomplete ANAF response.
    if company is None and found and cui not in found[1]:
        company_source.update(status="unavailable", error="ANAF did not account for this CUI")
    financials = []
    for year in requested_years:
        receipt = await collect("ANAF financial statements", anaf.BILANT_URL,
                                lambda year=year: anaf.bilant(client, cui, year), refresh)
        receipt["period"] = str(year)
        sources[f"financials_{year}"] = receipt
        if receipt["status"] == "available":
            financials.append(receipt["data"]["year"])
            if receipt["data"]["year"].get("filed") is False:
                receipt["status"] = "no_data"
        else:
            financials.append({"year": year, "error": receipt["error"]})
    courts = None
    if company and company.get("name"):
        court_source = await collect("Portal Just", just.URL,
                                     lambda: just.company_cases(client, company["name"], company.get("county"), limit=100),
                                     refresh)
        sources["courts"] = court_source
        courts = court_source["data"]
        if courts and courts.get("truncated"):
            court_source["status"] = "partial"
        if courts is not None:
            courts["matching"] = {
                "method": "company_name_and_legal_form",
                "certainty": "candidate",
                "note": "Portal Just does not provide a CUI. A name match, even in the same county, is not proof of identity.",
            }
    else:
        sources["courts"] = {"source": "Portal Just", "url": just.URL, "status": "not_checked",
                              "checked_at": now(), "error": "Company identity could not be established"}
    coverage = "complete" if all(s["status"] == "available" for s in sources.values()) else "partial"
    findings = []
    if company:
        for message in company.get("warnings", []):
            findings.append({"code": "company_status", "source": "company", "message": message,
                             "certainty": "reported_by_source"})
    elif company_source["status"] == "available":
        findings.append({"code": "not_found", "source": "company", "message": "ANAF did not find this CUI",
                         "certainty": "reported_by_source"})
    analysis = metrics(financials)
    if analysis:
        latest = analysis[0]
        if latest.get("equity") is not None and latest["equity"] < 0:
            findings.append({"code": "negative_equity", "source": f"financials_{latest['year']}",
                             "message": f"Capitaluri proprii negative în {latest['year']}", "certainty": "reported_by_source"})
        if latest.get("revenue_change_percent") is not None and latest["revenue_change_percent"] <= -30:
            findings.append({"code": "revenue_drop", "source": f"financials_{latest['year']}",
                             "message": f"Cifra de afaceri a scăzut cu {abs(latest['revenue_change_percent'])}% față de anul anterior",
                             "certainty": "calculated"})
    if courts and courts.get("insolvency_as_debtor"):
        findings.append({"code": "possible_insolvency", "source": "courts", "certainty": "candidate",
                         "message": "Dosare de insolvență cu o parte având aceeași denumire; verifică identitatea firmei"})
    assessment = "attention" if findings else "incomplete" if coverage != "complete" else "no_flags"
    summary = {"attention": "Sunt informații care merită verificate înainte de o decizie.",
               "incomplete": "Verificarea este incompletă. Unele date lipsesc sau nu au putut fi consultate.",
               "no_flags": "Nu au apărut semnale în verificările efectuate. Asta nu garantează o colaborare fără probleme."}[assessment]
    result = {"cui": cui, "checked_at": now(), "company": company, "financials": financials,
              "financial_analysis": analysis, "court_cases": courts, "coverage": coverage,
              "assessment": assessment, "summary": summary, "findings": findings,
              "missing_sources": [k for k, s in sources.items() if s["status"] != "available"],
              "sources": {k: {n: v for n, v in s.items() if n != "data"} for k, s in sources.items()},
              "note": "Bilanțurile se referă la anii indicați, nu la situația financiară de azi. "
                      "Un an fără bilanț disponibil nu este același lucru cu o eroare de acces la sursă."}
    if store is not None:
        result["public_contracts"] = datasets.procurement(store, cui)
    return result


def changes(before, after):
    """Compare observed facts. Failed sources and cases falling out of a result page are not deletions."""
    changed, skipped = [], []

    def difference(path, left, right):
        if left != right:
            changed.append({"field": path, "before": left, "after": right})

    left_sources, right_sources = before.get("sources", {}), after.get("sources", {})
    for source in sorted(set(left_sources) | set(right_sources)):
        left = left_sources.get(source, {}).get("status", "not_checked")
        right = right_sources.get(source, {}).get("status", "not_checked")
        # A rolling financial window should not produce lost-source alerts every January.
        if source.startswith("financials_") and source not in right_sources:
            continue
        if source in left_sources and source in right_sources:
            difference(f"sources.{source}.status", left, right)
    if (left_sources.get("company", {}).get("status") == "available" and
            right_sources.get("company", {}).get("status") == "available"):
        for field in ("name", "status", "vat_registered", "vat_periods", "vat_on_collection", "fiscally_inactive",
                      "inactive_since", "reactivated_on", "deregistered", "address", "county", "caen",
                      "registration_number", "e_factura_registry"):
            difference(f"company.{field}", (before.get("company") or {}).get(field), (after.get("company") or {}).get(field))
    else:
        skipped.append("company")
    previous = {y["year"]: y for y in before.get("financials", [])}
    for year in after.get("financials", []):
        old = previous.get(year["year"])
        source = f"financials_{year['year']}"
        if year.get("error") or (old and old.get("error")):
            skipped.append(source)
            continue
        if old is None:
            if year.get("filed", True):
                difference(source, None, year)
        else:
            difference(source, old, year)
    if (left_sources.get("courts", {}).get("status") == "available" and
            right_sources.get("courts", {}).get("status") == "available"):
        old_cases = {(c["number"], c["court"]): c for c in (before.get("court_cases") or {}).get("cases", [])}
        for case in (after.get("court_cases") or {}).get("cases", []):
            identity = (case["number"], case["court"])
            old = old_cases.get(identity)
            for field in ("stage", "last_modified", "latest_outcome", "next_hearing"):
                difference(f"court_cases.{case['number']}.{case['court']}.{field}", old.get(field) if old else None, case.get(field))
    else:
        skipped.append("courts")
    return {"cui": after["cui"], "changed": bool(changed), "changes": changed,
            "not_comparable": skipped, "coverage": after["coverage"],
            "note": "Changes are between saved observations, not a complete history from the registries."}


async def supplier_check(client, store, cuis=None, job_id=None, limit=3, years=3):
    if not 1 <= limit <= 20 or not 1 <= years <= 10:
        raise ValueError("Use limit 1..20 and years 1..10")
    if job_id and cuis:
        raise ValueError("Pass a new list of CUIs or an existing job_id")
    if not job_id:
        if not cuis or len(cuis) > 500:
            raise ValueError("Provide between 1 and 500 CUIs")
        # Validate the entire input before creating a persistent job.
        cleaned = [anaf.clean_cui(c) for c in cuis]
        job_id = store.create_job(cleaned, years)
    job = store.job(job_id)
    for item in [r for r in job["items"] if r["state"] == "pending"][:limit]:
        report = await company_report(client, item["cui"], years=job["years"], store=store)
        store.finish_item(job_id, item["cui"], report)
    job = store.job(job_id)
    return {k: job[k] for k in ("id", "created_at", "years", "total", "completed", "pending")}


def supplier_results(store, job_id, format="json", offset=0, limit=100):
    if format not in ("json", "csv") or offset < 0 or not 1 <= limit <= 500:
        raise ValueError("Use json or csv, a non-negative offset and limit 1..500")
    job = store.job(job_id)
    rows = []
    for item in job["items"]:
        report = json.loads(item["payload"]) if item["payload"] else {}
        rows.append({"cui": item["cui"], "state": item["state"], "name": (report.get("company") or {}).get("name"),
                     "assessment": report.get("assessment"), "coverage": report.get("coverage"),
                     "checked_at": report.get("checked_at"), "summary": report.get("summary"),
                     "findings": report.get("findings", []), "missing_sources": report.get("missing_sources", []),
                     "sources": report.get("sources", {})})
    rows.sort(key=lambda r: ({"attention": 0, "incomplete": 1, "no_flags": 2}.get(r["assessment"], 3), r["cui"]))
    page = rows[offset:offset + limit]
    result = {"job_id": job_id, "total": job["total"], "completed": job["completed"], "pending": job["pending"],
              "offset": offset, "next_offset": offset + len(page) if offset + len(page) < len(rows) else None}
    if format == "json":
        result["suppliers"] = page
    else:
        output = io.StringIO(newline="")
        fields = ["cui", "state", "name", "assessment", "coverage", "checked_at", "summary", "findings", "missing_sources", "sources"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in page:
            safe = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()}
            # A company name or source message must not turn into an Excel formula.
            safe = {k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for k, v in safe.items()}
            writer.writerow(safe)
        result["csv"] = output.getvalue()
    return result


async def monitor(client, store, limit=10):
    checked, events = 0, []
    for watch in store.watches(due=True, limit=limit):
        previous = store.history(watch["cui"], 1)
        report = await company_report(client, watch["cui"], years=3, refresh=True, store=store)
        delta = changes(previous[0]["report"], report) if previous else None
        snapshot_id = store.save(report)
        due = datetime.now(timezone.utc) + timedelta(hours=watch["interval_hours"])
        with store.connect() as db:
            db.execute("update watches set next_due=?,last_snapshot=? where cui=?",
                       (due.isoformat(), snapshot_id, watch["cui"]))
        if delta and delta["changed"]:
            events.append({"type": "changed", "snapshot_id": snapshot_id, **delta})
        elif not previous and report["coverage"] != "complete":
            events.append({"type": "incomplete", "cui": watch["cui"], "missing_sources": report["missing_sources"]})
        checked += 1
    return {"checked": checked, "events": events,
            "note": "Run this command periodically with your scheduler. Adding a watch does not start a background service."}
