"""The 'diverse documents' benchmark: the same kinds of vendor decisions as the main generator,
but every document is drawn in a different shape (synthetic/layouts.py): tables, prose letters,
multi-page, stamps over text, German, country date formats, phone photos, value-only edits.

    python scripts/bench.py build-diverse          -> bench/diverse/

Labels come from the scenario (decided when the case is built), never from the pipeline.
"""
from __future__ import annotations

import json
import random
import shutil
from datetime import date
from pathlib import Path

from . import ids, layouts
from .generator import ROOT, SANCTIONED, Generator

PLAN = [  # scenario, count, expected substatus
    ("clean", 22, "approved"),
    ("stale_bank_letter", 6, "awaiting_vendor"),
    ("undated_bank_letter", 3, "awaiting_vendor"),
    ("account_vs_letter", 5, "awaiting_vendor"),
    ("holder_is_person", 3, "awaiting_reviewer"),
    ("wrong_doc", 4, "awaiting_vendor"),
    ("blurry_photo", 4, "awaiting_vendor"),
    ("edited_value_only", 5, "awaiting_reviewer"),
    ("lookalike_vendor", 3, "awaiting_reviewer"),
    ("shared_account", 3, "awaiting_reviewer"),
    ("sanctions_exact", 2, "rejected_compliance"),
]
STATUS = {"approved": "approved", "awaiting_vendor": "pending", "awaiting_reviewer": "pending", "rejected_compliance": "rejected"}
TAX_KIND = {"IN": "gst_certificate", "DE": "vat_certificate", "GB": "vat_certificate", "US": "w9"}
PEOPLE = {"IN": ["Rakesh Sharma", "Meena Pillai"], "DE": ["Thomas Becker", "Sabine Krause"],
          "GB": ["David Lewis", "Sarah Hughes"], "US": ["Michael Turner", "Jessica Moore"]}


def printed_tax(form: dict) -> dict:
    addr = form["address"] + (f" {form['postal_code']}" if form["country"] in ("IN", "GB") else "")
    out = {"legal_name": form["legal_name"], "tax_id": form["tax_id"].upper().replace(" ", ""), "address": addr}
    if form.get("trade_name"):
        out["trade_name"] = form["trade_name"]
    return out


def printed_bank(form: dict, rng) -> dict:
    out = {"bank_name": form["bank_name"], "account_holder": form["account_holder"]}
    for k in ("account_number", "iban", "ifsc", "sort_code", "routing_number"):
        if form.get(k):
            out[k] = form[k]
    if form["country"] in ("DE", "GB") and rng.random() < 0.5:
        out["swift"] = {"Deutsche Bank": "DEUTDEFFXXX", "Commerzbank": "COBADEFFXXX", "Barclays Bank": "BARCGB22",
                        "Lloyds Bank": "LOYDGB2L", "HSBC UK": "HBUKGB4B", "NatWest": "NWBKGB2L"}.get(form["bank_name"], "")
    return {k: v for k, v in out.items() if v}


