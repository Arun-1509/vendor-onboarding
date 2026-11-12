"""Synthetic vendor-submission generator with ground-truth labels.

Each case = a vendor form + rendered documents + the outcome a careful procurement analyst
would choose. Scenarios are built by taking a CONSISTENT vendor and applying one deliberate
change (or, for combo cases, two). Labels come from the scenario, never from the pipeline.

    python scripts/synth.py --n 800 --seed 11 --split dev
    python scripts/synth.py --n 800 --seed 29 --split test

The vendor master (already-approved vendors that screening compares against) is generated
per dataset, so lookalike / shared-account / bank-change cases have something real to hit.
"""
from __future__ import annotations

import json
import random
import shutil
import unicodedata
from datetime import date, timedelta
from pathlib import Path

from app.validators import names as nm

from . import docs, ids

ROOT = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------ names ----
WORDS = {
    "IN": (["Shree", "Bharat", "Vardhan", "Kaveri", "Tejas", "Arihant", "Sahyadri", "Indus", "Nilgiri", "Pragati",
            "Navkar", "Deccan", "Suryoday", "Konark", "Malabar", "Aravali", "Vindhya", "Ganesha", "Utkal", "Narmada"],
           ["Agro", "Steel", "Polymers", "Logistics", "Textiles", "Pharma", "Infotech", "Engineering", "Packaging",
            "Electricals", "Chemicals", "Foods", "Auto Components", "Fabrication", "Exports", "Castings", "Cables", "Ceramics"]),
    "DE": (["Nordwerk", "Rheinland", "Alpen", "Hansa", "Elbtal", "Schwarzwald", "Weser", "Taunus", "Spreetal", "Moselland",
            "Harzer", "Bodensee", "Isar", "Lausitz", "Eifel", "Odenwald"],
           ["Technik", "Maschinenbau", "Logistik", "Elektro", "Metallbau", "Verpackung", "Systeme", "Antriebstechnik",
            "Kunststoffe", "Industriebedarf", "Messtechnik", "Werkzeugbau"]),
    "GB": (["Thames", "Pennine", "Albion", "Kestrel", "Harbour", "Highland", "Oakridge", "Severn", "Ashford", "Brightwater",
            "Cotswold", "Wessex", "Mercia", "Chiltern", "Fenland", "Solent"],
           ["Supplies", "Engineering", "Consulting", "Logistics", "Print", "Facilities", "Analytics", "Components",
            "Catering", "Security", "Interiors", "Plastics"]),
    "US": (["Lakeshore", "Summit", "Redwood", "Bluegrass", "Granite", "Prairie", "Cascade", "Liberty", "Ironwood",
            "Mesa", "Evergreen", "Sawtooth", "Riverbend", "Copperline", "Northstar", "Bayview"],
           ["Industries", "Software", "Logistics", "Supply", "Manufacturing", "Data Systems", "Packaging", "Freight",
            "Labs", "Electric", "Fabrication", "Analytics"]),
}
# synthesized brand names ('Veltranix', 'Corisora') widen the pool without real company names
SYL_A = ["vel", "tor", "zen", "kai", "mar", "lum", "ar", "bel", "cor", "dra", "fen", "gal", "hel", "jor", "kel", "lor",
         "nor", "pra", "quin", "ros", "sol", "tal", "ven", "wes", "xan", "yor", "zor", "ami", "bri", "cal"]
SYL_B = ["tra", "vi", "na", "ro", "li", "ka", "mo", "de", "sa", "ri", "the", "gu"]
SYL_C = ["nix", "tek", "ion", "ora", "ex", "us", "ia", "on", "ar", "is", "ent", "ly"]
ENTITY = {"IN": [("pvt_ltd", ["Private Limited", "Pvt Ltd"]), ("pvt_ltd", ["Private Limited"]), ("ltd", ["Limited"]),
                 ("llp", ["LLP"])],
          "DE": [("gmbh", ["GmbH"]), ("gmbh", ["GmbH"]), ("ag", ["AG"])],
          "GB": [("ltd", ["Ltd", "Limited"]), ("ltd", ["Limited"]), ("plc", ["PLC"]), ("llp", ["LLP"])],
          "US": [("llc", ["LLC"]), ("inc", ["Inc", "Inc."]), ("corp", ["Corp", "Corporation"])]}
CONTACTS = {"IN": ["Priya Nair", "Rahul Mehta", "Ananya Iyer", "Vikram Rao", "Sneha Kulkarni"],
            "DE": ["Jonas Weber", "Lena Fischer", "Felix Wagner", "Anna Schmidt"],
            "GB": ["Hannah Cole", "Oliver Price", "Amelia Shaw", "George Hunt"],
            "US": ["Emily Carter", "Jacob Reed", "Olivia Brooks", "Mason Hayes"]}
TLD = {"IN": "in", "DE": "de", "GB": "co.uk", "US": "com"}
CURRENCY = {"IN": "INR", "DE": "EUR", "GB": "GBP", "US": "USD"}
SANCTIONED = [("Meridian Crest Shipping", "Co", "US"), ("Blackthorn Offshore Trading", "Ltd", "GB"),
              ("Orion Vale Metals", "LLC", "US")]
