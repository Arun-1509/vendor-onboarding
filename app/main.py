"""HTTP API + live event stream.   uvicorn app.main:app --reload

Because the guide says the link must be 'live and runnable' (a stranger may try it),
this file also carries the abuse guards: upload size/type limits, per-IP rate limit and a
daily cap on runs so a public link cannot drain the API budget."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import defaultdict, deque

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import config, db, emailer, evaluation, pipeline, rules, service, workflow
from .llm import get_llm

def _refresh_samples_if_stale(max_age_days: int = 30):
    """Demo documents carry dates relative to when they were generated (bank letters expire
    after 90 days), so a long-running deployment regenerates them instead of drifting stale."""
    cases = config.ROOT / "samples" / "cases.json"
    if cases.exists() and time.time() - cases.stat().st_mtime < max_age_days * 86400:
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_samples", config.ROOT / "scripts" / "make_samples.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.main()
    except Exception as e:                      # never block startup on demo data
        print(f"sample refresh skipped: {e}")


@asynccontextmanager
async def _lifespan(_app):
    _refresh_samples_if_stale()
    c = db.connect()
    if c.execute("SELECT COUNT(*) n FROM vendors").fetchone()["n"] == 0:
        service.seed_vendor_master(c)
    c.close()
    yield


app = FastAPI(title="Vendor Onboarding", version="0.1", lifespan=_lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MAX_FILE_BYTES = 6 * 1024 * 1024
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
RATE_PER_HOUR = int(__import__("os").getenv("RATE_PER_HOUR", "30"))
DAILY_RUN_CAP = int(__import__("os").getenv("DAILY_RUN_CAP", "300"))
# Offline runs finish in ~100 ms; pause between stages so the live run view can be followed.
# Live-model runs are naturally slower, so no pause there. Labelled "paced" in the UI.
STAGE_PACE_MS = int(__import__("os").getenv("STAGE_PACE_MS", "550" if config.MOCK_LLM else "0"))
_hits: dict[str, deque] = defaultdict(deque)
_daily = {"day": "", "n": 0}
_bus: dict[str, dict] = {}
_bus_lock = threading.Lock()


def _conn():
    return db.connect()


def _guard(request: Request):
    ip = request.client.host if request.client else "?"
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= RATE_PER_HOUR:
        raise HTTPException(429, "Rate limit reached for this demo (per hour). Please try again later.")
    q.append(now)
    day = time.strftime("%Y-%m-%d")
    if _daily["day"] != day:
        _daily.update(day=day, n=0)
    if _daily["n"] >= DAILY_RUN_CAP:
        raise HTTPException(429, "Daily demo capacity reached. Please try again tomorrow.")
    _daily["n"] += 1


# ---------------------------------------------------------------- running ----
def _run_in_background(sid: str):
    with _bus_lock:
        _bus[sid] = {"events": [], "done": False}

    pace = STAGE_PACE_MS / 1000.0

    def emit(e):
        _bus[sid]["events"].append(e)
        if pace and e.get("type") == "stage_end":      # offline runs take ~0.1 s; pace them so people can follow
            time.sleep(pace)

    def work():
        conn = _conn()
        try:
            pipeline.process_submission(conn, sid, get_llm(), emit)
        except Exception as e:                      # last-resort guard: tell the UI, never hang
            emit({"type": "run_failed", "error": str(e)[:300]})
        finally:
            _bus[sid]["done"] = True
            conn.close()

    threading.Thread(target=work, daemon=True).start()


def _gemini_models():
    from .llm import GeminiLLM
    g = GeminiLLM()
    return {"extract": g.models_for(config.GEMINI_MODEL), "judge": g.models_for(config.GEMINI_JUDGE_MODEL),
            "available": len(g.available_models()), "recent_calls": GeminiLLM.trace}


@app.get("/api/health")
def health():
    return {"ok": True, "llm_mode": "mock" if config.MOCK_LLM else "live", "stage_pace_ms": STAGE_PACE_MS,
            "provider": config.PROVIDER_LABEL,
            "models": (_gemini_models() if config.PROVIDER == "gemini" and not config.MOCK_LLM else {"extract": config.MODEL_EXTRACT, "judge": config.MODEL_JUDGE, "write": config.MODEL_WRITE})}


@app.post("/api/submissions")
async def submit(request: Request, form: str = Form(...), vendor_id: str | None = Form(None),
                 tax_cert: UploadFile | None = File(None), bank_proof: UploadFile | None = File(None),
                 incorporation: UploadFile | None = File(None)):
    _guard(request)
    try:
        raw = json.loads(form)
        assert isinstance(raw, dict)
    except Exception:
        raise HTTPException(400, "`form` must be a JSON object")
    paths = {}
    for name, up in (("tax_cert", tax_cert), ("bank_proof", bank_proof), ("incorporation", incorporation)):
        if up is None or not up.filename:
            continue
        ext = "." + up.filename.rsplit(".", 1)[-1].lower() if "." in up.filename else ""
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"{name}: only PDF or image files are accepted")
        data = await up.read()
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{name}: file larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        paths[name] = service.store_upload(vendor_id or "new", f"{name}{ext}", data)
    conn = _conn()
    vid, sid = service.create_submission(conn, raw, paths, source="web", vendor_id=vendor_id)
    conn.close()
    _run_in_background(sid)
    return {"vendor_id": vid, "submission_id": sid}


@app.get("/api/samples")
def samples():
    return [{"id": c["id"], "group": c["group"], "title": c["title"], "form": c["form"], "has_follow_up": "follow_up" in c}
            for c in evaluation.load_cases()]


@app.post("/api/samples/{case_id}/submit")
def submit_sample(case_id: str, request: Request, follow_up: bool = False, vendor_id: str | None = None):
    """Run a prepared sample (its documents are already on disk). Used by the demo's 'load sample' button."""
    _guard(request)
    case = next((c for c in evaluation.load_cases() if c["id"] == case_id), None)
    if not case:
        raise HTTPException(404, "unknown sample")
    conn = _conn()
    if follow_up and case.get("follow_up"):
        fu = case["follow_up"]
        vid, sid = service.create_submission(conn, {**case["form"], **fu.get("form_overrides", {})},
                                             evaluation._abs(fu.get("add_docs", {})), source="sample-resubmit", vendor_id=vendor_id)
    else:
        vid, sid = service.create_submission(conn, case["form"], evaluation._abs(case["docs"]), source="sample")
    conn.close()
    _run_in_background(sid)
    return {"vendor_id": vid, "submission_id": sid}


