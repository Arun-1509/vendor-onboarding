# Vendor Onboarding: automated submission → decision

Takes a vendor submission (form + tax document + bank letter), reads and cross-checks it, screens it, and produces **approved / pending / rejected** with visible reasoning, a drafted message back to the vendor (or an internal note), and a tamper-evident audit trail.

It is a complete, runnable prototype: pipeline, rulebook, web UI (live run view, dashboard, vendor reply page, outbox, evaluation), a waiting loop with reminders and closure, and an audit trail you can verify.

**Start here:** open the app, go to *New submission*, pick a prepared vendor, press *Run it now*. Docs: `DESIGN.md` (why), `ASSUMPTIONS.md`, `DEPLOY.md` (public URL), `DEMO_SCRIPT.md` (video and live demo), `INTERVIEW_PREP.md` (code tour and likely questions).

## Run it (offline, no API key needed)
```bash
pip install -r requirements.txt
python scripts/make_samples.py          # sample PDFs + labelled cases (re-run before a demo: dates are relative to today)
python scripts/run_eval.py              # 26 labelled vendors through the pipeline → scorecard
pytest -q                               # 68 tests
uvicorn app.main:app --port 8000        # the app: http://localhost:8000
```
Without a key the app runs in **mock mode** (clearly labelled on every run). With a key it uses a real model: **Gemini (free key from aistudio.google.com)** or Claude, selected automatically.

## Use a real model
```bash
export GEMINI_API_KEY=...                # free: aistudio.google.com  (or ANTHROPIC_API_KEY for Claude)
python scripts/check_model.py            # confirms which models your account can call
python scripts/run_eval.py               # re-run the whole suite against live Claude, then READ any failures
```
Models are set per job in `.env.example`. Extraction uses a fast model (it's on the live-demo critical path); only the rare grey-zone name judgment uses the strongest.

## Try the API
```bash
curl -X POST localhost:8000/api/samples/E1b_bank_change_request/submit     # → {"submission_id": "S-00006", ...}
curl -N localhost:8000/api/submissions/S-00006/events                      # live stage-by-stage stream (SSE)
curl localhost:8000/api/vendors ; curl localhost:8000/api/stats ; curl localhost:8000/api/audit/verify
```
Real uploads: `POST /api/submissions` (multipart: `form` = JSON string, files `tax_cert`, `bank_proof`).

## Layout
```
app/pipeline.py      the 9 stages (what the live run view shows)
app/rules.py         RULEBOOK + decide(): the ONLY place a status is decided
app/llm.py           Claude client + offline mock (read docs, judge names, draft emails)
app/validators/      tax-ID, bank and name logic (pure, unit-tested)
app/screening.py     sanctions (fictional list), lookalikes, shared accounts
app/db.py            SQLite + simulated clock + hash-chained audit log
app/main.py          API, SSE stream, upload/rate/daily-cap guards
app/service.py       submissions, resubmission, waiting loop (reminders, closure), vendor portal
static/              the web UI: plain JS, no build step
DEPLOY.md  Dockerfile  render.yaml
samples/cases.json   26 labelled cases with expected outcomes
```

## Public-link safety
Upload size/type limits, per-IP rate limit (`RATE_PER_HOUR`), and a hard daily cap (`DAILY_RUN_CAP`) protect your API budget if the link is opened by strangers.
