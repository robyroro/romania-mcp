import asyncio

import httpx
import pytest

from romania_mcp import just

from conftest import mock_client

RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<soap:Body><CautareDosareResponse xmlns="portalquery.just.ro"><CautareDosareResult>
<Dosar>
  <parti>
    <DosarParte><nume>FOO TRADE S.R.L.</nume><calitateParte>Debitor</calitateParte></DosarParte>
    <DosarParte><nume>ANAF</nume><calitateParte>Creditor</calitateParte></DosarParte>
  </parti>
  <sedinte>
    <DosarSedinta><complet>C1</complet><data>2026-11-20T00:00:00</data><ora>9:00</ora><solutie /><solutieSumar /></DosarSedinta>
    <DosarSedinta><complet>C1</complet><data>2026-09-01T00:00:00</data><ora>9:00</ora><solutie>Amână cauza</solutie><solutieSumar>pentru lipsa de procedura</solutieSumar></DosarSedinta>
  </sedinte>
  <caiAtac />
  <numar>123/1285/2026</numar>
  <data>2026-03-02T00:00:00</data>
  <institutie>TribunalulCLUJ</institutie>
  <departament>Sectia a II-a civila</departament>
  <obiect>procedura insolventei</obiect>
  <dataModificare>2026-09-01T10:00:00</dataModificare>
  <categorieCazNume>Faliment</categorieCazNume>
  <stadiuProcesualNume>Fond</stadiuProcesualNume>
</Dosar>
<Dosar>
  <parti>
    <DosarParte><nume>SC FOO TRADE SRL</nume><calitateParte>Pârât</calitateParte></DosarParte>
    <DosarParte><nume>POPESCU ION</nume><calitateParte>Reclamant</calitateParte></DosarParte>
  </parti>
  <sedinte />
  <caiAtac><DosarCaleAtac><dataDeclarare>2026-05-01T00:00:00</dataDeclarare><parteDeclaratoare> SC FOO TRADE SRL, </parteDeclaratoare><tipCaleAtac>Apel</tipCaleAtac></DosarCaleAtac></caiAtac>
  <numar>55/211/2025</numar>
  <data>2025-01-10T00:00:00</data>
  <institutie>JudecatoriaCLUJNAPOCA</institutie>
  <obiect>pretentii</obiect>
  <dataModificare>2026-05-02T10:00:00</dataModificare>
  <categorieCazNume>Civil</categorieCazNume>
  <stadiuProcesualNume>Apel</stadiuProcesualNume>
