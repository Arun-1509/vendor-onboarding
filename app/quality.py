"""Model-quality measurement on labelled synthetic datasets.

Every case runs through the REAL pipeline in its own fresh in-memory database, seeded with
that dataset's vendor master. We then score the decision against the label.

Metrics are chosen for what goes wrong in vendor onboarding, in order of cost:
  unsafe_approvals   label says 'do not approve' but the system approved   (fraud / compliance loss)
  disclosure_leaks   label says 'human review' but the system emailed the vendor what to fix
                     (tells a fraudster which detail to forge next)
  missed_rejects     a sanctioned party not rejected
  false_holds        a clean vendor not approved                               (friction)
  over_escalations   a fixable gap sent to a reviewer instead of the vendor     (friction)
"""
from __future__ import annotations

import json
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import config, db, pipeline, service
from .evaluation import check

ROOT = config.ROOT
CLASSES = ["approved", "awaiting_vendor", "awaiting_reviewer", "rejected"]
COST = {"unsafe_approvals": 50, "missed_rejects": 50, "disclosure_leaks": 10, "over_escalations": 2, "false_holds": 1, "other": 1}


def _cls(sub: str) -> str:
    return "rejected" if sub.startswith("rejected") else sub


def load(path: Path) -> dict:
    return json.loads((Path(path) / "cases.json").read_text(encoding="utf-8"))


def run_case(case: dict, master_file: Path, llm) -> dict:
    conn = db.connect(":memory:")
    try:
        service.seed_vendor_master(conn, master_file)
        docs = {k: str(ROOT / v) for k, v in case["docs"].items() if v}
        _, sid = service.create_submission(conn, case["form"], docs, source="quality")
        t0 = time.time()
        res = pipeline.process_submission(conn, sid, llm)
        ms = int((time.time() - t0) * 1000)
    finally:
        conn.close()
    fails = check(res, case["expect"])
    exp, got = _cls(case["expect"]["substatus"]), _cls(res["substatus"])
    return {"id": case["id"], "scenario": case["scenario"], "country": case.get("country"), "title": case["title"],
            "expected": exp, "got": got, "decision_ok": exp == got, "strict_ok": not fails, "fails": fails,
            "risk": res["risk_score"], "ms": ms,
            "codes": [f["code"] for f in res["findings"] if f["action"] != "none" or f["points"]]}


def score(rows: list[dict]) -> dict:
    n = len(rows)
    conf = {e: {g: 0 for g in CLASSES} for e in CLASSES}
    err = Counter()
    for r in rows:
        conf[r["expected"]][r["got"]] += 1
        e, g = r["expected"], r["got"]
        if e == g:
            continue
        if g == "approved":
            err["unsafe_approvals"] += 1
        elif e == "rejected":
            err["missed_rejects"] += 1
        elif e == "awaiting_reviewer" and g == "awaiting_vendor":
            err["disclosure_leaks"] += 1
        elif e == "approved":
            err["false_holds"] += 1
        elif e == "awaiting_vendor" and g == "awaiting_reviewer":
            err["over_escalations"] += 1
        else:
            err["other"] += 1
    by_exp = Counter(r["expected"] for r in rows)
    not_ok = n - by_exp["approved"]
    per_class = {}
    for c in CLASSES:
        tp = conf[c][c]
        pred = sum(conf[e][c] for e in CLASSES)
        act = sum(conf[c].values())
        p = tp / pred if pred else 1.0
        rc = tp / act if act else 1.0
        per_class[c] = {"precision": round(p, 4), "recall": round(rc, 4), "f1": round(2 * p * rc / (p + rc), 4) if p + rc else 0.0,
                        "support": act}
    scen = defaultdict(lambda: {"n": 0, "decision_ok": 0, "strict_ok": 0, "failed_ids": []})
    for r in rows:
        s = scen[r["scenario"]]
        s["n"] += 1
        s["decision_ok"] += r["decision_ok"]
        s["strict_ok"] += r["strict_ok"]
        if not r["strict_ok"] and len(s["failed_ids"]) < 5:
            s["failed_ids"].append(r["id"])
    return {
        "n": n,
        "decision_accuracy": round(sum(r["decision_ok"] for r in rows) / n, 4),
        "strict_accuracy": round(sum(r["strict_ok"] for r in rows) / n, 4),
        "macro_f1": round(sum(v["f1"] for v in per_class.values()) / len(CLASSES), 4),
        "unsafe_approval_rate": round(err["unsafe_approvals"] / not_ok, 4) if not_ok else 0.0,
        "straight_through_rate": round(conf["approved"]["approved"] / by_exp["approved"], 4) if by_exp["approved"] else 0.0,
        "errors": {k: err.get(k, 0) for k in COST},
        "weighted_cost": sum(COST[k] * err.get(k, 0) for k in COST),
        "confusion": conf, "per_class": per_class,
        "per_scenario": dict(sorted(scen.items())),
        "median_ms": sorted(r["ms"] for r in rows)[n // 2] if rows else 0,
    }


def run_dataset(path: Path, llm, limit: int | None = None, progress=None) -> dict:
    data = load(path)
    cases = data["cases"][:limit] if limit else data["cases"]
    with tempfile.TemporaryDirectory() as tmp:
        mf = Path(tmp) / "master.json"
        mf.write_text(json.dumps({"vendors": data["master"]}), encoding="utf-8")
        rows = []
        for i, c in enumerate(cases):
            rows.append(run_case(c, mf, llm))
            if progress:
                progress(i + 1, len(cases))
    return {"split": data["split"], "seed": data["seed"], "llm_mode": llm.mode, "metrics": score(rows),
            "failures": [r for r in rows if not r["strict_ok"]][:200], "rows": rows}
