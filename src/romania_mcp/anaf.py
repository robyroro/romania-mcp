"""ANAF public web services: the VAT payer registry (company status) and filed balance sheets."""

import asyncio
import time
from datetime import date

from .cache import cached, get_cache
from .common import fold

TVA_URL = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"
BILANT_URL = "https://webservicesp.anaf.ro/bilant"
MAX_BATCH = 100  # ANAF's limit per request

# ANAF asks for at most one request per second
MIN_INTERVAL = 1.0
_next_slot = 0.0


async def _throttle():
    global _next_slot
    now = time.monotonic()
    wait = _next_slot - now
    _next_slot = max(now, _next_slot) + MIN_INTERVAL
    if wait > 0:
        await asyncio.sleep(wait)


# --- CUI ---

CUI_KEY = "753217532"


def cui_checksum_ok(digits):
    body, control = digits[:-1], int(digits[-1])
    total = sum(int(d) * int(k) for d, k in zip(body, CUI_KEY[-len(body):]))
    return total * 10 % 11 % 10 == control


def clean_cui(value):
    """'RO 14399840', 'ro14399840', 14399840 -> 14399840. ValueError if the control digit is off."""
    s = str(value).strip().upper().replace(" ", "").removeprefix("RO")
    if not s.isdigit() or not 2 <= len(s) <= 10:
        raise ValueError(f"not a CUI: {value!r}")
    if not cui_checksum_ok(s):
        raise ValueError(f"{value!r} isn't a valid CUI, the control digit doesn't match")
    return int(s)


# --- company status (VAT registry) ---

def _date(value):
    return value or None


def parse_company(item):
    g = item.get("date_generale", {})
    vat = item.get("inregistrare_scop_Tva", {})
    vat_cash = item.get("inregistrare_RTVAI", {})
    inactive = item.get("stare_inactiv", {})
    split = item.get("inregistrare_SplitTVA", {})
    status = g.get("stare_inregistrare") or ""

    out = {
        "cui": g.get("cui"),
        "name": g.get("denumire"),
        "registration_number": g.get("nrRegCom") or None,
        "registered_on": _date(g.get("data_inregistrare")),
        "status": status,
        "deregistered": status.upper().startswith("RADIERE"),
        "address": g.get("adresa"),
        "county": item.get("adresa_sediu_social", {}).get("sdenumire_Judet") or None,
        "caen": g.get("cod_CAEN") or None,
        "legal_form": g.get("forma_juridica") or None,
        "phone": g.get("telefon") or None,
        "tax_office": g.get("organFiscalCompetent") or None,
        "vat_registered": bool(vat.get("scpTVA")),
        "vat_periods": [
            {
                "from": _date(p.get("data_inceput_ScpTVA")),
                "to": _date(p.get("data_sfarsit_ScpTVA")),
                "note": p.get("mesaj_ScpTVA") or None,
            }
            for p in vat.get("perioade_TVA") or []
            if p.get("data_inceput_ScpTVA")
        ],
        "vat_on_collection": bool(vat_cash.get("statusTvaIncasare")),
        "split_vat": bool(split.get("statusSplitTVA")),
        "fiscally_inactive": bool(inactive.get("statusInactivi")),
        "inactive_since": _date(inactive.get("dataInactivare")),
        "reactivated_on": _date(inactive.get("dataReactivare")),
        "e_factura_registry": bool(g.get("statusRO_e_Factura")),
        "as_of": g.get("data"),
    }
    out["warnings"] = company_warnings(out)
    return out


def company_warnings(c):
    out = []
    if c["deregistered"]:
        out.append(f"company is deregistered ({c['status']})")
    if c["fiscally_inactive"]:
        out.append(
            f"declared fiscally inactive since {c['inactive_since']}: buyers generally can't "
            "deduct expenses or VAT on its invoices (Cod fiscal art. 11)"
        )
    if "SUSPENDARE" in c["status"].upper():
        out.append(f"activity is suspended ({c['status']})")
    return out


