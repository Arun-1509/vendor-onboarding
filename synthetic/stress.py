"""Stress split: variations the rules were NOT designed around.

Written before measuring, to find the model's limits honestly rather than to score well.
    python scripts/synth.py --split stress --n 300 --seed 47
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import docs, ids
from .generator import APPROVE, REVIEW, ROOT, SANCTIONED, SCENARIOS, Generator, _slug

STRESS = {
    "x_abbrev_nonprefix":   (3, APPROVE, [], [], "Bank letter uses a non-prefix abbreviation (Engg / Mfg / Intl / Tech)"),
    "x_ocr_heavy":          (3, APPROVE, [], [], "Scan with two digit misreads in the account and one in the holder name"),
    "x_signed_pdf":         (3, APPROVE, [], [], "Genuine letter saved with a later annotation (incremental save, no changed values)"),
    "x_unicode_name":       (3, APPROVE, [], [], "Umlaut vs transliterated spelling between sources (Mueller vs Muller)"),
    "x_lookalike_translit": (3, REVIEW, ["LOOKALIKE_VENDOR"], [], "Impostor uses a transliteration / plural change of an approved vendor's name"),
    "x_lookalike_xcountry": (2, REVIEW, ["LOOKALIKE_VENDOR"], [], "Impostor registers an approved vendor's exact name in another country"),
    "x_sanctions_typo2":    (2, REVIEW, ["SANCTIONS_NEAR"], [], "Sanctioned name with two spelling changes"),
    "x_tax_cert_tampered":  (3, REVIEW, [], [], "Tax certificate edited: the vendor's name pasted over another company's"),
    "x_minor_flags_sum":    (2, APPROVE, [], [], "Small harmless observations together (risk stays under the limit)"),
}
_ABBR = {"Engineering": ["Engg", "Engg."], "Manufacturing": ["Mfg", "Mfg."], "International": ["Intl"],
         "Technologies": ["Tech"], "Electricals": ["Elec"], "Industries": ["Inds"], "Components": ["Comp", "Cmpnts"]}
_TRANSLIT = [("Shree", "Shri"), ("Technik", "Technic"), ("Logistics", "Logistix"), ("Cables", "Cable"), ("Systems", "System"),
             ("Labs", "Laboratories"), ("Supplies", "Supply"), ("Exports", "Export"), ("Castings", "Casting"), ("Foods", "Food"),
             ("Logistik", "Logistic"), ("Elektro", "Electro"), ("Freight", "Frieght")]


class StressGenerator(Generator):
    def make(self, cid, scenario):
        SCENARIOS.update(STRESS)
        return super().make(cid, scenario)

    def s_x_abbrev_nonprefix(self, cid):
        hit = None
        for _ in range(60):
            idn = self.identity()
            hit = next((w for w in idn["core"].split()[1:] if w in _ABBR), None)
            if hit:
                break
            self.used_names.discard(idn["core"].lower())
        if not hit:
            idn = self.identity("US")
            idn["core"] += " Manufacturing"
            idn["legal_name"] = f"{idn['core']} {idn['legal_name'].split()[-1]}"
            idn["account_holder"] = idn["legal_name"]
            hit = "Manufacturing"
        holder = idn["legal_name"].replace(hit, self.rng.choice(_ABBR[hit]), 1)
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn, bank_kw={"holder": holder}), "note": holder}

    def s_x_ocr_heavy(self, cid):
        idn = self.identity(self.rng.choice(["IN", "GB", "US"]))
        paths = self.write_docs(cid, idn, bank=False)
        a = self.acct(idn)
        a["account_number"] = ids.ocr_noise(self.rng, ids.ocr_noise(self.rng, a["account_number"]))
        h = idn["account_holder"]
        holder = h.replace("l", "1", 1) if "l" in h else h.replace("o", "0", 1)
        p = self.out / "docs" / cid / "bank_proof.png"
        docs.scan(p, docs.bank_lines(idn, a, holder, idn["bank_name"], self.d(self.rng.randint(3, 60))))
        paths["bank_proof"] = p
        return {"form": self.form(idn), "docs": paths, "note": f"{a['account_number']} / {holder}"}

    def s_x_signed_pdf(self, cid):
        idn = self.identity()
        paths = self.write_docs(cid, idn, bank=False)
        orig = self.out / "docs" / cid / "_o.pdf"
        docs.bank_proof(orig, self.rng, idn, issue=self.d(self.rng.randint(3, 60)), layout="plain")
        dst = self.out / "docs" / cid / "bank_proof.pdf"
        docs.tamper_overlay(orig, dst, 9, "Digitally signed by Branch Operations", 90)   # a stamp below the content
        orig.unlink()
        paths["bank_proof"] = dst
        return {"form": self.form(idn), "docs": paths}

    def s_x_unicode_name(self, cid):
        idn = self.identity("DE")
        word = self.rng.choice(["Müller", "Schröder", "Köhler", "Bäcker"])
        ascii_ = word.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae")
        idn["core"] = f"{word} {idn['core'].split()[-1]}"
        idn["legal_name"] = f"{idn['core']} GmbH"
        idn["entity_type"] = "gmbh"                 # (was left as whatever identity() drew, e.g. 'ag': found by the name-vs-dropdown rule)
        idn["account_holder"] = idn["legal_name"].replace(word, ascii_)
        f = self.form(idn)
        f["legal_name"] = idn["legal_name"].replace(word, ascii_)
        return {"form": f, "docs": self.write_docs(cid, idn, bank_kw={"holder": idn["account_holder"]}), "note": f"{word} vs {ascii_}"}

    def s_x_lookalike_translit(self, cid):
        cands = [(m, a, b) for m in self.master_idents for a, b in _TRANSLIT if a in m["core"].split()]
        if not cands:
            return self.s_lookalike_vendor(cid)
        m, a, b = self.rng.choice(cands)
        idn = self.identity(m["country"], m["entity_type"])
        self.used_names.discard(idn["core"].lower())
        core = m["core"].replace(a, b, 1)
        idn["core"], idn["legal_name"] = core, m["legal_name"].replace(a, b, 1)
        idn["account_holder"] = idn["legal_name"]
        if idn["country"] == "IN":
            p = ids.pan(self.rng, idn["pan"][3], core[0])
            idn["pan"], idn["tax_id"] = p, ids.gstin(idn["state"], p)
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": f"'{idn['legal_name']}' vs '{m['legal_name']}'"}

    def s_x_lookalike_xcountry(self, cid):
        m = self.rng.choice(self.master_idents)
        cc = self.rng.choice([c for c in ("IN", "DE", "GB", "US") if c != m["country"]])
        idn = self.identity(cc)
        self.used_names.discard(idn["core"].lower())
        sfx = idn["legal_name"][len(idn["core"]):]
        idn["core"], idn["legal_name"] = m["core"], m["core"] + sfx
        idn["account_holder"] = idn["legal_name"]
        if cc == "IN":
            p = ids.pan(self.rng, idn["pan"][3], m["core"][0])
            idn["pan"], idn["tax_id"] = p, ids.gstin(idn["state"], p)
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": f"{m['legal_name']} ({m['country']}) as {idn['legal_name']} ({cc})"}

    def s_x_sanctions_typo2(self, cid):
        base, sfx, cc = self.rng.choice(SANCTIONED)
        w = list(base)
        for _ in range(2):
            i = self.rng.choice([i for i, ch in enumerate(w) if ch.isalpha() and i > 0])
            w[i] = self.rng.choice("aeiouy") if w[i] not in "aeiou" else ""
        core = "".join(w)
        idn = self.identity(cc)
        idn["core"], idn["legal_name"] = core, f"{core} {sfx}"
        idn["account_holder"] = idn["legal_name"]
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": idn["legal_name"]}

    def s_x_tax_cert_tampered(self, cid):
        idn = self.identity()
        folder = self.out / "docs" / cid
        paths = self.write_docs(cid, idn, tax=False)
        real = self.identity(idn["country"], idn["entity_type"])
        self.used_names.discard(real["core"].lower())
        cc = idn["country"]
        orig = folder / "_t.pdf"
        docs._pdf(orig, docs.TAX_TITLE[cc][0],
                  [f"{docs.NAME_LABEL[cc][0]}: {real['legal_name']}", f"{docs.TAX_LABEL[cc]}: {idn['tax_id']}",
                   f"Registered Address: {idn['full_address']}", f"Date of Issue: {self.d(900)}"])
        dst = folder / "tax_cert.pdf"
        docs.tamper_overlay(orig, dst, 0, f"{docs.NAME_LABEL[cc][0]}: {idn['legal_name']}", 90)
        orig.unlink()
        paths["tax_cert"] = dst
        return {"form": self.form(idn), "docs": paths, "note": f"real holder {real['legal_name']}"}

    def s_x_minor_flags_sum(self, cid):
        idn = self.identity("IN")
        trade = f"{idn['core'].split()[0]} {self.rng.choice(['Direct', 'Works'])}"
        idn["trade_name"] = trade
        idn["contact_email"] = f"{_slug(idn['core'])[:10]}@gmail.com"
        f = self.form(idn)
        f["account_holder"] = trade
        return {"form": f, "docs": self.write_docs(cid, idn, bank_kw={"holder": trade}), "note": "trade name + free email"}


def generate_stress(n: int, seed: int, out_root: Path | None = None) -> Path:
    out = (out_root or ROOT / "var" / "synth") / "stress"
    if out.exists():
        shutil.rmtree(out)
    (out / "docs").mkdir(parents=True)
    g = StressGenerator(seed, out)
    g.build_master()
    names = list(STRESS)
    plan = [s for s in names for _ in range(3)] + g.rng.choices(names, [STRESS[s][0] for s in names], k=max(0, n - 3 * len(names)))
    g.rng.shuffle(plan)
    cases = [g.make(f"stress-{i + 1:04d}-{sc}", sc) for i, sc in enumerate(plan)]
    (out / "cases.json").write_text(json.dumps({"split": "stress", "seed": seed, "n": len(cases), "generated_for": g.today.isoformat(),
                                                "master": g.master, "cases": cases}, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
