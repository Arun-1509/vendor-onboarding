"""Do the rules added after the two unseen sets (ChatGPT holdout, 'fresh') GENERALISE, or did they memorise those cases?

For every new rule this builds fresh vendors that share nothing with the holdout (new random names,
all four countries, other banks, other phrasings) in two kinds:
  positive  - the situation the rule is for          -> must get the rule's outcome
  negative  - a near-miss that must NOT trigger it    -> must stay approved / unaffected

    python scripts/generalization_check.py   -> bench/generalization/  + printed scorecard
"""
import json
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import bench  # noqa: E402
from synthetic import blind, ids  # noqa: E402
from synthetic.generator import Generator  # noqa: E402

OUT = ROOT / "bench" / "generalization"
TAX_KIND = {"IN": "gst_certificate", "DE": "vat_certificate", "GB": "vat_certificate", "US": "w9"}
G = Generator(seed=4242, out=OUT / "_tmp", today=date.today())
R = random.Random(4242)
MASTER = [v["form"] for v in json.loads((ROOT / "data" / "seed_vendors.json").read_text(encoding="utf-8"))["vendors"]]


def form_of(idn):
    f = {k: idn.get(k, "") or "" for k in ("legal_name", "trade_name", "entity_type", "country", "address", "city", "postal_code",
                                           "tax_id", "contact_name", "contact_email", "bank_name", "account_holder", "currency",
                                           "account_number", "ifsc", "iban", "sort_code", "routing_number")}
    f["swift"] = ""
    return f


def docs_of(f, *, cert_name=None, cert_addr=None, holder=None, bank_kind="bank_letter", edited=None,
            cert_tax=None, bank_days=None, bank_drop=()):
    """cert_tax='' prints no tax number; bank_days sets the letter's age (negative = future); bank_drop omits fields."""
    bank = {k: f[k] for k in ("bank_name", "account_number", "iban", "ifsc", "sort_code", "routing_number") if f.get(k) and k not in bank_drop}
    bank["account_holder"] = holder or f["account_holder"]
    addr = cert_addr or (f"{f['address']}, {f['postal_code']} {f['city']}" if f["country"] == "DE" else f"{f['address']}, {f['city']} {f['postal_code']}")
    printed = {"legal_name": cert_name or f["legal_name"], "tax_id": f["tax_id"] if cert_tax is None else cert_tax, "address": addr}
    printed = {k: v for k, v in printed.items() if v}
    return {"tax_cert": {"kind": TAX_KIND[f["country"]], "printed": printed,
                         "issued_days_ago": R.randint(30, 600), "language": "en", "format": "pdf", "quality": "clear", "edited": None},
            "bank_proof": {"kind": bank_kind, "printed": bank, "issued_days_ago": R.randint(3, 80) if bank_days is None else bank_days, "language": "en", "format": "pdf",
                           "quality": "clear", "edited": edited}}


def case(cid, rule, kind, title, f, docs, expected):
    return {"id": cid, "title": f"[{rule}/{kind}] {title}", "story": title, "form": f, "documents": docs, "expected": expected,
            "why": rule, "difficulty": kind}


def fresh(cc):
    idn = G.identity(cc)
    return form_of(idn)


SWIFT = {"IN": "HDFCINBB", "DE": "DEUTDEFF", "GB": "BARCGB22", "US": "BOFAUS3N"}
CUR = {"IN": "INR", "DE": "EUR", "GB": "GBP", "US": "USD"}
OTHER_TAX = {"DE": ids.de_vat, "GB": ids.gb_vat, "US": ids.us_ein}
WRONG_TYPE = {"pvt_ltd": "llp", "ltd": "llp", "llp": "pvt_ltd", "gmbh": "ag", "ag": "gmbh", "plc": "llp", "llc": "inc", "inc": "llc"}
FAR_CITY = {"DE": ("Hamburg", "20095"), "GB": ("Glasgow", "G1 1AA"), "US": ("Portland, OR", "97201")}