async def lookup(client, cuis, on=None):
    """Status of any number of CUIs (batched by 100). Returns (found, not_found)."""
    on = on or date.today().isoformat()
    cache = get_cache()
    found = {}
    todo = []
    for cui in dict.fromkeys(cuis):
        hit = cache.get(f"anaf:{cui}:{on}")
        if hit is not None:
            found[cui] = hit
        else:
            todo.append(cui)

    not_found = []
    for i in range(0, len(todo), MAX_BATCH):
        batch = todo[i:i + MAX_BATCH]
        await _throttle()
        r = await client.post(TVA_URL, json=[{"cui": c, "data": on} for c in batch])
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict) or "found" not in body or "notFound" not in body:
            raise ValueError("ANAF returned no company lookup result")
        parsed = [parse_company(item) for item in body["found"] or []]
        missing = [int(item["cui"] if isinstance(item, dict) else item) for item in body["notFound"] or []]
        if any(not c.get("name") or c.get("cui") not in batch for c in parsed):
            raise ValueError("ANAF returned an unexpected company identity")
        if {c["cui"] for c in parsed} | set(missing) != set(batch):
            raise ValueError("ANAF returned an incomplete company lookup result")
        for company in parsed:
            found[company["cui"]] = company
            cache.set(f"anaf:{company['cui']}:{on}", company, 6 * 3600)
        not_found += missing

    return [found[c] for c in dict.fromkeys(cuis) if c in found], not_found


# --- balance sheets ---

# what the summary calls each line, matched on the folded ANAF label
LABELS = {
    "cifra de afaceri neta": "revenue",
    "venituri totale": "total_income",
    "cheltuieli totale": "total_expenses",
    "profit brut": "gross_profit",
    "pierdere bruta": "gross_loss",
    "profit net": "net_profit",
    "pierdere neta": "net_loss",
    "numar mediu de salariati": "employees",
    "active imobilizate": "fixed_assets",
    "active circulante": "current_assets",
    "stocuri": "inventory",
    "creante": "receivables",
    "casa si conturi la banci": "cash",
    "cheltuieli in avans": "prepaid_expenses",
    "datorii": "debts",
    "venituri in avans": "deferred_income",
    "provizioane": "provisions",
    "capitaluri": "equity",
    "capital subscris varsat": "share_capital",
    "patrimoniul regiei": "public_patrimony",
}


def _label_key(label):
    s = fold(label).strip()
    s = s.removesuffix(", din care:").strip()
    s = s.removesuffix("- total").strip()
    return s


def parse_bilant(data):
    """One year of filed financials, None if nothing was filed (or the CUI is unknown)."""
    items = data.get("i") or []
    if not items:
        return None
    out = {"year": data.get("an")}
    other = {}
    for i in sorted(items, key=lambda i: int(i["indicator"].lstrip("I") or 0)):
        key = LABELS.get(_label_key(i["val_den_indicator"]))
        if key:
            out[key] = i["val_indicator"]
        else:
            other[i["val_den_indicator"].strip()] = i["val_indicator"]
    if "net_profit" in out or "net_loss" in out:
        out["net_result"] = out.get("net_profit", 0) - out.get("net_loss", 0)
    if other:
        # banks, insurers and NGOs file a different layout, pass it through as is
        out["other"] = other
    return out


async def bilant(client, cui, year):
    async def fetch():
        await _throttle()
        r = await client.get(BILANT_URL, params={"an": year, "cui": cui})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or data.get("an") != year or data.get("cui") != cui or not isinstance(data.get("i"), list):
            raise ValueError("ANAF returned no matching financial statement result")
        parsed = parse_bilant(data)
        meta = {"name": data.get("deni") or None, "caen": data.get("caen") or None,
                "caen_description": data.get("den_caen") or None}
        return {"meta": meta, "year": parsed or {"year": year, "filed": False}}

    # filed statements barely change, a day is plenty
    return await cached(f"bilant:{cui}:{year}", 24 * 3600, fetch)


async def financials(client, cui, years):
    meta = {}
    out = []
    for year in years:
        res = await bilant(client, cui, year)
        if res["meta"]["name"] and not meta:
            meta = res["meta"]
        out.append(res["year"])
    return {"cui": cui, **meta, "currency": "RON", "years": out}
