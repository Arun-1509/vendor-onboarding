"""Runs the labelled cases in samples/cases.json through the REAL pipeline, each in a fresh
database seeded with the same approved vendors, and compares to the expected outcome.
Used by `scripts/run_eval.py` and by the in-app Evaluation page."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from . import config, db, pipeline, service
from .llm import get_llm


def load_cases() -> list[dict]:
    return json.loads((config.ROOT / "samples" / "cases.json").read_text())["cases"]


def _abs(paths: dict) -> dict:
    return {k: str(config.ROOT / v) for k, v in paths.items() if v}


def check(res: dict, expect: dict) -> list[str]:
    fails = []
    codes = {f["code"] for f in res["findings"]}
    if "status" in expect and res["status"] != expect["status"]:
        fails.append(f"status {res['status']} != expected {expect['status']}")
    if "substatus" in expect and res["substatus"] != expect["substatus"]:
        fails.append(f"substatus {res['substatus']} != expected {expect['substatus']}")
    for c in expect.get("must_have", []):
        if c not in codes:
            fails.append(f"missing expected finding {c}")
    for c in expect.get("must_not_have", []):
        if c in codes:
            fails.append(f"unexpected finding {c}")
    if "max_risk" in expect and res["risk_score"] > expect["max_risk"]:
        fails.append(f"risk {res['risk_score']} > allowed {expect['max_risk']}")
    for r in expect.get("resolved_includes", []):
        if r not in res.get("resolved_since_last_run", []):
            fails.append(f"expected '{r}' to be resolved by the resubmission")
    return fails


def run_case(case: dict, llm=None) -> dict:
    llm = llm or get_llm()
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.connect(Path(tmp) / "eval.db")
        service.seed_vendor_master(conn)
        vid, sid = service.create_submission(conn, case["form"], _abs(case["docs"]), source="eval")
        res = pipeline.process_submission(conn, sid, llm)
        fails = check(res, case["expect"])
        steps = [{"expect": case["expect"], "status": res["status"], "substatus": res["substatus"], "risk": res["risk_score"],
                  "codes": [f["code"] for f in res["findings"] if f["action"] != "none" or f["points"]], "fails": fails}]
        fu = case.get("follow_up")
        if fu:
            form2 = {**case["form"], **fu.get("form_overrides", {})}
            _, sid2 = service.create_submission(conn, form2, _abs(fu.get("add_docs", {})), source="eval-resubmit", vendor_id=vid)
            res2 = pipeline.process_submission(conn, sid2, llm)
            f2 = check(res2, fu["expect"])
            steps.append({"expect": fu["expect"], "status": res2["status"], "substatus": res2["substatus"], "risk": res2["risk_score"],
                          "codes": [f["code"] for f in res2["findings"] if f["action"] != "none" or f["points"]],
                          "resolved": res2.get("resolved_since_last_run"), "fails": f2})
        conn.close()
    return {"id": case["id"], "group": case["group"], "title": case["title"],
            "passed": all(not s["fails"] for s in steps), "steps": steps, "seconds": round(time.time() - t0, 2)}


def run_all(llm=None) -> dict:
    llm = llm or get_llm()
    results = [run_case(c, llm) for c in load_cases()]
    return {"llm_mode": llm.mode, "total": len(results), "passed": sum(r["passed"] for r in results), "results": results}
