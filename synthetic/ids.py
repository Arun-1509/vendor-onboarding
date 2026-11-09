"""Identifier factories for synthetic vendors. Every ID is format- and checksum-valid
unless a scenario deliberately corrupts it. Bank-code tables are small, public-style
demo tables (see ASSUMPTIONS.md); they are not a complete bank directory."""
from __future__ import annotations

import random
import string

from app.validators.taxid import US_EIN_PREFIXES, _de_vat_check_digit, gstin_check_char

# --------------------------------------------------------------- India ----
# city -> (PIN, GST state code)
IN_CITIES = {
    "Mumbai": ("400001", "27"), "Pune": ("411001", "27"), "Bengaluru": ("560001", "29"),
    "Chennai": ("600002", "33"), "Hyderabad": ("500001", "36"), "Ahmedabad": ("380001", "24"),
    "New Delhi": ("110001", "07"), "Kolkata": ("700001", "19"), "Jaipur": ("302001", "08"),
    "Kochi": ("682001", "32"), "Lucknow": ("226001", "09"), "Gurugram": ("122001", "06"),
}
IN_STREETS = ["Plot 14, MIDC Industrial Area", "3rd Floor, Prestige Towers, MG Road", "Survey No 41, Phase II Industrial Estate",
              "B-12, Okhla Industrial Area", "45 Anna Salai", "Unit 7, Electronic City Phase 1", "22 Park Street",
              "Shed 9, GIDC Estate", "88 Residency Road"]
IN_BANKS = {"HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "SBIN": "State Bank of India", "UTIB": "Axis Bank",
            "KKBK": "Kotak Mahindra Bank", "BARB": "Bank of Baroda", "YESB": "Yes Bank", "CNRB": "Canara Bank"}
PAN_FOR_ENTITY = {"pvt_ltd": "C", "ltd": "C", "llp": "F", "partnership": "F", "sole_prop": "P"}


def pan(rng: random.Random, entity_char: str, initial: str) -> str:
    return ("".join(rng.choice(string.ascii_uppercase) for _ in range(3)) + entity_char + initial.upper()
            + f"{rng.randint(0, 9999):04d}" + rng.choice(string.ascii_uppercase))


def gstin(state: str, pan_: str, ent: str = "1") -> str:
    s = f"{state}{pan_}{ent}Z"
    return s + gstin_check_char(s)


def ifsc(rng, code: str) -> str:
    return f"{code}0{rng.randint(0, 999999):06d}"


def in_account(rng) -> str:
    n = rng.choice([11, 12, 14, 14, 15])
    return str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(n - 1))


# -------------------------------------------------------------- Germany ----
DE_CITIES = {"Frankfurt": "60311", "Berlin": "10115", "Muenchen": "80331", "Hamburg": "20095", "Koeln": "50667", "Stuttgart": "70173"}
DE_STREETS = ["Industriestrasse 22", "Hafenweg 5", "Am Gewerbepark 14", "Kaiserstrasse 71", "Lindenallee 3", "Werkstrasse 18"]
# BLZ (German bank code, IBAN chars 5-12) -> bank
DE_BANKS = {"50070010": "Deutsche Bank", "10070000": "Deutsche Bank", "50040000": "Commerzbank",
            "70020270": "HypoVereinsbank", "20050550": "Hamburger Sparkasse", "50010517": "ING-DiBa",
            "37050198": "Sparkasse KoelnBonn"}


def de_vat(rng) -> str:
    n8 = str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(7))
    return "DE" + n8 + str(_de_vat_check_digit(n8))


def iban(cc: str, bban: str) -> str:
    num = "".join(str(int(c, 36)) for c in bban + cc + "00")
    return f"{cc}{98 - int(num) % 97:02d}{bban}"


def de_iban(rng, blz: str) -> str:
    return iban("DE", blz + f"{rng.randint(0, 9999999999):010d}")


