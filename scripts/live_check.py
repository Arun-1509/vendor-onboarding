"""Validate the LIVE model path (needs GEMINI_API_KEY or ANTHROPIC_API_KEY).

The offline suite proves the rules. This proves the real document reader:
  1. extraction agreement: for every text-layer PDF, the live model's fields vs the exact text
     the file contains (read deterministically) -> field-level accuracy
  2. decisions: a stratified sample of the held-out test split, live model end to end

    python scripts/live_check.py --n 30          # ~30 cases x 2 documents; costs a little API credit
Writes reports/live_check.json (the Model quality page shows it when present).
"""
import argparse
import json
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, quality  # noqa: E402
from app.llm import MockLLM, get_llm, pdf_text  # noqa: E402

norm = lambda v: re.sub(r"[^A-Z0-9]", "", str(v or "").upper())  # noqa: E731


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--split", default="test")
    a = ap.parse_args()
    if config.MOCK_LLM:
        sys.exit("No API key found (GEMINI_API_KEY or ANTHROPIC_API_KEY). This script checks the live model.")
    llm, ref = get_llm(), MockLLM()
    data = quality.load(ROOT / "var" / "synth" / a.split)
    by = defaultdict(list)
    for c in data["cases"]:
        by[c["expect"]["substatus"]].append(c)
    rng = random.Random(7)
    per = max(1, a.n // len(by))
    sample = [c for cases in by.values() for c in rng.sample(cases, min(per, len(cases)))]
    print(f"live model: {config.PROVIDER_LABEL} | {len(sample)} cases from '{a.split}'")

    fields_ok = fields_n = 0
    field_errors = []
    for c in sample:                                   # 1. extraction agreement on text-layer PDFs
        for dtype, rel in c["docs"].items():
            p = str(ROOT / rel)
            if not p.endswith(".pdf") or not pdf_text(p).strip():
                continue
            truth = ref.extract_document(dtype, p)
            if truth["doc_type_detected"] != dtype:
                continue
            got = llm.extract_document(dtype, p)
            for k, v in truth["fields"].items():
                fields_n += 1
                gv = ((got.get("fields") or {}).get(k) or {}).get("value")
                if norm(gv) == norm(v["value"]):
                    fields_ok += 1
                elif len(field_errors) < 25:
                    field_errors.append({"case": c["id"], "doc": dtype, "field": k, "expected": v["value"], "got": gv})
    print(f"extraction: {fields_ok}/{fields_n} fields agree with the file's text ({fields_ok / max(1, fields_n):.1%})")

    import tempfile                                     # 2. end-to-end decisions with the live model
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        mf = Path(tmp) / "m.json"
        mf.write_text(json.dumps({"vendors": data["master"]}), encoding="utf-8")
        for i, c in enumerate(sample, 1):
            t0 = time.time()
            r = quality.run_case(c, mf, llm)
            rows.append(r)
            print(f"  [{'OK ' if r['decision_ok'] else 'BAD'}] {c['id']:<40} expected {r['expected']:<18} got {r['got']:<18} {time.time() - t0:.1f}s")
    m = quality.score(rows)
    print(f"decisions: {m['decision_accuracy']:.1%} | unsafe approvals {m['errors']['unsafe_approvals']} | disclosure leaks {m['errors']['disclosure_leaks']}")
    out = {"provider": config.PROVIDER_LABEL, "split": a.split, "n": len(sample), "extraction_field_accuracy": fields_ok / max(1, fields_n),
           "fields_checked": fields_n, "field_errors": field_errors, "metrics": m, "failures": [r for r in rows if not r["decision_ok"]]}
    (ROOT / "reports" / "live_check.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("report -> reports/live_check.json   (read every failure: a live-vs-mock difference is a real finding)")


if __name__ == "__main__":
    main()
