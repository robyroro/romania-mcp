"""Portal Just (portal.just.ro): case search across every Romanian court, over its SOAP API."""

import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from xml.sax.saxutils import escape

from .cache import cached
from .common import fold, words
from .courts import COURTS

# https is broken on their side (cloudflare 525), the API only answers on http
URL = "http://portalquery.just.ro/query.asmx"
NS = "portalquery.just.ro"
_ns = {"j": NS}

PREFIXES = [
    ("CurteaMilitaradeApel", "Curtea Militară de Apel"),
    ("CurteadeApel", "Curtea de Apel"),
    ("TribunalulMilitarTeritorial", "Tribunalul Militar Teritorial"),
    ("TribunalulMilitar", "Tribunalul Militar"),
    ("TribunalulComercial", "Tribunalul Comercial"),
    ("TribunalulpentruminoriSifamilie", "Tribunalul pentru minori și familie"),
    ("Tribunalul", "Tribunalul"),
    ("Judecatoria", "Judecătoria"),
]


# the API glues multi-word place names together, these can't be split by rule
PLACES = {
    "ALBAIULIA": "Alba Iulia", "BAIADEARAMA": "Baia de Arama", "BAIAMARE": "Baia Mare",
    "BISTRITANASAUD": "Bistrita-Nasaud", "BOLINTINVALE": "Bolintin-Vale",
    "CAMPULUNGMOLDOVENESC": "Campulung Moldovenesc", "CARASSEVERIN": "Caras-Severin",
    "CHISINEUCRIS": "Chisineu-Cris", "CLUJNAPOCA": "Cluj-Napoca", "CURTEADEARGES": "Curtea de Arges",
    "DROBETATURNUSEVERIN": "Drobeta-Turnu Severin", "GURAHUMORULUI": "Gura Humorului",
    "INTORSURABUZAULUI": "Intorsura Buzaului", "LEHLIUGARA": "Lehliu-Gara",
    "MIERCUREACIUC": "Miercurea Ciuc", "MOLDOVANOUA": "Moldova Noua", "NEGRESTIOAS": "Negresti-Oas",
    "ODORHEIULSECUIESC": "Odorheiu Secuiesc", "PIATRANEAMT": "Piatra Neamt",
    "PODUTURCULUI": "Podu Turcului", "RAMNICUSARAT": "Ramnicu Sarat", "RAMNICUVALCEA": "Ramnicu Valcea",
    "ROSIORIDEVEDE": "Rosiori de Vede", "SANNICOLAULMARE": "Sannicolau Mare", "SATUMARE": "Satu Mare",
    "SFANTUGHEORGHE": "Sfantu Gheorghe", "SIGHETUMARMATIEI": "Sighetu Marmatiei",
    "SIMLEULSILVANIEI": "Simleu Silvaniei", "SOMCUTAMARE": "Somcuta Mare", "TARGUBUJOR": "Targu Bujor",
    "TARGUCARBUNESTI": "Targu Carbunesti", "TARGUJIU": "Targu Jiu", "TARGULAPUS": "Targu Lapus",
    "TARGUMURES": "Targu Mures", "TARGUNEAMT": "Targu Neamt", "TARGUSECUIESC": "Targu Secuiesc",
    "TURNUMAGURELE": "Turnu Magurele", "VALENIIDEMUNTE": "Valenii de Munte", "VANJUMARE": "Vanju Mare",
    "VATRADORNEI": "Vatra Dornei", "VISEUDESUS": "Viseu de Sus",
}


def court_name(code):
    """'JudecatoriaSECTORUL4BUCURESTI' -> 'Judecătoria Sectorul 4 Bucuresti'"""
    if not code:
        return code
    for prefix, nice in PREFIXES:
        if code.startswith(prefix):
            rest = code[len(prefix):]
            if rest in PLACES:
                return f"{nice} {PLACES[rest]}"
            rest = re.sub(r"(\d+)", r" \1 ", rest).split()
            return " ".join([nice, *(w.title() for w in rest)]).strip()
    return code