def build_fresh_rules(nid_start: int) -> list:
    """Probes for the rules added after the 'fresh' unseen set (2026-11-12). New random companies each run of the
    seed; every rule gets the situation it is for AND a near-miss it must leave alone."""
    out = []
    nid = lambda: f"G{nid_start + len(out) + 1:03d}"  # noqa: E731
    add = lambda *a: out.append(case(nid(), *a))  # noqa: E731
    for cc in ["IN", "DE", "GB", "US"]:
        # a. contact email that is not an address
        f = fresh(cc)
        local, dom = f["contact_email"].split("@")
        f["contact_email"] = R.choice([f"{local}@{dom.replace('.', ',', 1)}", f"{local}@{dom.replace('.', ' ', 1)}", f"{local}@{dom.split('.')[0]}", f"{local}@@{dom}"])
        add("email_invalid", "positive", f"contact email '{f['contact_email']}'", f, docs_of(f), "awaiting_vendor")
        f = fresh(cc)
        local, dom = f["contact_email"].split("@")
        f["contact_email"] = R.choice([f"{local}+invoices@{dom}", f"{local}.ap@mail.{dom}", f"{local}_1@{dom}"])
        add("email_invalid", "negative", f"unusual but valid address '{f['contact_email']}'", f, docs_of(f), "approved")
        # b. bank letter dated in the future
        f = fresh(cc)
        ahead = R.randint(3, 40)
        add("future_date", "positive", f"bank letter dated {ahead} days ahead", f, docs_of(f, bank_days=-ahead), "awaiting_reviewer")
        f = fresh(cc)
        add("future_date", "negative", "bank letter dated today", f, docs_of(f, bank_days=0), "approved")
        # c. a document that does not show the number it should prove
        f = fresh(cc)
        add("doc_incomplete", "positive", "tax document without a tax number", f, docs_of(f, cert_tax=""), "awaiting_vendor")
        f = fresh(cc)
        add("doc_incomplete", "positive", "bank letter without an account number", f, docs_of(f, bank_drop=("account_number", "iban")), "awaiting_vendor")
        # d. dropdown contradicts the legal form in the name
        f = fresh(cc)
        if f["entity_type"] in WRONG_TYPE:
            was = f["entity_type"]
            f["entity_type"] = WRONG_TYPE[was]
            add("entity_dropdown", "positive", f"name says {was}, dropdown says {f['entity_type']}", f, docs_of(f), "awaiting_vendor")
        if cc != "IN":                                           # (in India the PAN also encodes the entity type)
            f = fresh(cc)
            core = f["legal_name"].rsplit(" ", 1)[0]
            f["legal_name"] = f["account_holder"] = core          # no legal form in the name at all: nothing to contradict
            add("entity_dropdown", "negative", "name carries no legal form", f, docs_of(f), "approved")
        # e. two different valid tax numbers vs a slip
        f = fresh(cc)
        other = fresh(cc)["tax_id"] if cc == "IN" else OTHER_TAX[cc](R)
        add("taxid_different", "positive", "certificate shows an unrelated valid tax ID", f, docs_of(f, cert_tax=other), "awaiting_reviewer")
        if cc == "IN":
            f = fresh(cc)
            st = "29" if f["tax_id"][:2] != "29" else "27"
            add("taxid_different", "negative", "same PAN, the company's registration in another state", f,
                docs_of(f, cert_tax=ids.gstin(st, f["tax_id"][2:12])), "awaiting_vendor")
        if cc == "US":
            f = fresh(cc)
            add("taxid_different", "negative", "certificate differs from the form by one digit", f,
                docs_of(f, cert_tax=ids.typo(R, f["tax_id"], digits_only=True)), "awaiting_vendor")
        # f. account in a sister company's name vs a misspelt holder
        f = fresh(cc)
        from app.validators import names as _nm
        n_core = len(_nm.split_name(f["legal_name"], expand=False)[0])       # words before the legal form ('Private Limited' is two)
        words = f["legal_name"].split()
        sister = (" ".join(words[:n_core - 1] + [R.choice(["Trading", "Holdings", "Logistics", "Ventures", "Realty"])] + words[n_core:])
                  if 2 <= n_core <= len(words) else None)
        if sister and sister != f["legal_name"]:
            add("sister_payee", "positive", f"cheque in the name of '{sister}'", f, docs_of(f, holder=sister), "awaiting_reviewer")
        f = fresh(cc)
        w = f["legal_name"].split()
        i = max(range(len(w) - 1), key=lambda k: len(w[k]))
        typo = w[i][:2] + w[i][3] + w[i][2] + w[i][4:] if len(w[i]) > 4 else w[i]
        # a spelling slip on the bank's side is recorded as a variant and approved; it must never be read as another payee
        add("sister_payee", "negative", "holder name has two letters swapped", f, docs_of(f, holder=" ".join(w[:i] + [typo] + w[i + 1:])), "approved")
        # g. another entity's tax registration (parent / franchisor) vs a harmless extra word
        if cc in FAR_CITY:
            f = fresh(cc)
            words = f["legal_name"].split()
            parent = f["legal_name"]
            place = R.choice(["North", "Midlands", "West", "Central"])
            f["legal_name"] = f["account_holder"] = " ".join(words[:-1] + [f"({place})"] + words[-1:])
            city, pc = FAR_CITY[cc]
            add("other_entity_cert", "positive", "certificate is the parent's, registered in another town", f,
                docs_of(f, cert_name=parent, cert_addr=f"12 Registry Road, {city} {pc}"), "awaiting_reviewer")
            f = fresh(cc)
            words = f["legal_name"].split()
            parent = f["legal_name"]
            f["legal_name"] = f["account_holder"] = " ".join(words[:-1] + [f"({place})"] + words[-1:])
            add("other_entity_cert", "negative", "extra word, but the certificate is at the vendor's own address", f,
                docs_of(f, cert_name=parent), "awaiting_vendor")
        # h. paid abroad in a foreign currency vs a normal SWIFT code
        f = fresh(cc)
        abroad = R.choice([x for x in SWIFT if x != cc])
        f.update(swift=SWIFT[abroad], currency=CUR[abroad] if CUR[abroad] != CUR[cc] else "USD", ifsc="", iban="", sort_code="", routing_number="",
                 bank_name={"IN": "HDFC Bank", "DE": "Deutsche Bank", "GB": "Barclays", "US": "Bank of America"}[abroad],
                 account_number=f.get("account_number") or "4400129987")
        if f["currency"] != CUR[cc]:
            add("offshore", "positive", f"{cc} vendor paid in {f['currency']} via {f['swift']}", f, docs_of(f), "awaiting_reviewer")
        f = fresh(cc)
        f["swift"] = SWIFT[cc]
        add("offshore", "negative", "own-country SWIFT code, home currency", f, docs_of(f), "approved")
        # i. contact domain spelling the company name with digits
        f = fresh(cc)
        tok = max((t for t in f["legal_name"].split()[:-1]), key=len).lower()
        swaps = [(a, b) for a, b in (("l", "1"), ("o", "0"), ("e", "3"), ("s", "5"), ("i", "1"), ("a", "4")) if a in tok]
        if swaps and len(tok) >= 5:
            a, b = R.choice(swaps)
            f["contact_email"] = f"accounts@{tok.replace(a, b)}-{R.choice(['group', 'pay', 'billing'])}.com"
            add("lookalike_domain", "positive", f"domain '{f['contact_email'].split('@')[1]}' for '{tok}'", f, docs_of(f), "awaiting_reviewer")
        f = fresh(cc)
        tok = max((t for t in f["legal_name"].split()[:-1]), key=len).lower()
        f["contact_email"] = f"accounts@{tok}{R.choice(['24', '365', '2000'])}.com"
        add("lookalike_domain", "negative", f"genuine digits in the domain '{f['contact_email'].split('@')[1]}'", f, docs_of(f), "approved")
        # j. the same name with the spaces elsewhere
        f = fresh(cc)
        w = f["legal_name"].split()
        if len(w) >= 3:
            add("spacing", "positive", "certificate joins two words of the name", f, docs_of(f, cert_name=" ".join([w[0] + w[1]] + w[2:])), "approved")
    # k. German spellings (umlauts typed out, 'und', legal form written in full)
    for word, typed in (("Müller", "Mueller"), ("Schröder", "Schroeder"), ("Jäger", "Jaeger"), ("Weiß", "Weiss")):
        f = fresh("DE")
        tail = f["legal_name"].split()[-2]
        suffix = f["legal_name"].split()[-1]
        official = f"{word} & Söhne {tail} {suffix}"
        f["legal_name"] = f["account_holder"] = f"{typed} und Soehne {tail} {suffix}"
        add("german_spelling", "positive", f"'{f['legal_name']}' vs '{official}'", f, docs_of(f, cert_name=official, holder=official), "approved")
        f = fresh("DE")
        long = {"GmbH": "Gesellschaft mit beschränkter Haftung", "AG": "Aktiengesellschaft"}.get(f["legal_name"].split()[-1])
        if long:
            full = f["legal_name"].rsplit(" ", 1)[0] + " " + long
            add("german_spelling", "positive", f"legal form written in full: '{full}'", f, docs_of(f, cert_name=full, holder=full), "approved")
        f = fresh("DE")
        w = f["legal_name"].split()
        add("german_spelling", "negative", "certificate names a different company with the same surname", f,
            docs_of(f, cert_name=f"{w[0]} {R.choice(['Immobilien', 'Gastronomie', 'Reisen'])} {w[-1]}"), "not_approved")
    return out


