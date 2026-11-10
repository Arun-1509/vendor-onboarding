"""Robustness benchmarks (bench/<set>/cases.json): the blind set and the diverse-documents set.

Two readers:
  TruthLLM : a PERFECT reader. It reports exactly what is printed (from the ground truth saved
             when the document was drawn), with dates normalised to YYYY-MM-DD as the real model
             is told to. Isolates the rules: "if reading were perfect, is the decision right?"
  live     : the real model (Gemini / Claude). Measures reading + rules together, plus field-level
             reading accuracy against the ground truth.

Each case runs in its own in-memory database seeded with the set's vendor master, with the clock
set to the day the documents were generated (so a 70-day-old letter stays 70 days old).
"""
from __future__ import annotations

from .validators import dates as dt_
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

from . import config, db, pipeline, quality, service
from .evaluation import check
from .llm import MockLLM

ROOT = config.ROOT
BENCH = ROOT / "bench"
SETS = ["blind", "diverse", "holdout"]
FIELD_KEYS = {"tax_cert": ["legal_name", "tax_id", "address", "issue_date"],
              "bank_proof": ["account_holder", "bank_name", "account_number", "iban", "ifsc", "sort_code", "routing_number", "issue_date"]}


def load(name: str) -> dict:
    return json.loads((BENCH / name / "cases.json").read_text(encoding="utf-8"))


def _abs(rel: str) -> str:
    return str(ROOT / rel) if rel and not Path(rel).is_absolute() else rel


def _key(p: str) -> str:
    return str(Path(p).resolve()).lower()


class TruthLLM(MockLLM):
    """Perfect reader: returns the printed values (ground truth) for registered documents."""
    mode = "truth"

    def __init__(self):
        self.truth: dict[str, dict] = {}

    def register(self, case: dict):
        for slot, rel in case["docs"].items():
            t = (case.get("doc_truth") or {}).get(slot)
            if t:
                self.truth[_key(_abs(rel))] = t

    def extract_document(self, doc_type: str, path: str) -> dict:
        t = self.truth.get(_key(path))
        if t is None:
            return super().extract_document(doc_type, path)
        from synthetic.layouts import doc_type_of
        t = {**t, "doc_type": doc_type_of(t["kind"])}
        if not t["legible"]:
            return {"doc_type_detected": t["doc_type"], "legible": False, "fields": {}, "notes": "blurry"}
        fields = {}
        for k, v in t["printed"].items():
            if k == "issue_date":
                fields[k] = {"value": t.get("issued_iso") or v, "evidence": v, "confidence": 0.95}
            elif k != "trade_name":
                fields[k] = {"value": v, "evidence": v, "confidence": 0.95}
        return {"doc_type_detected": t["doc_type"], "legible": True, "fields": fields, "notes": ""}


class RecordingLLM:
    """Wraps a live model and keeps what it extracted from each file (for field accuracy)."""

    def __init__(self, inner):
        self.inner, self.mode, self.seen = inner, inner.mode, {}

    def extract_document(self, doc_type, path):
        out = self.inner.extract_document(doc_type, path)
        self.seen.setdefault(_key(path), out)          # score the first read
        return out

    def __getattr__(self, name):
        return getattr(self.inner, name)


def run_case(case: dict, master_file: Path, llm, generated_for: str) -> dict:
    conn = db.connect(":memory:")
    try:
        service.seed_vendor_master(conn, master_file)
        offset = (date.fromisoformat(generated_for) - datetime.now(timezone.utc).date()).days
        if offset:
            db.advance_clock(conn, offset)
        docs = {k: _abs(v) for k, v in case["docs"].items() if v}
        _, sid = service.create_submission(conn, case["form"], docs, source="bench")
        t0 = time.time()
        res = pipeline.process_submission(conn, sid, llm)
        ms = int((time.time() - t0) * 1000)
    finally:
        conn.close()
    exp, got = quality._cls(case["expect"]["substatus"]), quality._cls(res["substatus"])
    return {"id": case["id"], "scenario": case.get("scenario", case.get("group", "")), "country": case.get("country"),
            "title": case["title"], "expected": exp, "got": got, "decision_ok": exp == got,
            "strict_ok": exp == got and not check(res, case["expect"]), "fails": check(res, case["expect"]),
            "risk": res["risk_score"], "ms": ms, "difficulty": case.get("difficulty", ""),
            "codes": [f["code"] for f in res["findings"] if f["action"] != "none" or f["points"]],
            "why_expected": case.get("why", "")}


_norm = lambda v: re.sub(r"[^A-Z0-9]", "", str(v or "").upper())  # noqa: E731


def field_accuracy(cases: list[dict], seen: dict) -> dict:
    """Compare what the live model read with what is printed (ground truth)."""
    ok = n = 0
    errors, by_layout = [], {}
    for c in cases:
        for slot, t in (c.get("doc_truth") or {}).items():
            got = seen.get(_key(_abs(c["docs"][slot])))
            if got is None or not t["legible"] or t["doc_type"] != slot:
                continue
            lay = f"{t['layout']}/{t['language']}"
            bl = by_layout.setdefault(lay, [0, 0])
            for k in FIELD_KEYS[slot]:
                if k not in t["printed"]:
                    continue
                want = t["issued_iso"] if k == "issue_date" else t["printed"][k]
                gv = ((got.get("fields") or {}).get(k) or {}).get("value")
                n += 1
                bl[1] += 1
                same_date = k == "issue_date" and gv and dt_.grounded_date(want, str(gv))
                if _norm(gv) == _norm(want) or same_date:
                    ok += 1
                    bl[0] += 1
                elif len(errors) < 40:
                    errors.append({"case": c["id"], "doc": slot, "layout": lay, "field": k, "printed": t["printed"][k], "read": gv})
    return {"fields_checked": n, "fields_correct": ok, "accuracy": round(ok / n, 4) if n else None,
            "by_layout": {k: {"correct": a, "n": b} for k, (a, b) in sorted(by_layout.items())}, "errors": errors}


def run_set(name: str, llm, *, pace_s: float = 0.0, progress=None, limit: int | None = None,
            ids: set | None = None, should_stop=None) -> dict:
    """`ids` reruns only those cases; `should_stop()` ends the run early (e.g. the daily AI quota ran out)."""
    data = load(name)
    cases = [c for c in data["cases"] if not ids or c["id"] in ids]
    cases = cases[:limit] if limit else cases
    import tempfile
    rec = RecordingLLM(llm) if llm.mode == "live" else llm
    if isinstance(llm, TruthLLM):
        for c in cases:
            llm.register(c)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        mf = Path(tmp) / "master.json"
        mf.write_text(json.dumps({"vendors": data["master"]}), encoding="utf-8")
        for i, c in enumerate(cases):
            t0 = time.time()
            rows.append(run_case(c, mf, rec, data["generated_for"]))
            if progress:
                progress(i + 1, len(cases), rows[-1])
            if should_stop and should_stop():
                break
            if pace_s and i + 1 < len(cases):
                time.sleep(max(0.0, pace_s - (time.time() - t0)))
    cases = cases[:len(rows)]
    out = {"set": name, "reader": llm.mode, "n": len(rows), "metrics": quality.score(rows),
           "failures": [r for r in rows if not r["decision_ok"]], "rows": rows}
    if llm.mode == "live":
        out["provider"] = config.PROVIDER_LABEL
        out["reading"] = field_accuracy(cases, rec.seen)
    return out
