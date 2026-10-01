import pytest

from romania_mcp import iban


def make_ro(bank, account):
    """Build a valid Romanian IBAN for tests."""
    bban = bank + account
    digits = "".join(str(int(c, 36)) for c in bban + "RO00")
    return f"RO{98 - int(digits) % 97:02d}{bban}"


def test_valid_romanian_iban_names_the_bank():
    value = make_ro("BTRL", "0130120212345678")
    res = iban.check(" ".join(value[i:i + 4] for i in range(0, 24, 4)))
    assert res["valid"] is True
    assert res["iban"] == value
    assert res["bank"] == "Banca Transilvania"


def test_treasury_and_unknown_bank():
    assert "Treasury" in iban.check(make_ro("TREZ", "7005069XXX012345"))["note"]
    res = iban.check(make_ro("ZZZZ", "0000000000000001"))
    assert res["valid"] is True
    assert res["bank"] is None
    assert "isn't in the built-in list" in res["note"]


def test_foreign_iban():
    res = iban.check("DE89 3704 0044 0532 0130 00")
    assert res["valid"] is True
    assert res["country"] == "DE"
    assert "bank" not in res


@pytest.mark.parametrize("value, error", [
    ("DE89 3704 0044 0532 0130 01", "typo"),
    ("RO49AAAA1B31007593840000X", "24 characters"),
    ("hello", "doesn't look like"),
])
def test_invalid(value, error):
    res = iban.check(value)
    assert res["valid"] is False
    assert error in res["error"]