SANCTION_ALIASES = ["MC Shipping", "Orion Vale Metallurgy"]


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s.lower() if c.isalpha())[:22]


# --------------------------------------------------------------- scenarios ----
# id: (weight, expected status, substatus, must_have codes, must_not_have codes, description)
APPROVE, VENDOR, REVIEW, REJECT = ("approved", "approved"), ("pending", "awaiting_vendor"), ("pending", "awaiting_reviewer"), ("rejected", "rejected_compliance")
FRAUD_CODES = ["LOOKALIKE_VENDOR", "BANK_ACCOUNT_SHARED", "SANCTIONS_HIT", "SANCTIONS_NEAR", "BANK_CHANGE_REQUEST"]
SCENARIOS = {
    # -------- should be approved (the straight-through path) --------
    "clean":                 (16, APPROVE, [], FRAUD_CODES, "Consistent vendor, clean documents"),
    "clean_scan":            (4, APPROVE, [], FRAUD_CODES, "Clean vendor; bank letter is a legible phone scan"),
    "variant_legal_form":    (4, APPROVE, ["NAME_VARIANT"], [], "'Pvt Ltd' on form, 'Private Limited' on certificate"),
    "variant_case_punct":    (3, APPROVE, [], [], "Upper-case / punctuation / '&' vs 'and' differences only"),
    "variant_trade_name":    (3, APPROVE, ["NAME_TRADE_NAME"], [], "Bank account in the declared trade name"),
    "variant_abbreviation":  (3, APPROVE, [], [], "Bank letter abbreviates one word of the name"),
    "variant_id_formatting": (3, APPROVE, [], [], "IDs typed with spaces / lower case"),
    "ocr_misread":           (3, APPROVE, [], ["BANK_ACCOUNT_MISMATCH_DOC", "TAXID_MISMATCH_DOC"], "Scan where OCR reads one digit as a look-alike letter"),
    "free_email_company":    (2, APPROVE, ["FREE_EMAIL_DOMAIN"], [], "Small company using a free email domain"),
    "branch_gstin":          (2, APPROVE, [], ["GSTIN_STATE_MISMATCH"], "Head-office address on form, GSTIN of a branch state (certificate consistent)"),
    "sanctions_word_overlap": (2, APPROVE, [], ["SANCTIONS_HIT", "SANCTIONS_NEAR"], "Shares one word with a sanctioned name only"),
    # -------- vendor can fix it --------
    "missing_field":         (4, VENDOR, ["MISSING_FIELD"], [], "A required field left blank"),
    "missing_doc":           (4, VENDOR, ["MISSING_DOC"], [], "Bank letter or tax certificate not attached"),
    "wrong_doc":             (3, VENDOR, ["WRONG_DOCUMENT"], [], "Utility bill / invoice uploaded as a required document"),
    "stale_bank_letter":     (3, VENDOR, ["DOC_STALE"], [], "Bank letter older than 90 days"),
    "taxid_typo":            (3, VENDOR, [], [], "Tax ID on the form has a typo (checksum / prefix fails)"),
    "bank_typo":             (3, VENDOR, [], [], "IBAN / routing / IFSC on the form has a typo"),
    # relabelled 2026-11-12: two DIFFERENT VALID numbers cannot be a keying slip (a slip fails the check digit), so a person looks
    "taxid_form_vs_cert":    (2, REVIEW, ["TAXID_DIFFERENT_DOC"], [], "Form tax ID is a different valid number from the certificate"),
    "account_vs_letter":     (3, VENDOR, ["BANK_ACCOUNT_MISMATCH_DOC"], [], "Account number transposed on the form"),
    "holder_is_person":      (3, REVIEW, ["THIRD_PARTY_PAYEE"], [], "Bank account in a person's name (third-party payee)"),
    "extra_word_name":       (2, VENDOR, [], [], "Certificate name has an extra word (possibly a different company)"),
    "blurry_scan":           (3, VENDOR, ["DOC_UNREADABLE"], [], "Bank letter photo too blurry to read"),
    "invalid_email":         (2, VENDOR, ["EMAIL_INVALID"], [], "Contact email is malformed"),
    # -------- a human must look (fraud-shaped) --------
    "lookalike_vendor":      (4, REVIEW, ["LOOKALIKE_VENDOR"], [], "Name imitates an approved vendor; different tax ID and bank"),
    "shared_account":        (3, REVIEW, ["BANK_ACCOUNT_SHARED"], [], "New vendor uses an approved vendor's bank account"),
    "bank_change":           (3, REVIEW, ["BANK_CHANGE_REQUEST"], [], "Approved vendor resubmits with a different bank account"),
    "taxid_dup_other_name":  (2, REVIEW, ["TAXID_DUPLICATE_OTHER_NAME"], [], "Tax ID already registered to another vendor"),
    "pan_entity_mismatch":   (2, REVIEW, ["ENTITY_TYPE_MISMATCH_PAN"], [], "GSTIN's PAN is an individual's; vendor claims a company"),
    "pan_name_mismatch":     (2, REVIEW, [], [], "GSTIN valid, but its PAN belongs to a differently named entity"),
    "cert_state_mismatch":   (2, REVIEW, [], [], "Certificate address is in a different state from the GSTIN"),
    "country_contradiction": (2, REVIEW, ["COUNTRY_CONTRADICTION"], [], "Form country contradicts tax ID and bank"),
    "bank_code_mismatch":    (3, REVIEW, [], [], "Bank code (IFSC / IBAN BLZ / sort code) belongs to another bank"),
    "tampered_bank_letter":  (3, REVIEW, [], [], "Genuine bank letter edited: new account number pasted over the old"),
    "sanctions_near":        (2, REVIEW, ["SANCTIONS_NEAR"], [], "Name is a near-spelling of a sanctioned entity"),
    "prior_rejected":        (1, REVIEW, ["PRIOR_REJECTED"], [], "Reuses the account of a vendor rejected for compliance"),
    "combo_fraud_plus_fix":  (3, REVIEW, [], [], "Fraud signal AND a fixable gap: reviewer must win"),
    # -------- hard stop --------
    "sanctions_exact":       (3, REJECT, ["SANCTIONS_HIT"], [], "Exact / alias / re-ordered sanctioned name"),
}