def match_court(value):
    """Free text ('Tribunalul Cluj', 'judecatoria sector 4') -> the API's court code."""
    want = words(value)
    if not want:
        raise ValueError("empty court name")
    joined = "".join(want)
    for code in COURTS:
        if fold(code) == joined:
            return code
    hits = [c for c in COURTS if all(w in words(court_name(c)) or w in fold(c) for w in want)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ValueError(f"no court matches {value!r}")
    names = ", ".join(court_name(c) for c in hits[:12])
    more = f" and {len(hits) - 12} more" if len(hits) > 12 else ""
    raise ValueError(f"{value!r} matches several courts: {names}{more}. be more specific")


def build_query(party=None, case_number=None, court=None, since=None):
    def field(name, value):
        if value is None:
            return f'<{name} xsi:nil="true"/>'
        return f"<{name}>{escape(str(value))}</{name}>"

    body = "".join([
        f"<numarDosar>{escape(case_number)}</numarDosar>" if case_number else "",
        f"<numeParte>{escape(party)}</numeParte>" if party else "",
        field("institutie", court),
        field("dataStart", f"{since}T00:00:00" if since else None),
        field("dataStop", None),
    ])
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<soap:Body><CautareDosare xmlns="{NS}">{body}</CautareDosare></soap:Body>'
        "</soap:Envelope>"
    )


def _text(el, path):
    value = el.findtext(path, namespaces=_ns)
    return value.strip() if value and value.strip() else None


def _day(value):
    return value[:10] if value else None


def _valid_ref(m):
    code = int(m.group(1)[1:], 16) if m.group(1)[0] in "xX" else int(m.group(1))
    return m.group(0) if code in (9, 10, 13) or code >= 32 else ""