class Diverse:
    def __init__(self, seed: int, out: Path, today: date | None = None):
        self.rng = random.Random(seed)
        self.out = out
        self.today = today or date.today()
        self.g = Generator(seed + 1, out / "_tmp", self.today)
        self.g.build_master()

    def _doc(self, kind: str, printed: dict, *, days: int | None = None, cc: str = "IN", **kw) -> dict:
        r = self.rng
        d = {"kind": kind, "printed": printed, "issued_days_ago": days if days is not None else r.randint(5, 70),
             "language": "de" if cc == "DE" and r.random() < 0.55 else "en",
             "format": "photo" if r.random() < 0.22 else "pdf", "quality": "clear"}
        d.update(kw)
        return d

    def _bank_kind(self, cc):
        return self.rng.choice(["bank_letter", "bank_letter", "cancelled_cheque"] if cc == "IN" else ["bank_letter"])

    def case(self, cid: str, scenario: str) -> dict:
        r, g = self.rng, self.g
        tax_days = r.randint(200, 2500)
        if scenario in ("lookalike_vendor", "shared_account"):
            form = getattr(g, f"s_{scenario}")(cid)["form"]
        elif scenario == "sanctions_exact":
            base, sfx, cc = r.choice(SANCTIONED)
            idn = g.identity(cc)
            idn["legal_name"] = idn["account_holder"] = f"{base} {sfx}"
            form = g.form(idn)
        else:
            form = g.form(g.identity())
        cc = form["country"]
        tax = self._doc(TAX_KIND[cc], printed_tax(form), days=tax_days, cc=cc)
        bank = self._doc(self._bank_kind(cc), printed_bank(form, r), cc=cc)
        note = ""
        if scenario == "stale_bank_letter":
            bank["issued_days_ago"] = r.randint(120, 420)
            note = f"{bank['issued_days_ago']} days old"
        elif scenario == "undated_bank_letter":
            bank["undated"] = True
            bank["format"] = "pdf"
        elif scenario == "account_vs_letter":
            k = "iban" if cc == "DE" else "account_number"
            form = {**form, k: ids.transpose(r, form[k])}
            note = f"form {k} {form[k]}"
        elif scenario == "holder_is_person":
            bank["printed"]["account_holder"] = r.choice(PEOPLE[cc])
            bank["kind"] = "bank_letter"
        elif scenario == "wrong_doc":
            slot_doc = self._doc(r.choice(["utility_bill", "invoice"]), {"legal_name": form["legal_name"], "address": printed_tax(form)["address"]}, cc=cc)
            if r.random() < 0.5:
                bank = slot_doc
                note = f"{slot_doc['kind']} as bank proof"
            else:
                tax = slot_doc
                note = f"{slot_doc['kind']} as tax certificate"
        elif scenario == "blurry_photo":
            bank.update(format="photo", quality="blurry")
        elif scenario == "edited_value_only":
            k = "iban" if cc == "DE" else "account_number"
            other = g.identity(cc)
            g.used_names.discard(other["core"].lower())
            bank.update(kind="bank_letter", format="pdf", edited={"field": k, "visible_value": other[k], "original_value": form[k]})
            form = {**form, k: other[k]}
            if k == "account_number" and form.get("iban"):
                form["iban"] = ""
                bank["printed"].pop("iban", None)
            note = f"{k} pasted over"
        folder = self.out / "docs" / cid
        docs, truth = {}, {}
        for slot, d in (("tax_cert", tax), ("bank_proof", bank)):
            p, t = layouts.render(folder, slot, d, r, self.today, cc)
            docs[slot] = str(p.resolve().relative_to(ROOT)).replace("\\", "/")
            truth[slot] = t
        sub = dict((s, e) for s, _, e in PLAN)[scenario]
        desc = (f"{scenario.replace('_', ' ')}: tax doc {truth['tax_cert']['layout']}/{truth['tax_cert']['language']}, "
                f"bank doc {truth['bank_proof']['kind']} {truth['bank_proof']['layout']}/{truth['bank_proof']['language']}") + (f" ({note})" if note else "")
        return {"id": cid, "scenario": scenario, "group": scenario, "title": desc, "country": cc, "form": form,
                "docs": docs, "doc_truth": truth, "expect": {"status": STATUS[sub], "substatus": sub}}


def build(seed: int = 83, out: Path | None = None) -> Path:
    out = out or ROOT / "bench" / "diverse"
    if out.exists():
        shutil.rmtree(out)
    (out / "docs").mkdir(parents=True)
    dv = Diverse(seed, out)
    plan = [s for s, n, _ in PLAN for _ in range(n)]
    dv.rng.shuffle(plan)
    cases = [dv.case(f"D{i + 1:03d}-{s}", s) for i, s in enumerate(plan)]
    shutil.rmtree(out / "_tmp", ignore_errors=True)
    meta = {"split": "diverse", "seed": seed, "n": len(cases), "generated_for": dv.today.isoformat(), "master": dv.g.master, "cases": cases}
    (out / "cases.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
