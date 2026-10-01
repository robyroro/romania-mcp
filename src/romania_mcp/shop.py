"""shop_check: is the company behind an online shop real and healthy?

Reads the CUI off the site, then asks ANAF, the balance sheets, Portal Just and the
Wayback Machine, and turns what comes back into red flags / warnings / good signs.
"""

import asyncio
import re
from datetime import date, datetime
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx

from . import anaf, just
from .cache import cached
from .common import describe_error, fold, words

SITE_UA = "Mozilla/5.0 (compatible; romania-mcp/0.1; +https://github.com/robyroro/romania-mcp)"

# links worth following when the homepage doesn't show the CUI
LEGAL_HINTS = ("termeni", "conditii", "contact", "despre", "about", "legal", "informatii",
               "date-firma", "date firma", "companie", "gdpr", "confidentialitate", "anpc")
MAX_EXTRA_PAGES = 4

CUI_RE = re.compile(
    r"(?<![a-z0-9])"
    r"(?:c\.?\s?u\.?\s?i|c\.?\s?i\.?\s?f"
    r"|cod(?:ul)?\s+(?:unic\s+de\s+[iî]nregistrare|fiscal|de\s+identificare\s+fiscal[aă])"
    r"|nr\.?\s+de\s+[iî]nregistrare\s+fiscal[aă])"
    r"(?![a-z])[^0-9a-z]{0,15}?(?:ro\s?)?(\d{2,10})(?!\d)",
    re.I,
)
# 4+ digits so the 'RO49' at the start of an IBAN doesn't count
VAT_RE = re.compile(r"(?<![A-Za-z0-9])RO\s?(\d{4,10})(?!\d)")
REGCOM_RE = re.compile(r"(?<![A-Za-z0-9])(J\s?\d{1,2}\s?/\s?\d{1,8}\s?/\s?\d{4}|J\d{13})(?!\d)")

SECOND_LEVEL = {"com", "org", "net", "co", "info", "nom", "gov", "edu", "ac", "firm", "store", "rec"}


def registrable_domain(host):
    labels = host.lower().strip(".").split(".")
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def page_text(html):
    html = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(text))


def find_cuis(text):
    """Valid CUIs next to a CUI/CIF/cod fiscal label, else any valid RO-prefixed VAT number."""
    out = []
    for m in CUI_RE.finditer(text):
        if anaf.cui_checksum_ok(m.group(1)):
            out.append(int(m.group(1)))
    if not out:
        out = [int(d) for d in VAT_RE.findall(text) if anaf.cui_checksum_ok(d)]
    return list(dict.fromkeys(out))


def find_registration_numbers(text):
    return list(dict.fromkeys(re.sub(r"\s", "", m).upper() for m in REGCOM_RE.findall(text)))


