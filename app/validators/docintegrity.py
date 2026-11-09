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
    over = overlaid_text(r)
    if over:
        out["overlaid"] = over
        # the edit replaced only the VALUE (no second label): name the field from the label next to it
        for o in over:
            key = o.get("field") or "overlaid_value"
            if key not in out["conflicts"]:
                out["conflicts"][key] = [o["under"], o["over"]]
    return out


def overlaid_text(reader) -> list[dict]:
    """Two DIFFERENT strings drawn starting at the same point of a page: what remains when someone
    covers a value with a white box and types a new one at the same spot (even without retyping
    the label). Genuine documents do not print two texts on top of each other."""
    found = []
    for pno, page in enumerate(reader.pages):
        runs = []

        def visit(text, cm, tm, font_dict, font_size):
            t = (text or "").strip()
            if len(_norm(t)) >= 4:
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                runs.append((round(x, 1), round(y, 1), t))
        try:
            page.extract_text(visitor_text=visit)
        except Exception:
            continue
        for i, (x1, y1, t1) in enumerate(runs):
            for x2, y2, t2 in runs[i + 1:]:
                if abs(x1 - x2) <= 1.5 and abs(y1 - y2) <= 1.5 and _norm(t1) != _norm(t2):
                    label = _label_left_of(runs, x1, y1)
                    found.append({"page": pno, "under": t1, "over": t2, "field": _LABELS.get(label) if label else None})
    return found


def _label_left_of(runs, x, y):
    left = [(x - rx, t) for rx, ry, t in runs if abs(ry - y) <= 1.5 and rx < x - 1]
    if not left:
        return None
    t = min(left)[1]
    return t.rstrip(": ").strip().lower() if t.rstrip().endswith(":") else None
