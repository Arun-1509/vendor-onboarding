"""Collect the quality reports into reports/summary.json (read by the app's Model quality page)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = ROOT / "reports"
KEYS = ["n", "decision_accuracy", "strict_accuracy", "macro_f1", "unsafe_approval_rate", "straight_through_rate", "weighted_cost", "errors"]


def slim(name):
    p = R / f"{name}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    m = d["metrics"]
    return {**{k: m[k] for k in KEYS}, "confusion": m["confusion"], "per_class": m["per_class"],
            "per_scenario": {k: {kk: vv for kk, vv in v.items() if kk != "failed_ids"} for k, v in m["per_scenario"].items()},
            "llm_mode": d.get("llm_mode")}


def main():
    out = {"splits": {}}
    for split, desc in (("dev", "Tuning set: rules were improved while looking at this one"),
                        ("test", "Held-out set: same generator, different seed; never used for tuning"),
                        ("stress", "Stress set: variations the rules were NOT designed for (written before measuring)")):
        out["splits"][split] = {"description": desc, "baseline": slim(f"quality_{split}_baseline"), "v2": slim(f"quality_{split}_v2"),
                                "first_look": slim(f"quality_{split}_v2_firstlook")}
    out["notes"] = [
        "Labels come from the scenario that generated each vendor, never from the pipeline.",
        "The held-out 'first look' is the honest number: one generic bug it revealed was fixed afterwards.",
        "Offline mode simulates the document reader. Live-model extraction accuracy is measured separately (scripts/live_check.py).",
    ]
    (R / "summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("wrote reports/summary.json")


if __name__ == "__main__":
    main()