class Generator:
    def __init__(self, seed: int, out: Path, today: date | None = None):
        self.rng = random.Random(seed)
        self.out = out
        self.today = today or date.today()
        self.used_names: set[str] = set()
        self.master: list[dict] = []
        self.master_idents: list[dict] = []

    # ------------------------------------------------------------ helpers --
    def d(self, days_ago: int) -> str:
        return (self.today - timedelta(days=days_ago)).isoformat()

    def _name(self, cc: str) -> tuple[str, str]:
        a, b = WORDS[cc]
        r = self.rng
        for _ in range(400):
            first = r.choice(a) if r.random() < 0.45 else (r.choice(SYL_A) + r.choice(SYL_B) + r.choice(SYL_C)).capitalize()
            core = f"{first} {r.choice(b)}" + (f" {r.choice(b)}" if r.random() < 0.25 else "")
            if len(set(core.split())) < len(core.split()):
                continue
            # keep accidental lookalikes out of the data: a 'clean' name must not resemble
            # any name already used (that would make its label wrong)
            if core.lower() in self.used_names or any(nm.core_similarity(core, u) >= 0.70 for u in self.used_names):
                continue
            if any(nm.core_similarity(core, s[0]) >= 0.6 for s in SANCTIONED):
                continue
            self.used_names.add(core.lower())
            return core, core
        raise RuntimeError("name pool exhausted; lower --n or add words")

    def identity(self, cc: str | None = None, entity: str | None = None) -> dict:
        r = self.rng
        cc = cc or r.choices(["IN", "DE", "GB", "US"], [4, 2, 2, 2])[0]
        core, _ = self._name(cc)
        etype, suffixes = r.choice([e for e in ENTITY[cc] if not entity or e[0] == entity] or ENTITY[cc])
        legal = f"{core} {suffixes[0]}"
        idn = {"country": cc, "core": core, "legal_name": legal, "entity_type": etype, "trade_name": "",
               "contact_name": r.choice(CONTACTS[cc]), "contact_email": f"{r.choice(['accounts', 'finance', 'ap', 'billing'])}@{_slug(core)}.{TLD[cc]}",
               "currency": CURRENCY[cc], "account_number": "", "iban": "", "ifsc": "", "sort_code": "", "routing_number": ""}
        if cc == "IN":
            city = r.choice(list(ids.IN_CITIES))
            pin, state = ids.IN_CITIES[city]
            street = r.choice(ids.IN_STREETS)
            code = r.choice(list(ids.IN_BANKS))
            p = ids.pan(r, ids.PAN_FOR_ENTITY[etype], core[0])
            idn.update(city=city, postal_code=pin, state=state, address=f"{street}, {city}", tax_id=ids.gstin(state, p),
                       pan=p, bank_name=ids.IN_BANKS[code], ifsc=ids.ifsc(r, code), account_number=ids.in_account(r))
        elif cc == "DE":
            city = r.choice(list(ids.DE_CITIES))
            blz = r.choice(list(ids.DE_BANKS))
            idn.update(city=city, postal_code=ids.DE_CITIES[city], address=f"{r.choice(ids.DE_STREETS)}, {ids.DE_CITIES[city]} {city}",
                       tax_id=ids.de_vat(r), bank_name=ids.DE_BANKS[blz], iban=ids.de_iban(r, blz))
        elif cc == "GB":
            city = r.choice(list(ids.GB_CITIES))
            pre = r.choice(list(ids.GB_BANKS))
            bank, code4 = ids.GB_BANKS[pre]
            sc, acct = ids.sort_code(r, pre), ids.gb_account(r)
            idn.update(city=city, postal_code=ids.GB_CITIES[city], address=f"{r.choice(ids.GB_STREETS)}, {city}",
                       tax_id=ids.gb_vat(r), bank_name=bank, sort_code=sc, account_number=acct,
                       iban=ids.gb_iban(code4, sc, acct) if r.random() < 0.3 else "")
        else:
            city = r.choice(list(ids.US_CITIES))
            idn.update(city=city.split(",")[0], postal_code=ids.US_CITIES[city], address=f"{r.choice(ids.US_STREETS)}, {city}",
                       tax_id=ids.us_ein(r), bank_name=r.choice(ids.US_BANKS), routing_number=ids.aba(r), account_number=ids.us_account(r))
        idn["account_holder"] = legal
        idn["full_address"] = f"{idn['address']} {idn['postal_code']}" if cc in ("IN", "GB") else idn["address"]
        return idn

    @staticmethod
    def form(idn: dict) -> dict:
        keys = ["legal_name", "trade_name", "entity_type", "country", "address", "city", "postal_code", "tax_id",
                "contact_name", "contact_email", "bank_name", "account_holder", "account_number", "iban", "ifsc",
                "sort_code", "routing_number", "currency"]
        return {k: idn.get(k, "") for k in keys}

    @staticmethod
    def acct(idn: dict) -> dict:
        return {k: idn.get(k, "") for k in ("account_number", "iban", "ifsc", "sort_code", "routing_number")}

    def write_docs(self, cid: str, idn: dict, *, tax=True, bank=True, tax_kw=None, bank_kw=None) -> dict:
        folder = self.out / "docs" / cid
        out = {}
        if tax:
            p = folder / "tax_cert.pdf"
            docs.tax_cert(p, self.rng, idn, issue=self.d(self.rng.randint(150, 2500)), **(tax_kw or {}))
            out["tax_cert"] = p
        if bank:
            p = folder / "bank_proof.pdf"
            docs.bank_proof(p, self.rng, idn, issue=self.d(self.rng.randint(2, 75)), **(bank_kw or {}))
            out["bank_proof"] = p
        return out

    def rel(self, paths: dict) -> dict:
        return {k: str(Path(v).resolve().relative_to(ROOT)).replace("\\", "/") for k, v in paths.items()}

    # ------------------------------------------------------------- master --
    def build_master(self, n: int = 24) -> None:
        for i in range(n):
            cc = ["IN", "IN", "DE", "GB", "US"][i % 5]
            idn = self.identity(cc)
            self.master_idents.append(idn)
            self.master.append({"vendor_id": f"V-{i + 1:04d}", "status": "approved", "form": self.form(idn)})
        # one vendor previously rejected for compliance (for PRIOR_REJECTED cases)
        bad = self.identity("GB")
        self.rejected_ident = bad
        self.master.append({"vendor_id": f"V-{n + 1:04d}", "status": "rejected", "substatus": "rejected_compliance", "form": self.form(bad)})

    # ---------------------------------------------------------- scenarios --
    def make(self, cid: str, scenario: str) -> dict:
        r = self.rng
        fn = getattr(self, f"s_{scenario}")
        res = fn(cid)
        form, paths, note = res["form"], res.get("docs", {}), res.get("note", "")
        weight, (st, sub), must, must_not, desc = SCENARIOS[scenario]
        expect = {"status": st, "substatus": sub}
        if must:
            expect["must_have"] = list(must)
        if must_not:
            expect["must_not_have"] = list(must_not)
        expect.update(res.get("expect_extra", {}))
        return {"id": cid, "group": scenario, "scenario": scenario, "title": desc + (f" ({note})" if note else ""),
                "country": form.get("country"), "form": form, "docs": self.rel(paths), "expect": expect}

    # approve -----------------------------------------------------------------
    def s_clean(self, cid):
        idn = self.identity()
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn)}

    def s_clean_scan(self, cid):
        idn = self.identity()
        paths = self.write_docs(cid, idn, bank=False)
        p = self.out / "docs" / cid / "bank_proof.png"
        docs.scan(p, docs.bank_lines(idn, self.acct(idn), idn["account_holder"], idn["bank_name"], self.d(self.rng.randint(3, 60))))
        paths["bank_proof"] = p
        return {"form": self.form(idn), "docs": paths}

    def s_variant_legal_form(self, cid):
        cc = self.rng.choice(["IN", "GB", "US"])
        idn = self.identity(cc, {"IN": "pvt_ltd", "GB": "ltd", "US": "inc"}[cc])
        long_ = {"IN": "Private Limited", "GB": "Limited", "US": "Incorporated"}[cc]
        short = {"IN": "Pvt. Ltd.", "GB": "Ltd", "US": "Inc."}[cc]
        idn["legal_name"] = f"{idn['core']} {short}"
        idn["account_holder"] = idn["legal_name"]
        cert_name = f"{idn['core']} {long_}"
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn, tax_kw={"legal_name": cert_name}), "note": f"{short} vs {long_}"}

    def s_variant_case_punct(self, cid):
        idn = self.identity()
        f = self.form(idn)
        choice = self.rng.randrange(3)
        if choice == 0:
            f["legal_name"] = f["legal_name"].upper()
        elif choice == 1:
            idn["account_holder"] = idn["legal_name"].upper()
        else:
            f["legal_name"] = f["legal_name"].replace(" ", "  ", 1) + "."
        return {"form": f, "docs": self.write_docs(cid, idn, bank_kw={"holder": idn["account_holder"]})}

    def s_variant_trade_name(self, cid):
        idn = self.identity()
        trade = f"{idn['core'].split()[0]} {self.rng.choice(['Direct', 'Express', 'Works', 'Group', 'Hub'])}"
        idn["trade_name"] = trade
        f = self.form(idn)
        f["account_holder"] = trade
        return {"form": f, "docs": self.write_docs(cid, idn, bank_kw={"holder": trade})}

    def s_variant_abbreviation(self, cid):
        for _ in range(30):
            idn = self.identity()
            words = idn["core"].split()
            longs = [k for k, w in enumerate(words) if len(w) >= 8 and k > 0]
            if longs:
                break
            self.used_names.discard(idn["core"].lower())
        i = self.rng.choice(longs)
        abbr = words[:]
        abbr[i] = words[i][:self.rng.choice([3, 4, 5])]
        suffix = idn["legal_name"][len(idn["core"]):]
        holder = " ".join(abbr) + suffix
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn, bank_kw={"holder": holder}), "note": f"'{holder}'"}

    def s_variant_id_formatting(self, cid):
        idn = self.identity()
        f = self.form(idn)
        if f["iban"]:
            f["iban"] = " ".join(f["iban"][i:i + 4] for i in range(0, len(f["iban"]), 4))
        if f["ifsc"]:
            f["ifsc"] = f["ifsc"].lower()
        if f["country"] == "IN":
            f["tax_id"] = f["tax_id"].lower()
        if f["country"] == "GB":
            f["tax_id"] = f"GB {f['tax_id'][2:5]} {f['tax_id'][5:9]} {f['tax_id'][9:]}"
            f["sort_code"] = f["sort_code"].replace("-", "")
        if f["country"] == "US":
            f["tax_id"] = f["tax_id"].replace("-", "")
        return {"form": f, "docs": self.write_docs(cid, idn)}

    def s_ocr_misread(self, cid):
        idn = self.identity(self.rng.choice(["IN", "GB", "US"]))
        paths = self.write_docs(cid, idn, bank=False)
        a = self.acct(idn)
        a["account_number"] = ids.ocr_noise(self.rng, a["account_number"])
        p = self.out / "docs" / cid / "bank_proof.png"
        docs.scan(p, docs.bank_lines(idn, a, idn["account_holder"], idn["bank_name"], self.d(self.rng.randint(3, 60))))
        paths["bank_proof"] = p
        return {"form": self.form(idn), "docs": paths, "note": f"read as {a['account_number']}"}

    def s_free_email_company(self, cid):
        idn = self.identity()
        idn["contact_email"] = f"{_slug(idn['core'])[:12]}.accounts@{self.rng.choice(['gmail.com', 'outlook.com', 'yahoo.com'])}"
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn)}

    def s_branch_gstin(self, cid):
        idn = self.identity("IN")
        # form shows head office in another state; the GSTIN + certificate are the branch's (consistent)
        other = self.rng.choice([c for c, (_, s) in ids.IN_CITIES.items() if s != idn["state"]])
        branch_addr = idn["full_address"]
        f = self.form(idn)
        f["city"] = other
        f["postal_code"] = ids.IN_CITIES[other][0]
        f["address"] = f"Corporate Office, {self.rng.choice(ids.IN_STREETS)}, {other}"
        return {"form": f, "docs": self.write_docs(cid, idn, tax_kw={"address": branch_addr})}

    def s_sanctions_word_overlap(self, cid):
        idn = self.identity(self.rng.choice(["GB", "US"]))
        word = self.rng.choice(["Meridian", "Blackthorn", "Orion"])
        other = self.rng.choice(["Dental Care", "Bakery", "Garden Centre", "Print Studio", "Yoga Studio"])
        idn["core"] = f"{word} {other}"
        idn["legal_name"] = f"{idn['core']} {idn['legal_name'].split()[-1]}"
        idn["account_holder"] = idn["legal_name"]
        idn["contact_email"] = f"hello@{_slug(idn['core'])}.{TLD[idn['country']]}"
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": idn["legal_name"]}

    # vendor ------------------------------------------------------------------
    def s_missing_field(self, cid):
        idn = self.identity()
        f = self.form(idn)
        k = self.rng.choice(["postal_code", "contact_name", "address", "bank_name", "city", "entity_type"])
        f[k] = ""
        return {"form": f, "docs": self.write_docs(cid, idn), "note": k}

    def s_missing_doc(self, cid):
        idn = self.identity()
        which = self.rng.choice(["tax", "bank"])
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn, tax=which != "tax", bank=which != "bank"), "note": f"no {which} document"}

    def s_wrong_doc(self, cid):
        idn = self.identity()
        slot = self.rng.choice(["tax_cert", "bank_proof"])
        paths = self.write_docs(cid, idn, tax=slot != "tax_cert", bank=slot != "bank_proof")
        p = self.out / "docs" / cid / f"{slot}.pdf"
        (docs.utility_bill if self.rng.random() < 0.6 else docs.invoice)(p, idn, self.d(20))
        paths[slot] = p
        return {"form": self.form(idn), "docs": paths, "note": slot}

    def s_stale_bank_letter(self, cid):
        idn = self.identity()
        paths = self.write_docs(cid, idn, bank=False)
        p = self.out / "docs" / cid / "bank_proof.pdf"
        docs.bank_proof(p, self.rng, idn, issue=self.d(self.rng.randint(120, 500)))
        paths["bank_proof"] = p
        return {"form": self.form(idn), "docs": paths}

    def s_taxid_typo(self, cid):
        idn = self.identity(self.rng.choice(["IN", "DE", "GB", "US"]))
        f = self.form(idn)
        if idn["country"] == "US":
            f["tax_id"] = "07-" + f["tax_id"][3:]                      # 07 is not an IRS prefix
            code = "TAXID_FORMAT"
        else:
            f["tax_id"] = ids.typo(self.rng, f["tax_id"], digits_only=idn["country"] != "IN")
            while f["tax_id"] == idn["tax_id"]:
                f["tax_id"] = ids.typo(self.rng, idn["tax_id"])
            code = None
        out = {"form": f, "docs": self.write_docs(cid, idn)}
        if code:
            out["expect_extra"] = {"must_have": [code]}
        return out

    def s_bank_typo(self, cid):
        idn = self.identity()
        f = self.form(idn)
        cc = idn["country"]
        if cc == "DE" or (cc == "GB" and f["iban"]):
            f["iban"] = ids.typo(self.rng, f["iban"], digits_only=True)
            must = "BANK_CHECKSUM"
        elif cc == "US":
            f["routing_number"] = ids.typo(self.rng, f["routing_number"], digits_only=True)
            must = "BANK_CHECKSUM"
        elif cc == "IN":
            f["ifsc"] = f["ifsc"][:4] + "O" + f["ifsc"][5:]            # letter O instead of zero
            must = "BANK_FORMAT"
        else:
            f["sort_code"] = f["sort_code"][:-1]
            must = "BANK_FORMAT"
        return {"form": f, "docs": self.write_docs(cid, idn), "expect_extra": {"must_have": [must]}}

    def s_taxid_form_vs_cert(self, cid):
        idn = self.identity(self.rng.choice(["DE", "GB", "US"]))
        other = {"DE": ids.de_vat, "GB": ids.gb_vat, "US": ids.us_ein}[idn["country"]](self.rng)
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn, tax_kw={"tax_id": other})}

    def s_account_vs_letter(self, cid):
        idn = self.identity(self.rng.choice(["IN", "GB", "US"]))
        f = self.form(idn)
        f["account_number"] = ids.transpose(self.rng, f["account_number"])
        if f["country"] == "GB" and f["iban"]:
            f["iban"] = ""
        return {"form": f, "docs": self.write_docs(cid, idn)}

    def s_holder_is_person(self, cid):
        idn = self.identity()
        person = self.rng.choice(CONTACTS[idn["country"]])
        f = self.form(idn)
        f["account_holder"] = person
        return {"form": f, "docs": self.write_docs(cid, idn, bank_kw={"holder": person}), "note": person}

    def s_extra_word_name(self, cid):
        idn = self.identity()
        extra = self.rng.choice(["Holdings", "Global", "International", "Trading"])
        parts = idn["legal_name"].split(" ")
        n = len(idn["core"].split())
        cert = " ".join(parts[:n] + [extra] + parts[n:])
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn, tax_kw={"legal_name": cert}), "note": cert}

    def s_blurry_scan(self, cid):
        idn = self.identity()
        paths = self.write_docs(cid, idn, bank=False)
        p = self.out / "docs" / cid / "bank_proof.png"
        docs.scan(p, docs.bank_lines(idn, self.acct(idn), idn["account_holder"], idn["bank_name"], self.d(10)), legible=False)
        paths["bank_proof"] = p
        return {"form": self.form(idn), "docs": paths}

    def s_invalid_email(self, cid):
        idn = self.identity()
        f = self.form(idn)
        f["contact_email"] = self.rng.choice([f["contact_email"].replace("@", " at "), f["contact_email"].split(".")[0], "accounts@"])
        return {"form": f, "docs": self.write_docs(cid, idn)}

    # reviewer ----------------------------------------------------------------
    def _mutate_name(self, core: str) -> tuple[str, str]:
        r = self.rng
        kind = r.choice(["suffix", "typo", "add_word", "swap", "homoglyph"])
        w = core.split()
        if kind == "typo":
            i = max(range(len(w)), key=lambda k: len(w[k]))
            j = r.randint(1, len(w[i]) - 2)
            w[i] = w[i][:j] + w[i][j + 1:]
        elif kind == "add_word":
            w.append(r.choice(["India", "Global", "Group", "International", "Services"]))
        elif kind == "swap":
            i = max(range(len(w)), key=lambda k: len(w[k]))
            j = r.randint(1, len(w[i]) - 3)
            s = w[i]
            w[i] = s[:j] + s[j + 1] + s[j] + s[j + 2:]
        elif kind == "homoglyph":
            i = max(range(len(w)), key=lambda k: len(w[k]))
            s = w[i]
            for a, b in (("m", "rn"), ("w", "vv"), ("l", "I"), ("o", "0"), ("i", "l")):
                if a in s[1:]:
                    w[i] = s[0] + s[1:].replace(a, b, 1)
                    break
            else:
                w[i] = s + "s"
        return " ".join(w), kind

    def s_lookalike_vendor(self, cid):
        m = self.rng.choice(self.master_idents)
        idn = self.identity(m["country"], m["entity_type"])
        self.used_names.discard(idn["core"].lower())
        core, kind = self._mutate_name(m["core"])
        suffix = m["legal_name"][len(m["core"]):]
        if kind == "suffix":
            alt = {"Pvt Ltd": "Ltd", "Private Limited": "Limited", "GmbH": "AG", "Ltd": "LLP", "Limited": "PLC", "LLC": "Inc", "Inc": "LLC"}
            suffix = " " + alt.get(suffix.strip(), "Group")
        idn["core"], idn["legal_name"] = core, core + suffix
        idn["account_holder"] = idn["legal_name"]
        if idn["country"] == "IN":   # keep the impostor's own GSTIN internally consistent
            p = ids.pan(self.rng, idn["pan"][3], core[0])
            idn["pan"], idn["tax_id"] = p, ids.gstin(idn["state"], p)
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": f"{kind}: '{idn['legal_name']}' vs '{m['legal_name']}'"}

    def s_shared_account(self, cid):
        m = self.rng.choice(self.master_idents)
        idn = self.identity(m["country"])
        for k in ("account_number", "iban", "ifsc", "sort_code", "routing_number", "bank_name"):
            idn[k] = m.get(k, "")
        # the fraudster also forges a bank letter in their own name for that account
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": f"account of {m['legal_name']}"}

    def s_bank_change(self, cid):
        m = self.rng.choice(self.master_idents)
        idn = dict(m)
        new = self.identity(m["country"])
        self.used_names.discard(new["core"].lower())
        for k in ("account_number", "iban", "ifsc", "sort_code", "routing_number", "bank_name"):
            idn[k] = new.get(k, "")
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": m["legal_name"]}

    def s_taxid_dup_other_name(self, cid):
        m = self.rng.choice([x for x in self.master_idents if x["country"] != "IN"])
        idn = self.identity(m["country"])
        idn["tax_id"] = m["tax_id"]
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": f"tax ID of {m['legal_name']}"}

    def s_pan_entity_mismatch(self, cid):
        idn = self.identity("IN", "pvt_ltd")
        p = ids.pan(self.rng, "P", self.rng.choice("ABCDEFGHKLMNPRSTV"))      # an individual's PAN
        idn["pan"], idn["tax_id"] = p, ids.gstin(idn["state"], p)
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn)}

    def s_pan_name_mismatch(self, cid):
        idn = self.identity("IN", self.rng.choice(["pvt_ltd", "ltd", "llp"]))
        wrong = self.rng.choice([c for c in "ABCDEFGHJKLMNPRSTUVW" if c != idn["core"][0].upper()])
        p = ids.pan(self.rng, idn["pan"][3], wrong)
        idn["pan"], idn["tax_id"] = p, ids.gstin(idn["state"], p)
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": f"PAN initial {wrong}"}

    def s_cert_state_mismatch(self, cid):
        idn = self.identity("IN")
        other = self.rng.choice([c for c, (_, s) in ids.IN_CITIES.items() if s != idn["state"]])
        addr = f"{self.rng.choice(ids.IN_STREETS)}, {other} {ids.IN_CITIES[other][0]}"
        f = self.form(idn)
        f.update(city=other, postal_code=ids.IN_CITIES[other][0], address=f"{addr.rsplit(' ', 1)[0]}")
        return {"form": f, "docs": self.write_docs(cid, idn, tax_kw={"address": addr}), "note": f"GSTIN state {idn['state']}, certificate in {other}"}

    def s_country_contradiction(self, cid):
        src = self.identity("IN")
        claim = self.rng.choice(["DE", "GB", "US"])
        f = self.form(src)
        f["country"] = claim
        f["currency"] = CURRENCY[claim]
        return {"form": f, "docs": self.write_docs(cid, src), "note": f"claims {claim}, Indian IDs"}

    def s_bank_code_mismatch(self, cid):
        cc = self.rng.choice(["IN", "DE", "GB"])
        idn = self.identity(cc)
        if cc == "IN":
            stated = self.rng.choice([b for b in ids.IN_BANKS.values() if b != idn["bank_name"]])
        elif cc == "DE":
            stated = self.rng.choice([b for b in set(ids.DE_BANKS.values()) if b != idn["bank_name"]])
        else:
            stated = self.rng.choice([b for b, _ in ids.GB_BANKS.values() if b != idn["bank_name"]])
        real = idn["bank_name"]
        idn["bank_name"] = stated
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": f"code is {real}, stated {stated}"}

    def s_tampered_bank_letter(self, cid):
        idn = self.identity(self.rng.choice(["IN", "DE", "GB", "US"]))
        folder = self.out / "docs" / cid
        paths = self.write_docs(cid, idn, bank=False)
        orig = folder / "_orig_bank.pdf"
        docs.bank_proof(orig, self.rng, idn, issue=self.d(self.rng.randint(5, 50)), layout="plain")
        new = self.identity(idn["country"])
        self.used_names.discard(new["core"].lower())
        field = "iban" if idn["country"] == "DE" else "account_number"
        label = "IBAN" if field == "iban" else "Account Number"
        line_idx = 2                       # Bank, Account Holder, then the account line
        dst = folder / "bank_proof.pdf"
        docs.tamper_overlay(orig, dst, line_idx, f"{label}: {new[field]}", 90)
        orig.unlink()
        idn[field] = new[field]
        if field == "account_number" and idn["country"] == "GB" and idn["iban"]:
            idn["iban"] = ""
        paths["bank_proof"] = dst
        return {"form": self.form(idn), "docs": paths}

    def s_sanctions_near(self, cid):
        base, sfx, cc = self.rng.choice(SANCTIONED)
        w = base.split()
        i = self.rng.randrange(len(w))
        s = w[i]
        j = self.rng.randint(1, len(s) - 2)
        w[i] = s[:j] + self.rng.choice("aeiou") + s[j + 1:] if s[j] not in "aeiou" else s[:j] + s[j + 1:]
        idn = self.identity(cc)
        idn["core"] = " ".join(w)
        idn["legal_name"] = f"{idn['core']} {sfx}"
        idn["account_holder"] = idn["legal_name"]
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": idn["legal_name"]}

    def s_prior_rejected(self, cid):
        m = self.rejected_ident
        idn = self.identity(m["country"])
        for k in ("account_number", "iban", "sort_code", "bank_name"):
            idn[k] = m.get(k, "")
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn)}

    def s_combo_fraud_plus_fix(self, cid):
        fraud = self.rng.choice(["shared_account", "lookalike_vendor", "bank_change"])
        res = getattr(self, f"s_{fraud}")(cid)
        k = self.rng.choice(["postal_code", "contact_name"])
        res["form"][k] = ""
        res["note"] = f"{fraud} + missing {k}"
        res["expect_extra"] = {"must_have": [{"shared_account": "BANK_ACCOUNT_SHARED", "lookalike_vendor": "LOOKALIKE_VENDOR",
                                              "bank_change": "BANK_CHANGE_REQUEST"}[fraud], "MISSING_FIELD"]}
        return res

    # reject ------------------------------------------------------------------
    def s_sanctions_exact(self, cid):
        base, sfx, cc = self.rng.choice(SANCTIONED)
        kind = self.rng.choice(["exact", "suffix", "reorder", "alias", "accent"])
        name = {"exact": f"{base} {sfx}", "suffix": f"{base} Limited" if sfx != "Ltd" else f"{base} LLC",
                "reorder": " ".join(reversed(base.split())) + f" {sfx}",
                "alias": self.rng.choice(SANCTION_ALIASES) + " Ltd",
                "accent": base.replace("e", "é", 1).replace("o", "ö", 1) + f" {sfx}"}[kind]
        idn = self.identity(cc)
        idn["core"], idn["legal_name"], idn["account_holder"] = name, name, name
        return {"form": self.form(idn), "docs": self.write_docs(cid, idn), "note": f"{kind}: {name}"}


def generate(n: int, seed: int, split: str, out_root: Path | None = None) -> Path:
    out = (out_root or ROOT / "var" / "synth") / split
    if out.exists():
        shutil.rmtree(out)
    (out / "docs").mkdir(parents=True)
    g = Generator(seed, out)
    g.build_master()
    names = list(SCENARIOS)
    weights = [SCENARIOS[s][0] for s in names]
    # every scenario at least twice, the rest by weight
    plan = [s for s in names for _ in range(2)]
    plan += g.rng.choices(names, weights, k=max(0, n - len(plan)))
    g.rng.shuffle(plan)
    cases = []
    for i, sc in enumerate(plan[:max(n, len(names) * 2)]):
        cases.append(g.make(f"{split}-{i + 1:04d}-{sc}", sc))
    meta = {"split": split, "seed": seed, "n": len(cases), "generated_for": g.today.isoformat(),
            "master": g.master, "cases": cases}
    (out / "cases.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
