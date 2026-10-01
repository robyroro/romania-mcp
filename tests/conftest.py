import httpx
import pytest

from romania_mcp import anaf, cache


@pytest.fixture(autouse=True)
def no_cache_no_throttle(monkeypatch):
    # every test starts with the cache off and no 1s ANAF spacing
    cache.set_cache(cache.Cache(None))
    monkeypatch.setattr(anaf, "MIN_INTERVAL", 0)
    monkeypatch.setattr(anaf, "_next_slot", 0.0)
    yield
    cache.set_cache(None)


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def anaf_item(cui=14399840, name="DANTE INTERNATIONAL SA", status="INREGISTRAT din data 29.08.2006",
              vat=True, inactive=False, registered="2002-01-23"):
    return {
        "date_generale": {
            "data": "2026-10-01", "cui": cui, "denumire": name,
            "adresa": "MUNICIPIUL BUCUREŞTI, SECTOR 2, STR. GARA HERĂSTRĂU, NR.6",
            "telefon": "", "stare_inregistrare": status, "statusRO_e_Factura": False,
            "nrRegCom": "J2002000372404", "cod_CAEN": "4754", "data_inregistrare": registered,
            "organFiscalCompetent": "DGAMC", "forma_juridica": "SOCIETATE COMERCIALĂ PE ACŢIUNI",
        },
        "inregistrare_scop_Tva": {
            "scpTVA": vat,
            "perioade_TVA": [{"data_inceput_ScpTVA": "2002-02-01", "data_sfarsit_ScpTVA": "",
                              "data_anul_imp_ScpTVA": "", "mesaj_ScpTVA": ""}],
        },
        "inregistrare_RTVAI": {"statusTvaIncasare": False},
        "stare_inactiv": {"dataInactivare": "2024-05-01" if inactive else "", "dataReactivare": "",
                          "dataPublicare": "", "dataRadiere": "", "statusInactivi": inactive},
        "inregistrare_SplitTVA": {"statusSplitTVA": False},
        "adresa_sediu_social": {"sdenumire_Judet": "MUNICIPIUL BUCUREŞTI"},
    }


def bilant_body(year=2024, cui=14399840, revenue=8992961799, employees=3191, equity=2390301600):
    labels = [
        ("I7", 2316131718, "DATORII"),
        ("I10", equity, "CAPITALURI - TOTAL, din care:"),
        ("I13", revenue, "Cifra de afaceri neta"),
        ("I14", 9068506360, "VENITURI TOTALE"),
        ("I18", 0, "Profit net"),
        ("I19", 191463916, "Pierdere neta"),
        ("I20", employees, "Numar mediu de salariati"),
        ("I1", 2770685187, "ACTIVE IMOBILIZATE - TOTAL "),
        ("I5", 291756487, "Casa şi conturi la bănci"),
    ]
    return {
        "an": year, "cui": cui, "deni": "Dante International SA", "caen": 4754,
        "den_caen": "Comert cu amanuntul",
        "i": [{"indicator": i, "val_indicator": v, "val_den_indicator": n} for i, v, n in labels],
    }
