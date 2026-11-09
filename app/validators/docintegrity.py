"""Document-integrity checks that do not depend on the AI.

A common payment-fraud trick is to take a genuine bank letter and paste a new account
number over the old one (white box + new text) in a PDF editor. The page LOOKS perfect,
and an AI reading the image would happily report the new number. But the PDF still
contains the original text underneath, and the edit is saved as an incremental update.
We read the raw text layer and the file structure to catch that."""
from __future__ import annotations

import re
from pathlib import Path

from ..llm import _LABELS

WATCH = {"account_number", "iban", "tax_id", "legal_name", "account_holder", "ifsc", "routing_number", "sort_code"}


def _norm(v: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (v or "").upper())


def analyze(path: str | Path) -> dict:
    p = Path(path)
    out = {"checked": False, "incremental_updates": 0, "conflicts": {}, "producer": ""}
    if p.suffix.lower() != ".pdf" or not p.exists():
        return out
    raw = p.read_bytes()
    out["checked"] = True
    out["incremental_updates"] = max(0, raw.count(b"%%EOF") - 1)
    try:
        from pypdf import PdfReader
        r = PdfReader(str(p))
        meta = r.metadata or {}
        out["producer"] = str(meta.get("/Producer", "") or "")[:80]
        text = "\n".join((pg.extract_text() or "") for pg in r.pages)
    except Exception:
        return out
    seen: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        key = _LABELS.get(label.strip().lower())
        value = value.strip()
        if key in WATCH and value:
            seen.setdefault(key, {})
            seen[key].setdefault(_norm(value), value)
    out["conflicts"] = {k: list(v.values()) for k, v in seen.items() if len(v) > 1}
    return out
