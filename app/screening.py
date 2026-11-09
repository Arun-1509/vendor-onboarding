"""Risk screening against (a) a MOCK sanctions list and (b) the existing vendor master.
The sanctions entries are fictional demo data; a real deployment would use a maintained feed."""
from __future__ import annotations

import json

from . import config
from .validators import names as nm


def load_sanctions() -> list[dict]:
    return json.loads((config.DATA_DIR / "sanctions_mock.json").read_text())["entries"]


def sanctions_best_match(candidates: list[str], entries: list[dict] | None = None) -> dict | None:
    """Highest-scoring (candidate, list-name) pair, or None."""
    entries = entries if entries is not None else load_sanctions()
    best = None
    for cand in filter(None, candidates):
        for e in entries:
            for listed in [e["name"], *e.get("aliases", [])]:
                score = 1.0 if nm.core_name(cand) == nm.core_name(listed) else nm.core_similarity(cand, listed)
                if best is None or score > best["score"]:
                    best = {"score": round(score, 3), "candidate": cand, "listed_as": listed,
                            "entry_id": e["id"], "list_note": e.get("note", "")}
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
        if same_core or best >= config.LOOKALIKE_THRESHOLD:
            hits.append({"vendor_id": v["vendor_id"], "legal_name": v["legal_name"],
                         "status": v["status"], "similarity": 1.0 if same_core else round(best, 3)})
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