@app.get("/api/submissions/{sid}/events")
async def events(sid: str):
    async def gen():
        i, waited = 0, 0.0
        while True:
            bus = _bus.get(sid)
            if bus is None:
                if waited > 3:
                    yield {"event": "error", "data": json.dumps({"error": "unknown submission"})}
                    return
                await asyncio.sleep(0.1)
                waited += 0.1
                continue
            while i < len(bus["events"]):
                e = bus["events"][i]
                i += 1
                yield {"event": e["type"], "data": json.dumps(e, default=str)}
            if bus["done"] and i >= len(bus["events"]):
                yield {"event": "end", "data": "{}"}
                return
            await asyncio.sleep(0.1)
    return EventSourceResponse(gen())


# ------------------------------------------------------------------ reads ----
@app.get("/api/vendors")
def vendors(status: str | None = None, q: str | None = None):
    c = _conn()
    sql, args = "SELECT * FROM vendors WHERE 1=1", []
    if status:
        sql += " AND status=?"; args.append(status)
    if q:
        sql += " AND (legal_name LIKE ? OR vendor_id LIKE ? OR tax_id LIKE ?)"; args += [f"%{q}%"] * 3
    rows = [db.row(r) for r in c.execute(sql + " ORDER BY updated_at DESC", args)]
    now = db.now(c)
    for r in rows:
        r.pop("token", None)
        r.pop("account_key", None)
        reason = "Existing approved vendor"
        if r.get("latest_run_id"):
            res = c.execute("SELECT result FROM runs WHERE run_id=?", (r["latest_run_id"],)).fetchone()
            res = json.loads(res["result"]) if res and res["result"] else {}
            titles = [rules.RULEBOOK[code]["title"] for code in res.get("driving", []) if code in rules.RULEBOOK]
            reason = "; ".join(dict.fromkeys(titles)) or ("All checks passed" if r["status"] == "approved" else "")
        elif r["status"] == "new":
            reason = "Not processed yet"
        if r["substatus"] in ("rejected_closed", "rejected_manual"):
            reason = {"rejected_closed": "No response from vendor", "rejected_manual": "Rejected by a reviewer"}[r["substatus"]]
        r["reason"] = reason
        r["overdue"] = bool(r["substatus"] == "awaiting_vendor" and r.get("due_at") and service._parse(r["due_at"]) <= now)
        r.pop("data", None)
    c.close()
    return rows


