# Onboard: vendor onboarding, from submission to decision (PS-2)

A vendor submits a form, a tax certificate and a bank letter. The process reads the documents, cross-checks every source against the others, screens the vendor, and decides: **approved**, **pending** (waiting on the vendor, or held for a reviewer) or **rejected**. Every decision shows its reasoning. For anything not approved it says exactly what is needed, and it writes the message.

> **Principle: the AI reads and explains; code decides; people handle the genuinely ambiguous.**

**Highlights**
- **9-stage live run view.** Each stage streams in as it executes. The source document is shown with the evidence highlighted: green where a value was verified in the file, red where a value was pasted over another.
- **Measured quality:**
  - Held-out test (first look): **99.9% accuracy, 0 unsafe approvals**. The original engine scored 86.3% with 48 unsafe approvals.
  - Stress set of variations the rules weren't built for (first look): 91.7%, against 65.0% for the original.
  - Full detail is in [VALIDATION.md](VALIDATION.md) and on the in-app **Model quality** page.
- **Catches what eyeballing misses:**
  - an edited PDF bank letter;
  - a valid GSTIN that belongs to someone else (PAN initial, state code);
  - a bank code that belongs to a different bank;
  - a padded lookalike name ("Globex Logistics Solutions **India**");
  - a request to change bank details;
  - a shared bank account;
  - a near-match on the sanctions list.
- **Reviewer workflow:**
  - a queue with a 48-hour SLA and a checklist for each risk type;
  - **four-eyes approval** for high-risk cases;
  - re-routing to the vendor with a guardrail against leaking internal terms.
- **Vendor portal.** The vendor sees only what to fix, never the risk or the screening.
- **Waiting loop.** Reminders, then closure if there's no reply. Resubmissions become new versions, and the system shows which findings were resolved.
- **Audit trail.** Hash-chained (tamper-evident), with JSON-lines export.

## Run it locally (no API key needed)
```bash
pip install -r requirements.txt
python scripts/make_samples.py                 # demo-pack documents (dates are relative to today)
uvicorn app.main:app --port 8000               # http://localhost:8000
```
Without a key it runs in **offline mode**: document reading is simulated, and the app labels every run with that. Add a free `GEMINI_API_KEY` (aistudio.google.com) or an `ANTHROPIC_API_KEY` to use a real model, then run `python scripts/live_check.py`.

## Test and validate
```bash
pytest -q                                      # 91 tests
python scripts/run_eval.py                     # 29 hand-made labelled cases (the demo pack)
python scripts/synth.py --split test --n 800 --seed 29 && python scripts/validate.py test
```

## Deploy (public link for the submission)
Render, Railway or Fly: one Docker container, no other services. See [DEPLOY.md](DEPLOY.md) for click-by-click steps.

## Where to look
| | |
|---|---|
| `app/pipeline.py` | the 9 stages shown in the live run view |
| `app/rules.py` | the rulebook and `decide()`: the only place a status is decided |
| `app/workflow.py` | next actions, review queue, four-eyes, re-route to the vendor |
| `app/validators/` | tax IDs, bank codes, names, document integrity (pure functions, unit-tested) |
| `app/llm.py` | the AI's three jobs (read, judge names, draft emails), for Claude, Gemini and offline |
| `synthetic/` | labelled synthetic vendor generator (37 + 9 stress scenarios) |
| `app/quality.py`, `scripts/validate.py` | measurement harness and reports |
| `static/` | the UI (plain JS, no build step; pdf.js from a CDN for document rendering) |

Docs:
- [DESIGN.md](DESIGN.md): why it is built this way
- [ASSUMPTIONS.md](ASSUMPTIONS.md)
- [VALIDATION.md](VALIDATION.md)
- [DEMO_SCRIPT.md](DEMO_SCRIPT.md): the video and the live demo
- [INTERVIEW_PREP.md](INTERVIEW_PREP.md)