# ------------------------------------------------------------------- UK ----
GB_CITIES = {"London": "EC1A 1BB", "Manchester": "M1 1AE", "Bristol": "BS1 4ST", "Leeds": "LS1 4AP", "Edinburgh": "EH1 1YZ", "Birmingham": "B2 4QA"}
GB_STREETS = ["12 Quay Street", "4 Canal Side", "Unit 3, Riverside Park", "77 King Street", "9 Station Approach", "21 Mill Lane"]
# sort-code first two digits -> bank ; and the 4-letter IBAN bank code
GB_BANKS = {"20": ("Barclays Bank", "BARC"), "30": ("Lloyds Bank", "LOYD"), "40": ("HSBC UK", "HBUK"),
            "60": ("NatWest", "NWBK"), "09": ("Santander UK", "ABBY"), "83": ("Royal Bank of Scotland", "RBOS")}


def gb_vat(rng) -> str:
    n7 = str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(6))
    w = [8, 7, 6, 5, 4, 3, 2]
    s = sum(int(x) * y for x, y in zip(n7, w))
    return f"GB{n7}{(-s) % 97:02d}"


def sort_code(rng, prefix: str) -> str:
    return f"{prefix}-{rng.randint(0, 99):02d}-{rng.randint(0, 99):02d}"


def gb_account(rng) -> str:
    return f"{rng.randint(10000000, 99999999)}"


def gb_iban(bankcode4: str, sort: str, acct: str) -> str:
    return iban("GB", bankcode4 + sort.replace("-", "") + acct)


# ------------------------------------------------------------------- US ----
US_CITIES = {"Austin, TX": "78701", "Chicago, IL": "60601", "Denver, CO": "80202", "Seattle, WA": "98101", "Atlanta, GA": "30303", "Columbus, OH": "43215"}
US_STREETS = ["500 Commerce Blvd", "1200 Market St, Suite 400", "77 Industrial Pkwy", "310 Lake Ave", "2100 Logistics Dr", "45 Main St, Floor 2"]
US_BANKS = ["JPMorgan Chase Bank", "Bank of America", "Wells Fargo Bank", "U.S. Bank", "PNC Bank", "Truist Bank"]
_EIN_PREFIXES = sorted(US_EIN_PREFIXES)


def us_ein(rng) -> str:
    return f"{rng.choice(_EIN_PREFIXES)}-{rng.randint(0, 9999999):07d}"


def aba(rng) -> str:
    n = [rng.randint(0, 9) for _ in range(8)]
    n[0] = rng.choice([0, 1, 2])
    partial = 3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5])
    return "".join(map(str, n)) + str((10 - partial % 10) % 10)


def us_account(rng) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(rng.choice([10, 11, 12])))


# ------------------------------------------------------------ corruption ----
def typo(rng, s: str, digits_only: bool = False) -> str:
    """Change one character so a checksum fails (retries until it really changed)."""
    idx = [i for i, c in enumerate(s) if c.isdigit()] if digits_only else [i for i, c in enumerate(s) if c.isalnum()]
    i = rng.choice(idx[2:] if len(idx) > 4 else idx)
    pool = string.digits if s[i].isdigit() else string.ascii_uppercase
    new = rng.choice([c for c in pool if c != s[i]])
    return s[:i] + new + s[i + 1:]


def transpose(rng, s: str) -> str:
    idx = [i for i in range(len(s) - 1) if s[i].isdigit() and s[i + 1].isdigit() and s[i] != s[i + 1]]
    if not idx:
        return typo(rng, s, digits_only=True)
    i = rng.choice(idx)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]


OCR_CONFUSE = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z"}


def ocr_noise(rng, s: str) -> str:
    """Simulate an OCR misread: one digit read as a look-alike letter."""
    idx = [i for i, c in enumerate(s) if c in OCR_CONFUSE]
    if not idx:
        return s
    i = rng.choice(idx)
    return s[:i] + OCR_CONFUSE[s[i]] + s[i + 1:]