</Dosar>
</CautareDosareResult></CautareDosareResponse></soap:Body></soap:Envelope>"""


@pytest.mark.parametrize("code, name", [
    ("JudecatoriaSECTORUL4BUCURESTI", "Judecătoria Sectorul 4 Bucuresti"),
    ("JudecatoriaCLUJNAPOCA", "Judecătoria Cluj-Napoca"),
    ("CurteadeApelTARGUMURES", "Curtea de Apel Targu Mures"),
    ("TribunalulpentruminoriSifamilieBRASOV", "Tribunalul pentru minori și familie Brasov"),
])
def test_court_name(code, name):
    assert just.court_name(code) == name


@pytest.mark.parametrize("text, code", [
    ("Tribunalul Cluj", "TribunalulCLUJ"),
    ("judecatoria sector 4", "JudecatoriaSECTORUL4BUCURESTI"),
    ("Curtea de Apel Târgu Mureș", "CurteadeApelTARGUMURES"),
    ("Judecătoria Cluj-Napoca", "JudecatoriaCLUJNAPOCA"),
])
def test_match_court(text, code):
    assert just.match_court(text) == code


def test_match_court_ambiguous_or_unknown():
    with pytest.raises(ValueError, match="several courts"):
        just.match_court("cluj")
    with pytest.raises(ValueError, match="no court"):
        just.match_court("Tribunalul Atlantida")


@pytest.mark.parametrize("raw, clean", [
    ("SC Foo Trade SRL", "Foo Trade"),
    ("S.C. FOO TRADE S.R.L.", "FOO TRADE"),
    ("DANTE INTERNATIONAL SA", "DANTE INTERNATIONAL"),
    ("Popescu Ion", "Popescu Ion"),
])
def test_search_name(raw, clean):
    assert just.search_name(raw) == clean


def test_build_query_escapes_and_nils():
    q = just.build_query(party="A & B <x>", court="TribunalulCLUJ", since="2025-01-01")
    assert "<numeParte>A &amp; B &lt;x&gt;</numeParte>" in q
    assert "<institutie>TribunalulCLUJ</institutie>" in q
    assert "<dataStart>2025-01-01T00:00:00</dataStart>" in q
    assert '<dataStop xsi:nil="true"/>' in q
    assert "numarDosar" not in q


def test_parse_and_report():
    cases = just.parse_cases(RESPONSE)
    assert [c["number"] for c in cases] == ["123/1285/2026", "55/211/2025"]
    assert cases[0]["court"] == "Tribunalul Cluj"
    assert cases[1]["appeals"] == [{"by": "SC FOO TRADE SRL", "type": "Apel", "date": "2026-05-01"}]

    rep = just.report(cases, party="Foo Trade", limit=1)
    assert rep["total"] == 2
    assert rep["by_category"] == {"Faliment": 1, "Civil": 1}
    assert rep["by_role"] == {"Debitor": 1, "Pârât": 1}
    assert rep["insolvency_as_debtor"] == ["123/1285/2026"]
    assert rep["showing"] == 1
    first = rep["cases"][0]
    assert first["party_role"] == ["Debitor"]


def test_summarize_splits_past_and_next_hearing():
    case = just.parse_cases(RESPONSE)[0]
    s = just.summarize(case, today="2026-10-01")
    assert s["latest_outcome"]["date"] == "2026-09-01"
    assert s["latest_outcome"]["outcome"] == "Amână cauza"
    assert s["next_hearing"]["date"] == "2026-11-20"


def test_search_posts_soap():
    seen = {}

    def handler(request):
        seen["action"] = request.headers["SOAPAction"]
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=RESPONSE)

    async def go():
        async with mock_client(handler) as client:
            return await just.search(client, party="SC Foo Trade SRL")

    cases = asyncio.run(go())
    assert len(cases) == 2
    assert seen["action"] == '"portalquery.just.ro/CautareDosare"'
    assert "<numeParte>Foo Trade</numeParte>" in seen["body"]


def test_control_characters_in_answer_are_dropped():
    # seen live: case texts with &#x1; inside, which XML 1.0 rejects
    dirty = RESPONSE.replace("pentru lipsa de procedura", "pentru lipsa&#x1; de&#11; procedura&#xE9;\x02")
    cases = just.parse_cases(dirty)
    assert cases[0]["hearings"][1]["summary"] == "pentru lipsa de procedura\u00e9"


def test_unreadable_answer_is_a_value_error():
    with pytest.raises(ValueError, match="Portal Just"):
        just.parse_cases("<not xml")


@pytest.mark.parametrize("a, b, same", [
    ("S.C. DANTE INTERNAŢIONAL S.A.", "DANTE INTERNATIONAL SA", True),
    ("Dante International", "DANTE INTERNAŢIONAL SA", True),
    ("FOO TRADE S.R.L.", "SC FOO TRADE SRL", True),
    # seen live: these came up for LIBRIS SRL (Brasov) and are other companies
    ("FINE LIBRIS SRL", "LIBRIS SRL", False),
    ("SC LIBRIS SA", "LIBRIS SRL", False),
    ("", "LIBRIS SRL", False),
])
def test_same_party(a, b, same):
    assert just.same_party(a, b) is same


def test_in_county():
    case = {"court": "Tribunalul Brasov"}
    assert just.in_county(case, "BRAŞOV")
    assert not just.in_county(case, "MEHEDINŢI")
    assert just.in_county({"court": "Tribunalul Bucuresti"}, "MUNICIPIUL BUCUREŞTI")
    assert just.in_county({"court": "Tribunalul Satu Mare"}, "SATU MARE")


def test_report_with_county_ignores_insolvency_elsewhere():
    cases = just.parse_cases(RESPONSE)  # the insolvency is at Tribunalul Cluj
    assert "insolvency_as_debtor" not in just.report(cases, party="FOO TRADE SRL", county="BRAŞOV")
    rep = just.report(cases, party="FOO TRADE SRL", county="CLUJ")
    assert rep["insolvency_as_debtor"] == ["123/1285/2026"]
    assert "insolvency_note" not in rep
    assert "insolvency_note" in just.report(cases, party="FOO TRADE SRL")