def legal_links(html, base_url):
    host = urlparse(base_url).hostname
    out = []
    for href, label in re.findall(r'(?is)<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html):
        url = urljoin(base_url, unescape(href))
        if urlparse(url).hostname != host or url in out:
            continue
        if any(h in fold(href + " " + page_text(label)) for h in LEGAL_HINTS):
            out.append(url)
    return out[:MAX_EXTRA_PAGES]


def has_anpc_link(html):
    return "anpc.ro" in html.lower()


async def read_site(client, url):
    """Fetch the homepage and, if it shows no CUI, a few legal/contact pages."""
    headers = {"User-Agent": SITE_UA, "Accept-Language": "ro,en;q=0.8"}
    try:
        r = await client.get(url, headers=headers, timeout=15.0)
    except httpx.HTTPError as e:
        return {"fetched": False, "error": describe_error(e)}
    if r.status_code >= 400:
        return {"fetched": False, "error": f"the site answered {r.status_code} (some shops block non-browser requests)"}

    final = str(r.url)
    html = r.text
    text = page_text(html)
    pages = [final]
    cuis = find_cuis(text)
    regcom = find_registration_numbers(text)
    anpc = has_anpc_link(html)

    if not cuis:
        for link in legal_links(html, final):
            try:
                sub = await client.get(link, headers=headers, timeout=10.0)
            except httpx.HTTPError:
                continue
            if sub.status_code >= 400:
                continue
            pages.append(link)
            sub_text = page_text(sub.text)
            text += " " + sub_text
            anpc = anpc or has_anpc_link(sub.text)
            cuis = find_cuis(sub_text)
            regcom = regcom or find_registration_numbers(sub_text)
            if cuis:
                break

    return {
        "fetched": True,
        "final_url": final,
        "https": final.startswith("https://"),
        "pages_checked": pages,
        "cuis_found": cuis,
        "registration_numbers_found": regcom,
        "anpc_links": anpc,
        "_text": text,
    }


async def first_archived(client, domain):
    """Date of the oldest Wayback Machine capture of the domain, None if never archived."""
    async def fetch():
        r = await client.get(
            "https://archive.org/wayback/available",
            params={"url": domain, "timestamp": "19960101"},
            timeout=20.0,
        )
        r.raise_for_status()
        snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
        ts = snap.get("timestamp")
        return {"date": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if ts else None}
    return (await cached(f"wayback-first:{domain}", 7 * 86400, fetch))["date"]


async def domain_registered(client, domain):
    """Registration date from RDAP. .ro has no public RDAP (and ROTLD's whois forbids
    automated use), so for .ro this is None and the Wayback date has to do."""
    if domain.endswith(".ro"):
        return None

    async def fetch():
        r = await client.get(f"https://rdap.org/domain/{domain}", timeout=15.0)
        if r.status_code == 404:
            return {"date": None}
        r.raise_for_status()
        events = {e.get("eventAction"): e.get("eventDate") for e in r.json().get("events", [])}
        reg = events.get("registration")
        return {"date": reg[:10] if reg else None}
    return (await cached(f"rdap-reg:{domain}", 7 * 86400, fetch))["date"]


def _days_since(iso, today):
    if not iso:
        return None
    try:
        return (today - datetime.strptime(iso[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return None


async def _safe(coro):
    try:
        return await coro
    except (httpx.HTTPError, ValueError) as e:
        return {"error": describe_error(e)}


def assess(report, today):
    red, warn, good = [], [], []
    site = report["site"]
    company = report.get("company")
    fin = [y for y in report.get("financials") or [] if y.get("filed", True)]

    if site.get("fetched") and not site.get("cuis_found") and not report.get("cui_given"):
        red.append("no CUI (fiscal code) anywhere on the homepage or legal/contact pages. "
                   "Romanian shops have to show who they are (Legea 365/2002)")
    if site.get("fetched") and not site.get("https"):
        warn.append("site doesn't use https")
    if site.get("fetched"):
        (good if site.get("anpc_links") else warn).append(
            "links to ANPC (consumer protection) as required" if site.get("anpc_links")
            else "no link to ANPC (consumer protection), Romanian shops are required to show one"
        )

    if report.get("company_error"):
        warn.append(f"couldn't check the company at ANAF ({report['company_error']}), try again")
    elif "company" in report and company is None:
        red.append(f"ANAF has no company with CUI {report['cui']}")
    if company:
        red += company["warnings"]
        age = _days_since(company.get("registered_on"), today)
        if age is not None and age < 180:
            warn.append(f"company was registered only {age} days ago ({company['registered_on']})")
        elif age is not None:
            good.append(f"company exists since {company['registered_on']} ({age // 365} years)")

        name_words = [w for w in words(just.search_name(company["name"] or "")) if len(w) > 2]
        text = set(words(site.get("_text", "")))
        if site.get("fetched") and name_words and not all(w in text for w in name_words):
            warn.append(f"the site never mentions '{company['name']}', the company ANAF has for this CUI. "
                        "fake shops sometimes copy a real company's CUI")

    if company and not report.get("financials_error"):
        if not fin:
            warn.append("no financial statements filed for the last two years")
        else:
            latest = fin[0]
            rev, emp = latest.get("revenue"), latest.get("employees")
            if rev == 0:
                warn.append(f"reported zero revenue for {latest['year']}")
            elif rev:
                good.append(f"revenue of {rev:,} RON in {latest['year']}".replace(",", "."))
            if emp == 0:
                warn.append(f"no employees in {latest['year']}")
            elif emp:
                good.append(f"{emp} employees in {latest['year']}")
            if (latest.get("equity") or 0) < 0:
                warn.append(f"negative equity in {latest['year']}, the company owes more than it owns")

    courts = report.get("court_cases") or {}
    if courts.get("insolvency_as_debtor"):
        red.append("in insolvency/bankruptcy proceedings as the debtor: " + ", ".join(courts["insolvency_as_debtor"][:5]))

    online = report.get("domain", {})
    # the site has been around at least since the older of the two
    ages = [d for d in (_days_since(online.get("first_archived"), today),
                        _days_since(online.get("registered"), today)) if d is not None]
    if not ages:
        warn.append("the Wayback Machine has never archived this site, it's probably very new")
    elif max(ages) < 90:
        warn.append(f"the site is only about {max(ages)} days old")
    elif online.get("first_archived"):
        good.append(f"site archived since {online['first_archived']}")
    else:
        good.append(f"domain registered on {online['registered']}")

    if red:
        risk = "high"
    elif len(warn) >= 2:
        risk = "medium"
    elif not site.get("fetched") and not company:
        risk = "unknown"
    else:
        risk = "low"
    return risk, red, warn, good


async def check(client, url, cui=None, today=None):
    today = today or date.today()
    if "://" not in url:
        url = "https://" + url
    host = urlparse(url).hostname
    if not host or "." not in host:
        raise ValueError(f"doesn't look like a website: {url!r}")
    domain = registrable_domain(host)

    site, first, registered = await asyncio.gather(
        read_site(client, url),
        _safe(first_archived(client, domain)),
        _safe(domain_registered(client, domain)),
    )
    report = {
        "url": url,
        "domain": {
            "name": domain,
            "registered": registered if not isinstance(registered, dict) else None,
            "first_archived": first if not isinstance(first, dict) else None,
        },
        "site": site,
    }

    if cui is not None:
        report["cui_given"] = True
        report["cui"] = anaf.clean_cui(cui)
    elif site.get("cuis_found"):
        report["cui"] = site["cuis_found"][0]

    if report.get("cui"):
        cui = report["cui"]
        years = [today.year - 1, today.year - 2]
        found, fin = await asyncio.gather(
            _safe(anaf.lookup(client, [cui])),
            _safe(anaf.financials(client, cui, years)),
        )
        if isinstance(found, dict):
            report["company_error"] = found["error"]
        else:
            report["company"] = found[0][0] if found[0] else None
        if isinstance(fin, dict) and "error" in fin:
            report["financials_error"] = fin["error"]
        else:
            report["financials"] = fin["years"]

        company = report["company"]
        if isinstance(company, dict) and company.get("name"):
            try:
                report["court_cases"] = await just.company_cases(
                    client, company["name"], company.get("county"), limit=5)
            except (httpx.HTTPError, ValueError) as e:
                report["court_cases"] = {"error": describe_error(e)}

    risk, red, warn, good = assess(report, today)
    site.pop("_text", None)
    report.pop("cui_given", None)
    return {
        "risk": risk,
        "red_flags": red,
        "warnings": warn,
        "good_signs": good,
        **report,
        "note": "signals, not proof. a legit company can still run a bad shop, and a scam can "
                "copy a real company's details, so check the company name on the site matches",
    }