def clean_xml(text):
    # case texts copied from Word sometimes carry control characters, which the portal
    # sends as &#x1; style references that XML 1.0 doesn't allow
    text = re.sub(r"&#([xX][0-9a-fA-F]+|\d+);", _valid_ref, text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def parse_cases(xml_text):
    try:
        root = ET.fromstring(clean_xml(xml_text))
    except ET.ParseError as e:
        raise ValueError(f"couldn't read Portal Just's answer ({e})")
    cases = []
    for d in root.iter(f"{{{NS}}}Dosar"):
        hearings = [
            {
                "date": _day(_text(s, "j:data")),
                "time": _text(s, "j:ora"),
                "panel": _text(s, "j:complet"),
                "outcome": _text(s, "j:solutie"),
                "summary": _text(s, "j:solutieSumar"),
            }
            for s in d.findall("j:sedinte/j:DosarSedinta", _ns)
        ]
        hearings.sort(key=lambda h: h["date"] or "", reverse=True)
        cases.append({
            "number": _text(d, "j:numar"),
            "court": court_name(_text(d, "j:institutie")),
            "department": _text(d, "j:departament"),
            "category": _text(d, "j:categorieCazNume"),
            "stage": _text(d, "j:stadiuProcesualNume"),
            "subject": _text(d, "j:obiect"),
            "registered": _day(_text(d, "j:data")),
            "last_modified": _day(_text(d, "j:dataModificare")),
            "parties": [
                {"name": _text(p, "j:nume"), "role": _text(p, "j:calitateParte")}
                for p in d.findall("j:parti/j:DosarParte", _ns)
            ],
            "hearings": hearings,
            "appeals": [
                {"by": (_text(a, "j:parteDeclaratoare") or "").strip(" ,") or None,
                 "type": _text(a, "j:tipCaleAtac"),
                 "date": _day(_text(a, "j:dataDeclarare"))}
                for a in d.findall("j:caiAtac/j:DosarCaleAtac", _ns)
            ],
        })
    return cases


LEGAL_FORMS = {"srl", "sa", "srld", "snc", "scs", "sca", "pfa", "ii", "if"}


def name_parts(name):
    """'S.C. FOO TRADE S.R.L.' -> ({'foo', 'trade'}, 'srl'). Single letters are dropped
    so 'S. R. L.' style leftovers don't get in the way."""
    ws = words((name or "").replace(".", ""))
    form = next((w for w in reversed(ws) if w in LEGAL_FORMS), None)
    core = {w for w in ws if w not in LEGAL_FORMS and w != "sc" and len(w) > 1}
    return core, form


def same_party(a, b):
    """Same name once SC / legal form / punctuation / diacritics are ignored. Portal Just
    has no CUI, so 'FINE LIBRIS SRL' or 'LIBRIS SA' must not count as 'LIBRIS SRL'."""
    core_a, form_a = name_parts(a)
    core_b, form_b = name_parts(b)
    if not core_a or core_a != core_b:
        return False
    return form_a is None or form_b is None or form_a == form_b


def search_name(name):
    """Drop 'SC' and the legal form so 'SC FOO SRL' also finds 'S.C. FOO S.R.L.' and 'FOO SRL'."""
    s = re.sub(r"^\s*s\.?\s?c\.?\s+", "", name, flags=re.I)
    s = re.sub(
        r"[\s,]+(s\.?\s?r\.?\s?l\.?(-?\s?d\.?)?|s\.?\s?a\.?|s\.?\s?n\.?\s?c\.?|p\.?\s?f\.?\s?a\.?)\s*$",
        "", s, flags=re.I,
    )
    return s.strip() or name.strip()


def party_roles(case, party):
    """Roles the searched party has in a case ('Pârât', 'Debitor', ...)."""
    return sorted({p["role"] for p in case["parties"] if p["role"] and same_party(p["name"], party)})


def is_insolvency(case):
    return fold(case["category"] or "").startswith(("faliment", "insolvent"))


def in_county(case, county):
    """Is the case's court in this county? ('BRAŞOV', 'MUNICIPIUL BUCUREŞTI', ...)"""
    want = [w for w in words(county) if w != "municipiul"]
    return bool(want) and all(w in words(case["court"]) for w in want)


def summarize(case, party=None, today=None):
    """Trim a case down to what's worth reading: latest outcome and the next hearing."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    past = [h for h in case["hearings"] if h["date"] and h["date"] <= today and (h["outcome"] or h["summary"])]
    upcoming = [h for h in case["hearings"] if h["date"] and h["date"] > today]
    out = {k: case[k] for k in ("number", "court", "category", "stage", "subject", "registered", "last_modified")}
    if party:
        out["party_role"] = party_roles(case, party)
    out["parties"] = case["parties"][:10]
    if len(case["parties"]) > 10:
        out["parties_total"] = len(case["parties"])
    out["latest_outcome"] = past[0] if past else None
    out["next_hearing"] = min(upcoming, key=lambda h: h["date"]) if upcoming else None
    if case["appeals"]:
        out["appeals"] = case["appeals"]
    return out


async def search(client, party=None, case_number=None, court=None, since=None):
    if party:
        party = search_name(party)
    key = f"just:{party}|{case_number}|{court}|{since}"

    async def fetch():
        r = await client.post(
            URL,
            content=build_query(party, case_number, court, since).encode(),
            headers={"Content-Type": "text/xml; charset=utf-8",
                     "SOAPAction": f'"{NS}/CautareDosare"'},
            timeout=60.0,
        )
        r.raise_for_status()
        return parse_cases(r.text)

    # cases move slowly, but a hearing can get an outcome any day
    return await cached(key, 3 * 3600, fetch)


def report(cases, party=None, limit=20, county=None):
    """Counts plus the most recently updated cases. With a party, only cases where a party
    has exactly that name count for roles. With the company's county too, insolvency only
    counts at courts in that county, since that's where a company's insolvency is filed."""
    cases = sorted(cases, key=lambda c: c["last_modified"] or "", reverse=True)
    out = {
        "total": len(cases),
        "by_category": dict(Counter(c["category"] for c in cases).most_common()),
    }
    if party:
        out["by_role"] = dict(Counter(r for c in cases for r in party_roles(c, party)).most_common(12))
        debtor = list(dict.fromkeys(
            c["number"] for c in cases
            if is_insolvency(c)
            and any("debitor" in fold(r) for r in party_roles(c, party))
            and (county is None or in_county(c, county))
        ))
        if debtor:
            out["insolvency_as_debtor"] = debtor
            if county is None:
                out["insolvency_note"] = ("matched on the name only, another company can have the same "
                                          "name. check the court is in the county where the company is registered")
    if len(cases) >= 1000:
        out["note"] = "Portal Just returns at most 1000 cases, narrow it down with court or since"
    out["cases"] = [summarize(c, party) for c in cases[:limit]]
    if len(cases) > limit:
        out["showing"] = limit
    return out


async def company_cases(client, name, county=None, limit=20, court=None, since=None):
    """Cases of one company (exact name from ANAF): searched by name, then only those where
    a party has exactly that name, with insolvency checked against the county of its seat."""
    cases = await search(client, party=name, court=court, since=since)
    cases = [c for c in cases if party_roles(c, name)]
    return report(cases, party=name, limit=limit, county=county)
