"""IBAN validation (ISO 13616 mod-97) and which Romanian bank issued it. Fully offline."""

import re

# first 4 letters of the BIC = chars 5-8 of a Romanian IBAN. only codes we're sure of,
# anything else is reported as unknown rather than guessed
RO_BANKS = {
    "BACX": "UniCredit Bank",
    "BRDE": "BRD - Groupe Société Générale",
    "BTRL": "Banca Transilvania",
    "BUCU": "Alpha Bank România",
    "CECE": "CEC Bank",
    "CITI": "Citibank Europe (sucursala România)",
    "EGNA": "Vista Bank",
    "FNNB": "Credit Europe Bank",
    "INGB": "ING Bank",
    "NBOR": "Banca Națională a României",
    "OTPV": "OTP Bank România",
    "PIRB": "First Bank",
    "RNCB": "Banca Comercială Română (BCR)",
    "RZBR": "Raiffeisen Bank",
    "TREZ": "Trezoreria Statului",
    "UGBI": "Garanti BBVA",
    "WBAN": "Intesa Sanpaolo Bank",
}


def mod97_ok(iban):
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


def check(value):
    iban = re.sub(r"[\s\-]", "", value).upper()
    out = {"iban": iban, "formatted": " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))}

    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", iban):
        return {**out, "valid": False, "error": "doesn't look like an IBAN (2 letters, 2 digits, then up to 30 letters/digits)"}
    out["country"] = iban[:2]
    if iban.startswith("RO") and len(iban) != 24:
        return {**out, "valid": False, "error": f"Romanian IBANs have 24 characters, this has {len(iban)}"}
    if not mod97_ok(iban):
        return {**out, "valid": False, "error": "check digits don't match, there's a typo somewhere"}

    out["valid"] = True
    if iban.startswith("RO"):
        code = iban[4:8]
        out["bank_code"] = code
        out["bank"] = RO_BANKS.get(code)
        if code == "TREZ":
            out["note"] = "State Treasury account, used for taxes and payments to public institutions"
        elif out["bank"] is None:
            out["note"] = f"valid IBAN, bank code {code} isn't in the built-in list"
    return out
