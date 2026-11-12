"""Tax-ID validation. FORMAT and CHECKSUM only: there is no registry lookup,
so a passing ID is *plausible*, not *verified*. (Listed in ASSUMPTIONS.md.)"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ALNUM36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

PAN_ENTITY = {
    "C": "company", "P": "individual", "F": "firm / LLP", "H": "HUF",
    "A": "association of persons", "T": "trust", "B": "body of individuals",
    "G": "government", "J": "artificial juridical person", "L": "local authority",
}
# declared legal form -> PAN 4th characters that are consistent with it
ENTITY_TO_PAN = {
    "pvt_ltd": {"C"}, "ltd": {"C"}, "llp": {"F"}, "partnership": {"F"},
    "sole_prop": {"P"}, "trust": {"T"},
}

GST_STATE_CODES = set(f"{i:02d}" for i in range(1, 39)) | {"97", "99"}

US_EIN_PREFIXES = set(
    "01 02 03 04 05 06 10 11 12 13 14 15 16 20 21 22 23 24 25 26 27 30 31 32 33 34 "
    "35 36 37 38 39 40 41 42 43 44 45 46 47 48 50 51 52 53 54 55 56 57 58 59 60 61 "
    "62 63 64 65 66 67 68 71 72 73 74 75 76 77 80 81 82 83 84 85 86 87 88 90 91 92 "
    "93 94 95 98 99".split()
)


@dataclass
class Check:
    ok: bool
    kind: str = ""          # "", "format" or "checksum"
    detail: str = ""
    normalized: str = ""
    info: dict = field(default_factory=dict)


def _clean(raw: str) -> str:
    return re.sub(r"[\s\-.]", "", (raw or "")).upper()


# ------------------------------------------------------------------ India --
def gstin_check_char(first14: str) -> str:
    """GSTIN checksum (base-36, alternating weights 1 and 2)."""
    total = 0
    for i, ch in enumerate(first14):
        val = _ALNUM36.index(ch)
        prod = val * (1 if i % 2 == 0 else 2)
        total += prod // 36 + prod % 36
    return _ALNUM36[(36 - total % 36) % 36]


def validate_gstin(raw: str) -> Check:
    g = _clean(raw)
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g):
        return Check(False, "format", "GSTIN must be 15 characters, e.g. 27AAACK1234F1Z5.", g)
    if g[:2] not in GST_STATE_CODES:
        return Check(False, "format", f"'{g[:2]}' is not a valid GST state code.", g)
    if gstin_check_char(g[:14]) != g[14]:
        return Check(False, "checksum", "GSTIN check character is wrong, so there is probably a typo.", g)
    pan = g[2:12]
    return Check(True, "", "GSTIN is valid.", g,
                 {"kind": "GSTIN", "pan": pan, "pan_entity": pan[3], "state_code": g[:2]})


def validate_pan(raw: str) -> Check:
    p = _clean(raw)
    if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", p) or p[3] not in PAN_ENTITY:
        return Check(False, "format", "PAN must look like AAACK1234F.", p)
    return Check(True, "", "PAN is valid.", p, {"kind": "PAN", "pan": p, "pan_entity": p[3]})


def validate_india(raw: str) -> Check:
    c = _clean(raw)
    if len(c) == 10:
        return validate_pan(c)
    return validate_gstin(c)


def pan_matches_declared_entity(entity_type: str, pan_char: str) -> bool | None:
    exp = ENTITY_TO_PAN.get(entity_type)
    return None if exp is None else pan_char in exp


# ---------------------------------------------------------------- Germany --
def _de_vat_check_digit(first8: str) -> int:
    """ISO 7064 MOD 11,10 as used for German USt-IdNr."""
    product = 10
    for d in first8:
        s = (int(d) + product) % 10
        if s == 0:
            s = 10
        product = (2 * s) % 11
    check = 11 - product
    return 0 if check == 10 else check


def validate_de_vat(raw: str) -> Check:
    v = _clean(raw)
    digits = v[2:] if v.startswith("DE") else v
    if not re.fullmatch(r"\d{9}", digits):
        return Check(False, "format", "German VAT ID is DE followed by 9 digits.", v)
    norm = "DE" + digits
    if _de_vat_check_digit(digits[:8]) != int(digits[8]):
        return Check(False, "checksum", "German VAT ID check digit is wrong, so there is probably a typo.", norm)
    return Check(True, "", "German VAT ID is valid.", norm, {"kind": "USt-IdNr"})


# --------------------------------------------------------------------- UK --
def validate_gb_vat(raw: str) -> Check:
    v = _clean(raw)
    digits = v[2:] if v.startswith("GB") else v
    if not re.fullmatch(r"\d{9}", digits):
        return Check(False, "format", "UK VAT number is GB followed by 9 digits.", v)
    norm = "GB" + digits
    weights = [8, 7, 6, 5, 4, 3, 2]
    s = sum(int(d) * w for d, w in zip(digits[:7], weights))
    cc = int(digits[7:9])
    if (s + cc) % 97 != 0 and (s + cc + 55) % 97 != 0:
        return Check(False, "checksum", "UK VAT number check digits are wrong, so there is probably a typo.", norm)
    return Check(True, "", "UK VAT number is valid.", norm, {"kind": "VAT"})


# --------------------------------------------------------------------- US --
def validate_us_ein(raw: str) -> Check:
    d = re.sub(r"\D", "", raw or "")
    if len(d) != 9:
        return Check(False, "format", "US EIN is 9 digits, written XX-XXXXXXX.", d)
    if re.fullmatch(r"\s*\d{3}-\d{2}-\d{4}\s*", raw or ""):
        # grouped 3-2-4 is how a personal Social Security number is written, not a company EIN
        return Check(False, "format", "This is written like a personal SSN (XXX-XX-XXXX); a company EIN is written XX-XXXXXXX.", d)
    norm = f"{d[:2]}-{d[2:]}"
    if d[:2] not in US_EIN_PREFIXES:
        return Check(False, "format", f"'{d[:2]}' is not an IRS-issued EIN prefix.", norm)
    return Check(True, "", "EIN format is valid.", norm, {"kind": "EIN"})


# --------------------------------------------------------------- dispatch --
def validate_tax_id(country: str, raw: str) -> Check:
    fn = {"IN": validate_india, "DE": validate_de_vat, "GB": validate_gb_vat, "US": validate_us_ein}.get(country)
    if not fn:
        return Check(False, "format", f"No tax-ID rules configured for country '{country}'.", _clean(raw))
    return fn(raw)


def detect_tax_id_country(raw: str) -> str | None:
    """Which country's tax-ID format does this string *look* like? Used to spot
    contradictions such as an Indian GSTIN on a form that claims Germany."""
    c = _clean(raw)
    if re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", c) or re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", c):
        return "IN"
    if re.fullmatch(r"DE\d{9}", c):
        return "DE"
    if re.fullmatch(r"GB\d{9}", c):
        return "GB"
    if re.fullmatch(r"\d{9}", re.sub(r"\D", "", raw or "")) and "-" in (raw or ""):
        return "US"
    return None


# ------------------------------------------------- India: cross-field checks --
# For a non-individual PAN, the 5th character is the first letter of the entity's name.
_PAN_NAME_RULE = set("CFATBLJG")


def pan_name_initial_ok(pan: str, legal_name: str) -> bool | None:
    """True/False if checkable, None otherwise (individual/HUF PANs use the surname)."""
    if not pan or len(pan) != 10 or pan[3] not in _PAN_NAME_RULE or not legal_name:
        return None
    from .names import split_name
    core = [t for t in split_name(re.sub(r"(?i)^m\s*/\s*s\.?\s+", "", legal_name))[0] if t not in ("the", "ms")]
    if not core or not core[0][0].isalpha():                    # '3S Engineering': no letter to compare with
        return None
    return core[0][0].upper() == pan[4]


# First two digits of an Indian PIN code -> GST state codes it can belong to.
# (Postal circles do not follow state lines exactly, so each prefix lists every state it touches.)
_PIN_STATES = {
    "11": {"07"}, "12": {"06"}, "13": {"06"}, "14": {"03"}, "15": {"03"}, "16": {"03", "04"}, "17": {"02"},
    "18": {"01", "38"}, "19": {"01", "38"}, "20": {"09"}, "21": {"09"}, "22": {"09"}, "23": {"09"},
    "24": {"09", "05"}, "25": {"09", "05"}, "26": {"09", "05"}, "27": {"09"}, "28": {"09"},
    "30": {"08"}, "31": {"08"}, "32": {"08"}, "33": {"08"}, "34": {"08"}, "36": {"24"}, "37": {"24"},
    "38": {"24"}, "39": {"24", "26", "25"}, "40": {"27", "30"}, "41": {"27"}, "42": {"27"}, "43": {"27"},
    "44": {"27"}, "45": {"23"}, "46": {"23"}, "47": {"23"}, "48": {"23"}, "49": {"22"},
    "50": {"36", "37"}, "51": {"37", "36"}, "52": {"37"}, "53": {"37"}, "56": {"29"}, "57": {"29"},
    "58": {"29"}, "59": {"29"}, "60": {"33", "34"}, "61": {"33"}, "62": {"33"}, "63": {"33"}, "64": {"33"},
    "67": {"32", "34"}, "68": {"32"}, "69": {"32"}, "70": {"19"}, "71": {"19"}, "72": {"19"}, "73": {"19", "11"},
    "74": {"19", "35"}, "75": {"21"}, "76": {"21"}, "77": {"21"}, "78": {"18"}, "79": {"12", "13", "14", "15", "16", "17"},
    "80": {"10"}, "81": {"10", "20"}, "82": {"10", "20"}, "83": {"20"}, "84": {"10"}, "85": {"10"},
}


def pin_from_address(*parts: str) -> str | None:
    found = re.findall(r"(?<!\d)([1-9]\d{2}\s?\d{3})(?!\d)", " ".join(p or "" for p in parts))
    return found[-1].replace(" ", "") if found else None


def pin_states(pin: str | None) -> set[str]:
    return _PIN_STATES.get((pin or "")[:2], set())
