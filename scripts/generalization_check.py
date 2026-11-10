"""Do the rules added after the ChatGPT holdout GENERALISE, or did they memorise those 37 cases?

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


def docs_of(f, *, cert_name=None, cert_addr=None, holder=None, bank_kind="bank_letter", edited=None):
    bank = {k: f[k] for k in ("bank_name", "account_number", "iban", "ifsc", "sort_code", "routing_number") if f.get(k)}
    bank["account_holder"] = holder or f["account_holder"]
    addr = cert_addr or (f"{f['address']}, {f['postal_code']} {f['city']}" if f["country"] == "DE" else f"{f['address']}, {f['city']} {f['postal_code']}")
    return {"tax_cert": {"kind": TAX_KIND[f["country"]], "printed": {"legal_name": cert_name or f["legal_name"], "tax_id": f["tax_id"], "address": addr},
                         "issued_days_ago": R.randint(30, 600), "language": "en", "format": "pdf", "quality": "clear", "edited": None},
            "bank_proof": {"kind": bank_kind, "printed": bank, "issued_days_ago": R.randint(3, 80), "language": "en", "format": "pdf",
                           "quality": "clear", "edited": edited}}


def case(cid, rule, kind, title, f, docs, expected):
    return {"id": cid, "title": f"[{rule}/{kind}] {title}", "story": title, "form": f, "documents": docs, "expected": expected,
            "why": rule, "difficulty": kind}


def fresh(cc):
    idn = G.identity(cc)
    return form_of(idn)


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
