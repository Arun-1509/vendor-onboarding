"""Robustness benchmarks.

  python scripts/bench.py build-blind        # bench/blind/  from bench/blind/spec.json (independent author)
  python scripts/bench.py build-diverse      # bench/diverse/ (varied layouts, languages, photos, edits)
  python scripts/bench.py run                # both sets with the perfect reader -> reports/bench_<set>_truth.json
  python scripts/bench.py summarize          # -> reports/bench_summary.json (shown on the Model quality page)

The LIVE run (real model) happens on the deployed server, where the API key lives:
Model quality -> 'Run live benchmark' (admin key). Save its JSON as reports/bench_live.json.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import bench  # noqa: E402

REP = ROOT / "reports"
FINDINGS = [
    {"set": "holdout", "found": "A lookalike of an existing vendor reused its tax ID and bank account but switched the contact to a ProtonMail address, and was approved.",
     "fix": "An existing vendor whose contact email moves to another domain goes to a reviewer (account takeover starts with the mailbox)."},
    {"set": "holdout", "found": "A new company used an existing vendor's exact contact email and was approved.",
     "fix": "One mailbox used by two different companies goes to a reviewer, like a shared bank account."},
    {"set": "holdout", "found": "An edited bank STATEMENT was only refused as the wrong document; the edit was never flagged.",
     "fix": "The tamper check runs on every uploaded file, including ones then refused as the wrong kind."},
    {"set": "holdout", "found": "'O'Connell' vs 'OConnell' and 'Brand, a trade name of Company LLC' were treated as different payees; a street number differing from the certificate (same city) was approved.",
     "fix": "Apostrophes join words; 'a trade/trading/business name of X' resolves to X; same postcode/city but another street number asks the vendor to confirm."},
    {"set": "diverse", "found": "Bank-letter dates printed as 10/07/2026 or 9 November 2026 were not matched to the ISO date the reader returns, so the 90-day check silently skipped them.",
     "fix": "Dates are compared as dates (all common styles); a bank letter without a verifiable date now asks the vendor (DOC_UNDATED)."},
    {"set": "diverse", "found": "A fraudster pasting over only the account NUMBER (not the whole 'IBAN: ...' line) was not detected.",
     "fix": "Two different texts drawn at the same spot of a page are flagged as a pasted-over value, whatever the field."},
    {"set": "blind", "found": "The account holder was never screened: a clean company asking to be paid into a sanctioned party's account was not rejected.",
     "fix": "Form and bank-letter account holders are screened against the sanctions list."},
    {"set": "blind", "found": "A name containing a sanctioned entity's whole distinctive name ('Meridian Crest Logistics') was approved.",
     "fix": "Sharing the full distinctive part of a listed name goes to compliance review."},
    {"set": "blind", "found": "Payments to a third party (factoring company, a director personally) were sent back to the vendor instead of a reviewer.",
     "fix": "Policy change: a clearly different account holder is a third-party payee -> reviewer with four-eyes (my own earlier labels were relabelled)."},
    {"set": "blind", "found": "A wholly different account on the form vs the bank letter was treated like a typo; another company's tax registration was treated like an upload slip.",
     "fix": "Typo vs different account is measured; a tax ID registered to a differently named company -> reviewer."},
    {"set": "blind", "found": "Honest variants were held: 'M/s' prefix, 'DBA' account titles, 'UG (haftungsbeschraenkt)', swapped upload slots, company PAN with the wrong dropdown.",
     "fix": "Recognised as variants or form slips (vendor asked to correct the type); swapped documents are read as what they are."},
    {"set": "blind", "found": "A bank statement was accepted as bank proof; a US domestic account in EUR was accepted.",
     "fix": "Statements are recognised and the vendor is asked for a bank letter; a foreign currency on a domestic-only account asks the vendor to confirm."},
    {"set": "fresh", "found": "Three vendors were approved with something plainly wrong on the form: 'LLC' selected for a company named '... Inc.', a W-9 with the tax-number box empty, and a contact email with a comma in it.",
     "fix": "The dropdown is checked against the legal form in the company's own name; a document must actually show the number it is meant to prove; email addresses are validated properly."},
    {"set": "fresh", "found": "A contact domain spelling the company's name with digits ('carro11ton' for Carrollton) was approved.",
     "fix": "A domain that turns into a word of the company name once digits are read as letters goes to a reviewer."},
    {"set": "fresh", "found": "Fraud-shaped cases were sent back to the vendor: a future-dated bank letter, two different VALID tax IDs for one company, a cheque in a sister company's name, a franchisor's tax certificate, an Indian supplier asking for dollars into a US account.",
     "fix": "Future dates, different valid tax IDs (a slip fails the check digit), a holder name with a whole word swapped, another entity's registration from another town, and offshore payment in a foreign currency all go to a reviewer."},
    {"set": "fresh", "found": "Honest German and Indian spellings were held: 'Mueller und Soehne' vs 'Müller & Söhne', 'AG' vs 'Aktiengesellschaft', '3S' vs '3 S'.",
     "fix": "Umlauts typed out, 'und' / '&', legal forms written in full and moved spaces are recognised as the same name."},
]


def run_truth():
    for name in bench.SETS:
        r = bench.run_set(name, bench.TruthLLM())
        r.pop("rows", None)
        (REP / f"bench_{name}_truth.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
        print(f"{name}: {r['metrics']['decision_accuracy']:.1%}  errors {r['metrics']['errors']}")


def summarize():
    out = {"sets": {}, "findings": FINDINGS}
    for name in bench.SETS:
        data = bench.load(name)
        first = json.loads((REP / f"bench_{name}_truth_firstlook.json").read_text(encoding="utf-8"))
        now = json.loads((REP / f"bench_{name}_truth.json").read_text(encoding="utf-8"))
        layouts = {}
        for c in data["cases"]:
            for t in c.get("doc_truth", {}).values():
                k = t["layout"] + ("/de" if t["language"] == "de" else "")
                layouts[k] = layouts.get(k, 0) + 1
        out["sets"][name] = {
            "n": data["n"], "description": {
                "blind": "Written by a separate AI agent that never saw the code or rules: 56 scenarios with expected outcomes from business judgment.",
                "diverse": "60 vendors whose documents come in many shapes: tables, prose letters, multi-page, stamps, German, country date formats, phone photos, value-only edits.",
                "fresh": "44 vendors by a second separate AI agent (2026-11-12) told to avoid every pattern already covered: boundary dates, sister companies, German spellings, look-alike email domains, offshore payment. Scored once before any change.",
                "holdout": "37 vendors written by ChatGPT (a different AI company) from a description of the process only: no code, no rules. Scored once before any change."}[name],
            "document_styles": layouts,
            "first_look": first["metrics"], "first_look_failures": [{k: f[k] for k in ("id", "title", "expected", "got")} for f in first["failures"]],
            "after_fixes": now["metrics"]}
    live = REP / "bench_live.json"
    if live.exists():
        out["live"] = json.loads(live.read_text(encoding="utf-8"))
    (REP / "bench_summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("-> reports/bench_summary.json")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "build-blind":
        from synthetic import blind
        print(blind.build())
    elif cmd == "build-diverse":
        from synthetic import diverse
        print(diverse.build())
    elif cmd == "run":
        run_truth()
    elif cmd == "summarize":
        summarize()
