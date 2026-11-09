"""Bank-detail validation: IBAN mod-97, IFSC, ABA routing checksum, UK sort code,
SWIFT/BIC. Format/checksum only; no bank API is called."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .taxid import Check

IBAN_LENGTHS = {
    "DE": 22, "GB": 22, "FR": 27, "NL": 18, "ES": 24, "IT": 27, "IE": 22, "BE": 16,
    "AT": 20, "CH": 21, "AE": 23, "PL": 28, "SE": 24, "DK": 18, "NO": 15, "FI": 18,
    "PT": 25, "LU": 20,
}

IFSC_BANKS = {
    "HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "SBIN": "State Bank of India",
    "UTIB": "Axis Bank", "KKBK": "Kotak Mahindra Bank", "PUNB": "Punjab National Bank",
    "BARB": "Bank of Baroda", "YESB": "Yes Bank", "INDB": "IndusInd Bank",
    "IDFB": "IDFC First Bank", "CNRB": "Canara Bank", "UBIN": "Union Bank of India",
}


def _clean(raw: str) -> str:
    return re.sub(r"[\s\-]", "", (raw or "")).upper()


def validate_iban(raw: str) -> Check:
    iban = _clean(raw)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", iban):
        return Check(False, "format", "IBAN must start with a 2-letter country code and 2 check digits.", iban)
    cc = iban[:2]
    want = IBAN_LENGTHS.get(cc)
    if want and len(iban) != want:
        return Check(False, "format", f"A {cc} IBAN has {want} characters; this one has {len(iban)}.", iban)
    rearranged = iban[4:] + iban[:4]
    num = "".join(str(int(ch, 36)) for ch in rearranged)
    if int(num) % 97 != 1:
        return Check(False, "checksum", "IBAN check digits are wrong, so there is probably a typo.", iban)
    return Check(True, "", "IBAN is valid.", iban, {"country": cc})


def validate_ifsc(raw: str) -> Check:
    c = _clean(raw)
    if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", c):
        return Check(False, "format", "IFSC is 11 characters: 4 letters, a zero, then 6 characters.", c)
    return Check(True, "", "IFSC format is valid.", c, {"country": "IN", "bank": IFSC_BANKS.get(c[:4])})


def validate_aba(raw: str) -> Check:
    d = re.sub(r"\D", "", raw or "")
    if len(d) != 9:
        return Check(False, "format", "US routing numbers have 9 digits.", d)
    n = [int(x) for x in d]
    total = 3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5] + n[8])
    if total % 10 != 0:
        return Check(False, "checksum", "Routing number check digit is wrong, so there is probably a typo.", d)
    return Check(True, "", "Routing number is valid.", d, {"country": "US"})


def validate_sort_code(raw: str) -> Check:
    d = re.sub(r"\D", "", raw or "")
    if len(d) != 6:
        return Check(False, "format", "UK sort codes have 6 digits (NN-NN-NN).", d)
    return Check(True, "", "Sort code format is valid.", d, {"country": "GB"})


def validate_account_number(country: str, raw: str) -> Check:
    d = re.sub(r"\D", "", raw or "")
    rules = {"IN": (9, 18), "GB": (8, 8), "US": (4, 17)}
    if country not in rules:
        return Check(bool(d), "" if d else "format", "", d)
    lo, hi = rules[country]
    if not (lo <= len(d) <= hi):
        span = f"{lo}" if lo == hi else f"{lo}-{hi}"
        return Check(False, "format", f"A {country} account number has {span} digits.", d)
    return Check(True, "", "Account number format is valid.", d)


def validate_swift(raw: str) -> Check:
    s = _clean(raw)
    if not re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", s):
        return Check(False, "format", "SWIFT/BIC is 8 or 11 characters.", s)
    return Check(True, "", "SWIFT/BIC format is valid.", s, {"country": s[4:6]})


def ifsc_bank_matches_name(ifsc: str, bank_name: str) -> bool | None:
    """True/False if we can tell, None if the IFSC bank code is not in our table."""
    bank = IFSC_BANKS.get(_clean(ifsc)[:4])
    if not bank or not bank_name:
        return None
    a = re.sub(r"[^a-z0-9 ]", "", bank.lower())
    b = re.sub(r"[^a-z0-9 ]", "", bank_name.lower())
    return fuzz.partial_ratio(a, b) >= 80


def normalize_account_key(country: str, form: dict) -> str:
    """A canonical string identifying a bank account, used to detect the same
    account appearing under two different vendors."""
    iban = _clean(form.get("iban", ""))
    if iban:
        return f"IBAN:{iban}"
    acct = re.sub(r"\D", "", form.get("account_number", "") or "")
    if not acct:
        return ""
    if country == "IN":
        return f"IN:{_clean(form.get('ifsc', ''))}:{acct}"
    if country == "GB":
        return f"GB:{re.sub(r'[^0-9]', '', form.get('sort_code', '') or '')}:{acct}"
    if country == "US":
        return f"US:{re.sub(r'[^0-9]', '', form.get('routing_number', '') or '')}:{acct}"
    return f"{country}:{acct}"
