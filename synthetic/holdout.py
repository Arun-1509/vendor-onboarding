"""HOLDOUT benchmark: 37 cases written by ChatGPT (a different AI company) from a prompt that describes
the process but not the rules or code (bench/holdout/CHATGPT_PROMPT.md). Its answer is kept verbatim in
bench/holdout/chatgpt_raw.json. The only thing done here is mechanical: replacing {{TAX_ID}}-style
placeholders with checksum-valid numbers (a language model cannot compute GSTIN / IBAN / ABA check
digits), choosing bank codes that belong to the bank named, and mapping two document-kind spellings.
No expected outcome is changed.

    python -m synthetic.holdout      -> bench/holdout/spec.json + cases.json + docs/
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from app.validators import bank as bk
from . import blind, ids
from .generator import ROOT

OUT = ROOT / "bench" / "holdout"
IN_STATE = {"mumbai": "27", "pune": "27", "vadodara": "24", "mysuru": "29", "bengaluru": "29", "bhopal": "23",
            "chennai": "33", "hyderabad": "36"}
MASTER = [v["form"] for v in json.loads((ROOT / "data" / "seed_vendors.json").read_text(encoding="utf-8"))["vendors"]]
KIND = {"ust_certificate": "vat_certificate"}


def _pick(table: dict, bank_name: str):
    return next((k for k, v in table.items() if bk.bank_names_match(v if isinstance(v, str) else v[0], bank_name)), None)


def _values(c: dict, rng: random.Random) -> dict:
    f, cc = c["form"], c["form"]["country"]
    name, et = f["legal_name"], f["entity_type"]
    if cc == "IN":
        mk_tax = lambda: ids.gstin(IN_STATE.get(f["city"].lower(), "27"), ids.pan(rng, ids.PAN_FOR_ENTITY.get(et, "C"), name[0]))  # noqa: E731
        code = _pick(ids.IN_BANKS, f["bank_name"]) or "HDFC"
        acct, other, bcode = ids.in_account(rng), ids.in_account(rng), ids.ifsc(rng, code)
    elif cc == "DE":
        mk_tax = lambda: ids.de_vat(rng)  # noqa: E731
        blz = _pick(ids.DE_BANKS, f["bank_name"]) or "50070010"
        acct, other, bcode = ids.de_iban(rng, blz), ids.de_iban(rng, blz), ""
    elif cc == "GB":
        mk_tax = lambda: ids.gb_vat(rng)  # noqa: E731
        pre = _pick(ids.GB_BANKS, f["bank_name"]) or "20"
        acct, other, bcode = ids.gb_account(rng), ids.gb_account(rng), ids.sort_code(rng, pre)
    else:
        mk_tax = lambda: ids.us_ein(rng)  # noqa: E731
        acct, other, bcode = ids.us_account(rng), ids.us_account(rng), ids.aba(rng)
    # ChatGPT was told to copy an existing vendor's account number literally, but it was never given that
    # vendor's IFSC / sort code / routing number, so take the bank code from the master record.
    for m in MASTER:
        if any(f.get(k) and f.get(k) == m.get(k) for k in ("account_number", "iban")):
            bcode = m.get("ifsc") or m.get("sort_code") or m.get("routing_number") or bcode
    tax = mk_tax()
    typo_tax = ids.typo(rng, tax, digits_only=True)
    return {"TAX_ID": tax, "TAX_ID_TYPO": typo_tax, "TAX_ID_OTHER": mk_tax(), "ACCOUNT": acct,
            "ACCOUNT_TYPO": ids.typo(rng, acct, digits_only=True), "ACCOUNT_OTHER": other, "BANK_CODE": bcode}


def _fill(obj, vals: dict, field: str = "", cc: str = ""):
    if isinstance(obj, dict):
        return {k: _fill(v, vals, k, cc) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, vals, field, cc) for v in obj]
    if isinstance(obj, str) and "{{" in obj:
        if field == "iban" and cc != "DE":          # a non-German IBAN field (H31: a euro account abroad)
            vals.setdefault("_FR_IBAN", ids.iban("FR", "30006000011234567890189"))
            return vals["_FR_IBAN"]
        return re.sub(r"\{\{(\w+)\}\}", lambda m: vals[m.group(1)], obj)
    return obj


def build(seed: int = 37) -> Path:
    raw = json.loads((OUT / "chatgpt_raw.json").read_text(encoding="utf-8"))
    rng = random.Random(seed)
    cases = []
    for c in raw["cases"]:
        vals = _values(c, rng)
        c = _fill(c, vals, cc=c["form"]["country"])
        for d in (c.get("documents") or {}).values():
            if d:
                d["kind"] = KIND.get(d["kind"], d["kind"])
                d["printed"] = {k: v for k, v in d["printed"].items() if v}
        cases.append(c)
    spec = {"author_note": "Written by ChatGPT from bench/holdout/CHATGPT_PROMPT.md, without access to the code or rules. "
                           "Expected outcomes are ChatGPT's, unchanged.", "cases": cases}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "spec.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    return blind.build(OUT / "spec.json", out=OUT)


if __name__ == "__main__":
    print(build())