def build():
    cases, n = [], 0
    nid = lambda: f"G{len(cases) + 1:03d}"  # noqa: E731
    countries = ["IN", "DE", "GB", "US"]
    for cc in countries:
        # 1. apostrophes inside a name
        f = fresh(cc)
        w = f["legal_name"].split(" ", 1)                   # keep the first letter (Indian PANs encode it)
        f["legal_name"] = f["account_holder"] = f"{w[0]}'s {w[1]}"
        cases.append(case(nid(), "apostrophe", "positive", "form has an apostrophe, certificate prints it without", f,
                          docs_of(f, cert_name=f"{w[0]}s {w[1]}"), "approved"))
        f = fresh(cc)
        other = fresh(cc)["legal_name"]
        cases.append(case(nid(), "apostrophe", "negative", "certificate names a genuinely different company", f,
                          docs_of(f, cert_name="O'" + other), "not_approved"))
        # 2. 'a trade name of'
        f = fresh(cc)
        tn = R.choice(["Studio", "Direct", "Works", "Hub"])
        phrase = R.choice(["a trade name of", "trading name of", "a business name of"])
        cases.append(case(nid(), "trade_name_of", "positive", f"bank holder '<brand>, {phrase} <legal>'", f,
                          docs_of(f, holder=f"{f['legal_name'].split()[0]} {tn}, {phrase} {f['legal_name']}"), "approved"))
        f = fresh(cc)
        other = fresh(cc)["legal_name"]
        cases.append(case(nid(), "trade_name_of", "negative", "holder is the vendor's brand but a trade name of ANOTHER company", f,
                          docs_of(f, holder=f"{f['legal_name'].split()[0]} Direct, a trade name of {other}"), "not_approved"))
        # 4. shared contact email with an existing vendor
        f = fresh(cc)
        f["contact_email"] = R.choice(MASTER)["contact_email"]
        cases.append(case(nid(), "contact_shared", "positive", "new company uses an existing vendor's exact mailbox", f, docs_of(f), "awaiting_reviewer"))
        f = fresh(cc)
        m = R.choice(MASTER)["contact_email"]
        f["contact_email"] = "ap2@" + m.split("@")[1].replace(".", "-x.", 1)
        cases.append(case(nid(), "contact_shared", "negative", "similar-looking but different mailbox", f, docs_of(f), "approved"))
        # 5. tampered document that is ALSO the wrong kind
        f = fresh(cc)
        fld = "iban" if f.get("iban") and not f.get("account_number") else "account_number"
        orig = ids.typo(R, f[fld], digits_only=True)
        cases.append(case(nid(), "tamper_wrong_type", "positive", "bank STATEMENT with a pasted-over account", f,
                          docs_of(f, bank_kind="bank_statement", edited={"field": fld, "visible_value": f[fld], "original_value": orig}), "awaiting_reviewer"))
        f = fresh(cc)
        cases.append(case(nid(), "tamper_wrong_type", "negative", "honest bank statement (no edits)", f,
                          docs_of(f, bank_kind="bank_statement"), "awaiting_vendor"))
        # 6. street number
        f = fresh(cc)
        num = next((t for t in f["address"].replace(",", " ").split() if t.isdigit()), None)
        if num:
            wrong = f["address"].replace(num, str(int(num) + R.choice([1, 2, 10])), 1)
            cert = (f"{wrong}, {f['postal_code']} {f['city']}" if cc == "DE" else f"{wrong}, {f['city']} {f['postal_code']}")
            cases.append(case(nid(), "street_number", "positive", "same city, different street number on certificate", f,
                              docs_of(f, cert_addr=cert), "awaiting_vendor"))
        f = fresh(cc)
        cert = f"{f['address'].upper().replace('STREET', 'ST.')}, {f['city']} {f['postal_code']}"
        cases.append(case(nid(), "street_number", "negative", "same address written differently (caps, abbreviations)", f,
                          docs_of(f, cert_addr=cert), "approved"))
    cases += build_fresh_rules(nid_start=len(cases))
    # 3. contact change on each EXISTING approved vendor (same data otherwise)
    for m in MASTER:
        f = {**{k: m.get(k) or "" for k in form_of({}).keys()}, "swift": ""}
        f["contact_email"] = R.choice(["finance", "payments", "ap"]) + "@" + R.choice(["gmail.com", "proton.me", f"{m['legal_name'].split()[0].lower()}-pay.com"])
        cases.append(case(nid(), "contact_change", "positive", f"{m['legal_name']} resubmits with a new email domain", f, docs_of(f), "awaiting_reviewer"))
        f = {**{k: m.get(k) or "" for k in form_of({}).keys()}, "swift": ""}
        cases.append(case(nid(), "contact_change", "negative", f"{m['legal_name']} resubmits unchanged", f, docs_of(f), "approved"))
    return cases


