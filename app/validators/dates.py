"""Find the dates printed on a document, in the styles real documents use.

The AI is asked to return dates as YYYY-MM-DD, but documents print '09.11.2026', '9 November 2026',
'11/09/2026' or '09-Nov-2026'. Grounding must compare the DATE, not the characters, or every
non-ISO letter loses its date and the 90-day age check silently never runs."""
from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MONTHS.update({"mae": 3, "mrz": 3, "maerz": 3, "marz": 3, "mai": 5, "okt": 10, "dez": 12, "januar": 1, "februar": 2, "juni": 6, "juli": 7})
_MON = r"([A-Za-zä]{3,9})\.?"


def _mk(y, m, d):
    try:
        y = int(y)
        y = y + 2000 if y < 100 else y
        return date(y, int(m), int(d))
    except (ValueError, TypeError):
        return None


def _month(name: str):
    n = name.lower().replace("ä", "ae")
    return MONTHS.get(n) or MONTHS.get(n[:3])


def dates_in(text: str) -> set[date]:
    """Every date the text could mean. Ambiguous 05/10/2026 yields both 5 Oct and 10 May."""
    out: set[date] = set()
    t = text or ""
    for y, m, d in re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        out.add(_mk(y, m, d))
    for a, b, y in re.findall(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})\b", t):
        out.add(_mk(y, b, a))                                   # day-first (most of the world)
        out.add(_mk(y, a, b))                                   # month-first (US)
    for d, mon, y in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+" + _MON + r"[\s\-,]+(\d{4})\b", t):
        if _month(mon):
            out.add(_mk(y, _month(mon), d))
    for mon, d, y in re.findall(r"\b" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", t):
        if _month(mon):
            out.add(_mk(y, _month(mon), d))
    out.discard(None)
    return out


def parse_one(value: str) -> date | None:
    """A single date value in any of the styles above (ISO first). Ambiguous slash dates are
    read day-first unless that is impossible."""
    v = (value or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        return _mk(*m.groups())
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})$", v)
    if m:
        a, b, y = m.groups()
        return _mk(y, b, a) or _mk(y, a, b)
    ds = dates_in(v)
    return min(ds) if len(ds) == 1 else None


def grounded_date(value: str, text: str) -> bool:
    d = parse_one(value)
    return bool(d) and d in dates_in(text)
