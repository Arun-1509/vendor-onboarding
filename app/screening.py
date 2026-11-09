"""Risk screening against (a) a MOCK sanctions list and (b) the existing vendor master.
The sanctions entries are fictional demo data; a real deployment would use a maintained feed."""
from __future__ import annotations

import json

from . import config
from .validators import names as nm


def load_sanctions() -> list[dict]:
    return json.loads((config.DATA_DIR / "sanctions_mock.json").read_text())["entries"]


def _same_core(a: str, b: str) -> bool:
    """Exact after normalisation: case, accents, punctuation, legal form and word order ignored."""
    ca, cb = nm.split_name(a)[0], nm.split_name(b)[0]
    return bool(ca) and sorted(ca) == sorted(cb)


# industry words: a listed name minus these is its DISTINCTIVE core ('Meridian Crest' of 'Meridian Crest Shipping')
INDUSTRY_WORDS = {"shipping", "shipments", "logistics", "trading", "traders", "metals", "metal", "metallurgy", "group",
                  "holdings", "international", "global", "services", "industries", "exports", "imports", "marine", "maritime",
                  "freight", "energy", "resources", "capital", "ventures", "enterprises", "company"}


def distinctive_core(listed: str) -> list[str]:
    return [t for t in nm.split_name(listed)[0] if t not in INDUSTRY_WORDS]


def sanctions_best_match(candidates: list[str], entries: list[dict] | None = None) -> dict | None:
    """Highest-scoring (candidate, list-name) pair, or None.
    `exact` = same name once case, accents, punctuation, legal form and word order are ignored
    (or a listed alias). Only an exact match may auto-reject; a fuzzy match is a human's call,
    because a one-letter difference can be evasion OR a different, innocent company."""
    entries = entries if entries is not None else load_sanctions()
    best = None
    for cand in filter(None, candidates):
        for e in entries:
            for listed in [e["name"], *e.get("aliases", [])]:
                exact = _same_core(cand, listed)
                score = 1.0 if exact else max(nm.core_similarity(cand, listed), nm.aligned_similarity(cand, listed))
                core = distinctive_core(listed)
                shares = (not exact and len(core) >= 2 and all(t in nm.split_name(cand)[0] for t in core))
                if shares:                                      # whole distinctive name + another industry word: an affiliate?
                    score = max(score, config.SANCTIONS_REVIEW)
                if best is None or score > best["score"]:
                    best = {"score": round(score, 3), "exact": exact, "candidate": cand, "listed_as": listed,
                            "entry_id": e["id"], "list_note": e.get("note", ""),
                            **({"shares_distinctive_name": " ".join(core)} if shares else {})}
    return best


def other_vendors(conn, vendor_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE vendor_id != ?", (vendor_id,))]


def find_lookalikes(conn, vendor_id: str, names: list[str], tax_id: str) -> list[dict]:
    hits = []
    for v in other_vendors(conn, vendor_id):
        if v["tax_id"] and v["tax_id"] == tax_id:
            continue                      # same registration is handled by the duplicate rule
        best = max((nm.core_similarity(n, v["legal_name"]) for n in names if n), default=0.0)
        same_core = any(nm.core_name(n) == nm.core_name(v["legal_name"]) for n in names if n)
        padding = next((p for n in names if n and (p := nm.padded_variant(n, v["legal_name"]))), None)
        if same_core or padding or best >= config.LOOKALIKE_THRESHOLD:
            why = ("same core name" if same_core else f"same name plus the generic word(s) '{padding}'" if padding
                   else f"{best:.0%} similar spelling")
            hits.append({"vendor_id": v["vendor_id"], "legal_name": v["legal_name"], "status": v["status"],
                         "similarity": 1.0 if same_core else round(max(best, 0.9 if padding else 0), 3), "why": why})
    return hits


def find_shared_account(conn, vendor_id: str, account_key: str) -> list[dict]:
    if not account_key:
        return []
    return [{"vendor_id": v["vendor_id"], "legal_name": v["legal_name"], "status": v["status"]}
            for v in other_vendors(conn, vendor_id) if v["account_key"] == account_key]


def find_same_taxid(conn, vendor_id: str, country: str, tax_id: str) -> list[dict]:
    if not tax_id:
        return []
    return [{"vendor_id": v["vendor_id"], "legal_name": v["legal_name"], "status": v["status"]}
            for v in other_vendors(conn, vendor_id) if v["tax_id"] == tax_id and v["country"] == country]


def mask(s: str) -> str:
    s = str(s or "")
    return ("•" * max(0, len(s) - 4)) + s[-4:] if s else ""