def main():
    cases = build()
    expected_map = {}
    for c in cases:                                         # 'not_approved' is scored below; compile as reviewer
        expected_map[c["id"]] = c["expected"]
        if c["expected"] == "not_approved":
            c["expected"] = "awaiting_reviewer"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "spec.json").write_text(json.dumps({"author_note": "generalisation probes", "cases": cases}, indent=1), encoding="utf-8")
    blind.build(OUT / "spec.json", out=OUT)
    r = bench.run_set("generalization", bench.TruthLLM())
    by = {}
    for row in r["rows"]:
        exp = expected_map[row["id"]]
        ok = row["got"] != "approved" if exp == "not_approved" else row["got"] == exp
        rule, kind = row["title"][1:].split("]")[0].split("/")
        by.setdefault((rule, kind), []).append(ok)
        if not ok:
            print(f"  MISS {row['id']} {row['title']}: expected {exp}, got {row['got']} {row['codes']}")
    tot = sum(sum(v) for v in by.values()), sum(len(v) for v in by.values())
    for (rule, kind), v in sorted(by.items()):
        print(f"{rule:18s} {kind:9s} {sum(v)}/{len(v)}")
    print(f"TOTAL {tot[0]}/{tot[1]}")
    (ROOT / "reports" / "generalization_check.json").write_text(json.dumps(
        {"total": tot, "by_rule": {f"{a}/{b}": [sum(v), len(v)] for (a, b), v in by.items()}}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
