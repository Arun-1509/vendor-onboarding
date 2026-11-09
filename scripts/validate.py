"""Run a synthetic split through the pipeline and write a quality report.
   python scripts/validate.py dev                # -> reports/quality_dev.json
   python scripts/validate.py test --tag v2      # -> reports/quality_test_v2.json
   python scripts/validate.py test --show 20     # print 20 failures"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import quality  # noqa: E402
from app.llm import get_llm  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("split")
    ap.add_argument("--tag", default="")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--show", type=int, default=0)
    a = ap.parse_args()
    rep = quality.run_dataset(ROOT / "var" / "synth" / a.split, get_llm(), a.limit)
    m = rep["metrics"]
    out = ROOT / "reports" / f"quality_{a.split}{'_' + a.tag if a.tag else ''}.json"
    out.parent.mkdir(exist_ok=True)
    slim = {k: v for k, v in rep.items() if k != "rows"}
    out.write_text(json.dumps(slim, indent=1), encoding="utf-8")
    print(f"split={a.split} n={m['n']} mode={rep['llm_mode']}")
    print(f"  decision accuracy {m['decision_accuracy']:.1%} | strict (right reason) {m['strict_accuracy']:.1%} | macro-F1 {m['macro_f1']:.3f}")
    print(f"  unsafe approvals {m['errors']['unsafe_approvals']} ({m['unsafe_approval_rate']:.2%}) | missed rejects {m['errors']['missed_rejects']}"
          f" | disclosure leaks {m['errors']['disclosure_leaks']} | false holds {m['errors']['false_holds']}"
          f" | over-escalations {m['errors']['over_escalations']} | other {m['errors']['other']}")
    print(f"  straight-through (clean vendors auto-approved) {m['straight_through_rate']:.1%} | weighted cost {m['weighted_cost']}")
    print("  per scenario (strict ok / n):")
    for s, v in m["per_scenario"].items():
        flag = "" if v["strict_ok"] == v["n"] else "   <--"
        print(f"    {s:<24} {v['strict_ok']:>3}/{v['n']:<3} decision {v['decision_ok']}/{v['n']}{flag}")
    for f in rep["failures"][:a.show]:
        print(f"  FAIL {f['id']}: {f['title']}\n       expected {f['expected']} got {f['got']} codes={f['codes']}\n       {f['fails']}")
    print(f"report -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