@app.get("/api/vendors/{vendor_id}")
def vendor_detail(vendor_id: str):
    c = _conn()
    v = db.row(c.execute("SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)).fetchone())
    if not v:
        raise HTTPException(404, "vendor not found")
    v["portal_path"] = f"/portal/{v['token']}"        # reviewers can open/copy the vendor's response link
    v.pop("token", None)
    v.pop("account_key", None)
    out = {"vendor": v,
           "submissions": [db.row(r) for r in c.execute("SELECT * FROM submissions WHERE vendor_id=? ORDER BY version", (vendor_id,))],
           "runs": [db.row(r) for r in c.execute("SELECT * FROM runs WHERE vendor_id=? ORDER BY run_id", (vendor_id,))],
           "emails": [db.row(r) for r in c.execute("SELECT * FROM emails WHERE vendor_id=? ORDER BY id", (vendor_id,))],
           "overrides": [db.row(r) for r in c.execute("SELECT * FROM overrides WHERE vendor_id=? ORDER BY id", (vendor_id,))],
           "audit": [db.row(r) for r in c.execute("SELECT seq,at,event,payload,hash FROM audit WHERE vendor_id=? ORDER BY seq", (vendor_id,))]}
    c.close()
    return out


@app.get("/api/runs")
def recent_runs(limit: int = 15):
    c = _conn()
    rows = []
    for r in c.execute("""SELECT r.run_id, r.vendor_id, r.submission_id, r.status, r.substatus, r.risk_score, r.llm_mode,
                          r.started_at, r.finished_at, v.legal_name, v.country, s.source, s.version
                          FROM runs r LEFT JOIN vendors v ON v.vendor_id=r.vendor_id LEFT JOIN submissions s ON s.submission_id=r.submission_id
                          WHERE r.status!='running' ORDER BY r.run_id DESC LIMIT ?""", (min(limit, 100),)):
        rows.append(dict(r))
    c.close()
    return rows


@app.post("/api/quality/spotcheck")
def quality_spotcheck(request: Request, n: int = 12):
    """Validate on vendors generated RIGHT NOW (random seed), scored against their labels."""
    import random as _r
    import tempfile
    from pathlib import Path as _P
    from synthetic.generator import SCENARIOS, Generator
    from . import quality
    _guard(request)
    n = max(1, min(n, 30))
    seed = _r.randint(1, 10**9)
    with tempfile.TemporaryDirectory() as tmp:
        g = Generator(seed, _P(tmp))
        seeds = json.loads((config.DATA_DIR / "seed_vendors.json").read_text())["vendors"]
        g.used_names |= {v["form"]["legal_name"].lower() for v in seeds}
        rows = []
        for i in range(n):
            sc = _r.Random(seed + i).choice(LIVE_SCENARIOS)
            res = getattr(g, f"s_{sc}")(f"spot-{i}")
            st, sub = SCENARIOS[sc][1]
            case = {"id": f"spot-{i}", "scenario": sc, "title": SCENARIOS[sc][4] + (f" ({res['note']})" if res.get("note") else ""),
                    "country": res["form"]["country"], "form": res["form"], "docs": {k: str(v) for k, v in res["docs"].items()},
                    "expect": {"status": st, "substatus": sub}}
            rows.append(quality.run_case(case, config.DATA_DIR / "seed_vendors.json", get_llm()))
    return {"seed": seed, "n": n, "correct": sum(r["decision_ok"] for r in rows), "rows": rows}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    c = _conn()
    r = db.row(c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
    if not r:
        raise HTTPException(404, "run not found")
    r["steps"] = [db.row(s) for s in c.execute("SELECT * FROM run_steps WHERE run_id=? ORDER BY seq", (run_id,))]
    c.close()
    return r


class Override(BaseModel):
    status: str
    reason: str
    actor: str = "reviewer"


@app.post("/api/vendors/{vendor_id}/override")
def override(vendor_id: str, body: Override):
    c = _conn()
    try:
        return service.reviewer_override(c, vendor_id, body.status, body.reason, body.actor)
    except KeyError:
        raise HTTPException(404, "vendor not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        c.close()


class Review(BaseModel):
    action: str
    reason: str
    actor: str = "reviewer"
    checklist_done: bool = False
    asks: list[str] = []


@app.get("/api/review/queue")
def review_queue():
    c = _conn()
    try:
        return workflow.review_queue(c)
    finally:
        c.close()


@app.post("/api/vendors/{vendor_id}/review")
def review(vendor_id: str, body: Review):
    c = _conn()
    try:
        return workflow.review_action(c, vendor_id, body.action, body.actor, body.reason, body.checklist_done, body.asks)
    except KeyError:
        raise HTTPException(404, "vendor not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        c.close()


@app.get("/api/audit/export")
def audit_export(vendor_id: str | None = None):
    """Full, verifiable audit trail as JSON lines: what an auditor would be handed."""
    c = _conn()
    sql, args = "SELECT * FROM audit", []
    if vendor_id:
        sql += " WHERE vendor_id=?"; args.append(vendor_id)
    lines = [json.dumps(db.row(r), default=str) for r in c.execute(sql + " ORDER BY seq", args)]
    c.close()
    name = f"audit_{vendor_id or 'all'}.jsonl"
    return PlainTextResponse("\n".join(lines), media_type="application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/quality")
def quality_summary():
    p = config.ROOT / "reports" / "summary.json"
    if not p.exists():
        raise HTTPException(404, "No quality report yet. Run scripts/validate.py and scripts/summarize_quality.py.")
    return json.loads(p.read_text(encoding="utf-8"))


# ===================================================== robustness benchmarks (blind + diverse documents) ====
_bench = {"state": "idle", "set": "", "done": 0, "total": 0, "recent": [], "results": {}, "error": "", "started": 0.0}


def _bench_live_file():
    return config.VAR_DIR / "bench_live.json"


@app.get("/api/benchmark")
def benchmark_summary():
    """Offline first-look / after-fix numbers (committed report) + the latest live run, if any."""
    p = config.ROOT / "reports" / "bench_summary.json"
    out = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"sets": {}, "findings": []}
    lf = _bench_live_file()
    if lf.exists():
        out["live"] = json.loads(lf.read_text(encoding="utf-8"))
    job = {k: v for k, v in _bench.items() if k != "results"}
    job["seconds"] = round(time.time() - _bench["started"], 1) if _bench["started"] else 0
    out["job"] = job
    return out


@app.post("/api/admin/benchmark")
def benchmark_run(sets: str = "blind,diverse", pace: float = 12.0, ids: str = "", x_admin_key: str | None = Header(None)):
    """Run the benchmarks through the LIVE model, paced under the free-tier rate limit. Admin only:
    it uses API quota (~3 model calls per vendor)."""
    if config.ADMIN_KEY and x_admin_key != config.ADMIN_KEY:
        raise HTTPException(403, "Wrong or missing admin key.")
    if config.MOCK_LLM:
        raise HTTPException(409, "No model key is configured, so there is no live model to benchmark.")
    if _bench["state"] == "running":
        return {"started": False, "reason": "already running"}
    from . import bench
    names = [s for s in sets.split(",") if s in bench.SETS]
    only = {x.strip() for x in ids.split(",") if x.strip()} or None
    total = sum(len([c for c in bench.load(n)["cases"] if not only or c["id"] in only]) for n in names)
    _bench.update(state="running", set="", done=0, total=total, recent=[], results={}, ai_errors_in_a_row=0,
                  error="", started=time.time())

    def work():
        try:
            llm = get_llm()
            for n in names:
                _bench["set"] = n

                def prog(i, total, row):
                    _bench["done"] += 1
                    _bench["recent"] = (_bench["recent"] + [{"set": n, "id": row["id"], "ok": row["decision_ok"],
                                                             "expected": row["expected"], "got": row["got"]}])[-400:]
                    _bench["ai_errors_in_a_row"] = _bench.get("ai_errors_in_a_row", 0) + 1 if "STAGE_ERROR" in row.get("codes", []) else 0

                def quota_gone():                      # 3 vendors in a row with no AI answer: stop, don't burn hours
                    return _bench.get("ai_errors_in_a_row", 0) >= 3
                if only and not any(c["id"] in only for c in bench.load(n)["cases"]):
                    continue
                r = bench.run_set(n, llm, pace_s=max(0.0, pace), progress=prog, ids=only, should_stop=quota_gone)
                r.pop("rows", None)
                _bench["results"][n] = r
                _bench_live_file().parent.mkdir(parents=True, exist_ok=True)
                _bench_live_file().write_text(json.dumps({"provider": config.PROVIDER_LABEL, "finished": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                                                          "sets": _bench["results"]}, indent=1), encoding="utf-8")
                if quota_gone():
                    _bench.update(state="error", error="Stopped: the AI stopped answering 3 vendors in a row (daily free quota used up). Results so far are saved; try again after the quota resets.")
                    return
            _bench["state"] = "done"
        except Exception as e:                                  # report, never crash the server
            _bench.update(state="error", error=str(e)[:300])

    threading.Thread(target=work, daemon=True).start()
    return {"started": True, "total": _bench["total"], "sets": names}


@app.get("/api/rulebook")
def rulebook():
    return {"auto_approve_below_risk": config.AUTO_APPROVE_MAX_RISK,
            "thresholds": {"bank letter max age (days)": config.BANK_PROOF_MAX_AGE_DAYS, "names treated as the same at core similarity >=": config.NAME_EQUIVALENT,
                           "names go to the AI judge between": f"{config.NAME_GREY_LOW} and {config.NAME_EQUIVALENT}", "lookalike vendor at similarity >=": config.LOOKALIKE_THRESHOLD,
                           "sanctions: auto-reject only on": "exact name / alias (any order, accents, legal form ignored)", "sanctions: human review at >=": config.SANCTIONS_REVIEW,
                           "AI name judgment accepted at confidence >=": config.LLM_MIN_CONFIDENCE, "vendor response window (days)": config.VENDOR_RESPONSE_DAYS,
                           "reminders before closing": config.MAX_REMINDERS, "days between reminders": config.REMINDER_GAP_DAYS},
            "rules": list(rules.RULEBOOK.values())}


@app.get("/api/stats")
def stats():
    c = _conn()
    now = db.now(c)
    vs = [dict(r) for r in c.execute("SELECT * FROM vendors WHERE latest_run_id IS NOT NULL")]   # vendors the pipeline has processed
    by: dict[str, int] = {}
    for v in vs:
        by[v["substatus"]] = by.get(v["substatus"], 0) + 1
    handled = sum(1 for v in vs if v["substatus"] in ("approved", "awaiting_vendor", "rejected_compliance", "rejected_closed"))
    by_day: dict[str, dict] = {}
    for r in c.execute("SELECT substr(started_at,1,10) d, substatus s, COUNT(*) n FROM runs WHERE status!='running' GROUP BY d, s"):
        by_day.setdefault(r["d"], {})[r["s"]] = r["n"]
    overdue = sum(1 for v in vs if v["substatus"] == "awaiting_vendor" and v["due_at"] and service._parse(v["due_at"]) <= now)
    secs = c.execute("SELECT AVG((julianday(finished_at)-julianday(started_at))*86400.0) s, COUNT(*) n FROM runs WHERE status!='running'").fetchone()
    out = {"vendors_total": c.execute("SELECT COUNT(*) n FROM vendors").fetchone()["n"], "processed": len(vs), "by_substatus": by,
           "approved": by.get("approved", 0), "awaiting_vendor": by.get("awaiting_vendor", 0),
           "awaiting_reviewer": by.get("awaiting_reviewer", 0),
           "rejected": sum(n for k, n in by.items() if k.startswith("rejected")), "overdue": overdue,
           "handled_without_human": handled, "handled_pct": round(100 * handled / len(vs)) if vs else 0,
           "runs": secs["n"], "avg_run_seconds": round(secs["s"] or 0, 1), "runs_by_day": by_day,
           "est_hours_saved": round(handled * config.ESTIMATED_MANUAL_MINUTES / 60, 1),
           "manual_minutes_assumption": config.ESTIMATED_MANUAL_MINUTES,
           "awaiting_second_approval": by.get("awaiting_second_approval", 0),
           "review_overdue": sum(1 for q in workflow.review_queue(c) if q["overdue"]),
           "clock_offset_days": db.clock_offset_days(c), "llm_mode": "mock" if config.MOCK_LLM else "live", "provider": config.PROVIDER_LABEL}
    c.close()
    return out


@app.get("/api/audit/verify")
def audit_verify():
    c = _conn()
    r = db.verify_audit(c)
    c.close()
    return r


# ===================================================== run lookup (so a reload still works) ====
@app.get("/api/submissions/{sid}/run")
def submission_run(sid: str):
    c = _conn()
    r = db.row(c.execute("SELECT * FROM runs WHERE submission_id=? ORDER BY run_id DESC LIMIT 1", (sid,)).fetchone())
    sub = db.row(c.execute("SELECT vendor_id, version, docs, form, source FROM submissions WHERE submission_id=?", (sid,)).fetchone())
    if sub:
        sub["documents"] = sorted((sub.pop("docs") or {}).keys())
        sub["legal_name"] = (sub.pop("form") or {}).get("legal_name", "")
    if r and r["status"] != "running":
        r["steps"] = [db.row(x) for x in c.execute("SELECT * FROM run_steps WHERE run_id=? ORDER BY seq", (r["run_id"],))]
        r["emails"] = [db.row(x) for x in c.execute("SELECT * FROM emails WHERE run_id=? ORDER BY id", (r["run_id"],))]
    c.close()
    return {"submission": sub, "run": r if r and r["status"] != "running" else None, "live": sid in _bus and not _bus[sid]["done"]}


# ===================================================== the waiting loop ====
class Clock(BaseModel):
    days: int = 7


@app.post("/api/admin/clock")
def advance_clock(body: Clock):
    """Demo control: pretend `days` have passed, then run the waiting loop (reminders / closing)."""
    c = _conn()
    if not 0 < body.days <= 60:
        raise HTTPException(422, "days must be between 1 and 60")
    off = db.advance_clock(c, body.days)
    db.audit(c, "clock_advanced", {"days": body.days, "offset_days": off})
    actions = service.tick(c)
    c.close()
    return {"clock_offset_days": off, "actions": actions}


@app.post("/api/admin/tick")
def run_tick():
    c = _conn()
    a = service.tick(c)
    c.close()
    return {"actions": a}


@app.post("/api/admin/reset")
def reset(x_admin_key: str | None = Header(None)):
    if config.ADMIN_KEY and x_admin_key != config.ADMIN_KEY:
        raise HTTPException(403, "Wrong or missing admin key.")
    c = _conn()
    n = service.reset_demo(c)
    c.close()
    _bus.clear()
    return {"ok": True, "seeded_vendors": n}


# ===================================================== vendor-facing response page ====
_portal_sid: dict[str, str] = {}      # token -> submission currently being checked


@app.get("/api/portal/{token}")
def portal(token: str):
    c = _conn()
    sid = _portal_sid.get(token)
    running = bool(sid and sid in _bus and not _bus[sid]["done"])
    v = service.portal_view(c, token, running)
    c.close()
    if not v:
        raise HTTPException(404, "This link is not valid.")
    return v


@app.post("/api/portal/{token}/respond")
async def portal_respond(token: str, request: Request, form: str = Form("{}"),
                         tax_cert: UploadFile | None = File(None), bank_proof: UploadFile | None = File(None),
                         incorporation: UploadFile | None = File(None)):
    _guard(request)
    c = _conn()
    v = db.row(c.execute("SELECT * FROM vendors WHERE token=?", (token,)).fetchone())
    if not v:
        raise HTTPException(404, "This link is not valid.")
    if v["substatus"] != "awaiting_vendor":
        raise HTTPException(409, "This request is not waiting for anything from you.")
    try:
        changes = json.loads(form)
        assert isinstance(changes, dict)
    except Exception:
        raise HTTPException(400, "`form` must be a JSON object")
    paths = {}
    for name, up in (("tax_cert", tax_cert), ("bank_proof", bank_proof), ("incorporation", incorporation)):
        if up is None or not up.filename:
            continue
        ext = "." + up.filename.rsplit(".", 1)[-1].lower() if "." in up.filename else ""
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"{name}: only PDF or image files are accepted")
        data = await up.read()
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{name}: file larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        paths[name] = service.store_upload(v["vendor_id"], f"{name}{ext}", data)
    merged = {**service._latest_form(c, v["vendor_id"]), **{k: val for k, val in changes.items() if k in service.FORM_KEYS}}
    vid, sid = service.create_submission(c, merged, paths, source="vendor-portal", vendor_id=v["vendor_id"])
    c.close()
    _portal_sid[token] = sid
    _run_in_background(sid)
    return {"ok": True, "submission_id": sid}


# ===================================================== emails ====
@app.get("/api/emails")
def emails():
    c = _conn()
    rows = [db.row(r) for r in c.execute(
        "SELECT e.*, v.legal_name FROM emails e LEFT JOIN vendors v ON v.vendor_id=e.vendor_id ORDER BY e.id DESC LIMIT 200")]
    c.close()
    return rows


@app.post("/api/emails/{email_id}/send")
def send_email(email_id: int):
    c = _conn()
    r = emailer.send_to_test_inbox(c, email_id)
    c.close()
    if not r["ok"]:
        raise HTTPException(503, r["error"])
    return r


# ===================================================== evaluation scorecard ====
_eval = {"state": "idle", "done": 0, "total": 0, "results": [], "llm_mode": "", "started": 0.0}


@app.post("/api/eval/run")
def eval_run(confirm_live: bool = False):
    if _eval["state"] == "running":
        return {"started": False, "reason": "already running"}
    if not config.MOCK_LLM and not confirm_live:
        raise HTTPException(409, "Live mode: this runs every labelled vendor through the real model and costs API credit. Confirm to continue.")
    cases = evaluation.load_cases()
    llm = get_llm()
    _eval.update(state="running", done=0, total=len(cases), results=[], llm_mode=llm.mode, started=time.time())

    def work():
        try:
            for case in cases:
                _eval["results"].append(evaluation.run_case(case, llm))
                _eval["done"] += 1
            _eval["state"] = "done"
        except Exception as e:
            _eval.update(state="error", error=str(e)[:300])

    threading.Thread(target=work, daemon=True).start()
    return {"started": True, "total": len(cases)}


@app.get("/api/eval/status")
def eval_status():
    return {**_eval, "passed": sum(1 for r in _eval["results"] if r["passed"]),
            "seconds": round(time.time() - _eval["started"], 1) if _eval["started"] else 0}


# ===================================================== synthetic: a fresh, unseen vendor ====
LIVE_SCENARIOS = ["clean", "clean_scan", "variant_legal_form", "variant_trade_name", "variant_abbreviation", "ocr_misread",
                  "branch_gstin", "missing_field", "missing_doc", "wrong_doc", "stale_bank_letter", "taxid_typo", "bank_typo",
                  "account_vs_letter", "holder_is_person", "blurry_scan", "pan_entity_mismatch", "pan_name_mismatch",
                  "cert_state_mismatch", "country_contradiction", "bank_code_mismatch", "tampered_bank_letter",
                  "sanctions_near", "sanctions_exact", "sanctions_word_overlap"]


@app.get("/api/synthetic/scenarios")
def synthetic_scenarios():
    from synthetic.generator import SCENARIOS
    return [{"id": k, "title": SCENARIOS[k][4], "expected": SCENARIOS[k][1][1]} for k in LIVE_SCENARIOS]


@app.post("/api/synthetic/submit")
def synthetic_submit(request: Request, scenario: str = "random"):
    """Generate a brand-new vendor the system has never seen (random names, IDs, documents),
    run it, and return the label so the UI can show 'expected vs decided'."""
    import random as _r
    from synthetic.generator import SCENARIOS, Generator
    _guard(request)
    sc = _r.choice(LIVE_SCENARIOS) if scenario == "random" else scenario
    if sc not in LIVE_SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    seed = _r.randint(1, 10**9)
    out = config.VAR_DIR / "synthetic_live"
    g = Generator(seed, out)
    c = _conn()
    g.used_names |= {r["legal_name"].lower() for r in c.execute("SELECT legal_name FROM vendors")}
    cid = f"live-{seed}"
    res = getattr(g, f"s_{sc}")(cid)
    docs = {k: str(v) for k, v in res.get("docs", {}).items()}
    vid, sid = service.create_submission(c, res["form"], docs, source=f"synthetic:{sc}")
    c.close()
    _run_in_background(sid)
    st, sub = SCENARIOS[sc][1]
    return {"vendor_id": vid, "submission_id": sid, "scenario": sc, "title": SCENARIOS[sc][4], "note": res.get("note", ""),
            "expected": sub, "seed": seed}


# ===================================================== files the UI needs ====
@app.get("/api/submissions/{sid}/doc/{doc_type}")
def submission_doc(sid: str, doc_type: str):
    """Serve a document that belongs to a submission (only paths recorded in the database)."""
    c = _conn()
    r = c.execute("SELECT docs FROM submissions WHERE submission_id=?", (sid,)).fetchone()
    c.close()
    path = json.loads(r["docs"]).get(doc_type) if r else None
    if not path or not __import__("pathlib").Path(path).exists():
        raise HTTPException(404, "no such document")
    return FileResponse(path)


@app.get("/api/sample-docs/{case_id}/{doc_type}")
def sample_doc(case_id: str, doc_type: str):
    case = next((c for c in evaluation.load_cases() if c["id"] == case_id), None)
    rel = (case or {}).get("docs", {}).get(doc_type) or ((case or {}).get("follow_up", {}).get("add_docs", {}).get(doc_type))
    if not rel:
        raise HTTPException(404, "no such sample document")
    return FileResponse(config.ROOT / rel)


@app.get("/api/docs/{name}")
def doc(name: str):
    allowed = {"assumptions": "ASSUMPTIONS.md", "design": "DESIGN.md", "demo": "DEMO_SCRIPT.md", "interview": "INTERVIEW_PREP.md"}
    if name not in allowed or not (config.ROOT / allowed[name]).exists():
        raise HTTPException(404, "unknown document")
    return PlainTextResponse((config.ROOT / allowed[name]).read_text())


STATIC = config.ROOT / "static"
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    @app.get("/portal/{token}")
    def index(token: str | None = None):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
