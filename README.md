# Onboard: vendor onboarding, from submission to decision

**Live:** https://vendor-onboarding-0i9u.onrender.com (free hosting: the first visit can take about a minute to wake up)

A vendor submits a form, a tax certificate and a bank letter. The process reads the documents, cross-checks every source against the others, screens the vendor and decides: **approved**, **pending** (waiting on the vendor, or held for a reviewer) or **rejected**. Every decision shows its reasoning. For anything not approved it says exactly what is needed and writes the message. An approved vendor gets a vendor master record ready for the ERP.

> **The AI reads and explains. Code decides. People handle what is genuinely ambiguous.**

## Try it in two minutes
Open **New submission → Start here**. Five steps, each run live through the real process:

| | Case | Outcome | What it shows |
|---|---|---|---|
| 1 | Happy path | Approved | Nine stages stream in; values are highlighted where they were found in the document |
| 2 | Payment-fraud attempt | Needs review | An approved vendor "changes" its bank account; held for two reviewers, the vendor is not told why |
| 3 | A document that lies | Needs review | A new account number pasted over the original in the PDF, shown with a red box |
| 4 | Missing document, then the reply | Waiting on vendor → Approved | One precise request; the reply becomes version 2 and only the failed check is re-run |
| 5 | Looks wrong, is fine | Approved | "Fab" vs "Fabrication": the AI explains, the rulebook accepts, no false alarm |

Then: **Overview** (history and status across runs), **Review queue** (checklists, 48-hour SLA, four-eyes), **Model quality** (how it was tested), **Rulebook** (every rule, in plain words). Or enter your own vendor and documents.

## How it decides
- **AI (Gemini) does three jobs:** read each document and quote the text it used, judge an ambiguous pair of names, and draft the emails. Code checks every quote is really in the file, and checks the emails never mention fraud or screening.
- **Code does the rest:** check digits (GSTIN, VAT, EIN, IBAN, IFSC, sort code, routing number), cross-references (form vs certificate vs bank letter vs bank code vs registration state), PDF edit detection, sanctions and lookalike screening against the existing vendors.
- **One rule table decides:** reject > hold for a reviewer > ask the vendor > risk limit > approve. Fraud-shaped findings go to a person, never back to the vendor.
- **If the AI is down,** a text PDF is read by a built-in reader only when every critical field is found, and the run says so. Anything else waits for a person. Nothing is ever approved on a guess.

## How well it works (honest version)
- On **my own** 800 held-out test vendors: 99.9%. I wrote that data, so it is the easy number.
- On cases **written blind by other AI models** that never saw the code, first try, before any fix: **62.5%, 78.4% and 70.5%**; a set of messy documents (photos, German, stamps): 81.7%. Every gap they found was fixed with a general rule, then checked on 133 freshly generated vendors (132 pass) so the fixes are not fitted to the test cases.
- With live Gemini reading the real files: 113 of 115 decisions correct, 99% of printed fields read exactly.

Details: [VALIDATION.md](VALIDATION.md).

## Run it locally (no API key needed)
```bash
pip install -r requirements.txt
python scripts/make_samples.py          # demo documents (dates are relative to today)
uvicorn app.main:app --port 8000        # http://localhost:8000
pytest -q                               # 116 tests
```
Without a key it runs in **offline mode** and labels every run that way. Add `GEMINI_API_KEY` (free at aistudio.google.com) for the real model. Deployment: [DEPLOY.md](DEPLOY.md).

## Where to look
| | |
|---|---|
| `app/pipeline.py` | the nine stages of a run |
| `app/rules.py` | the rulebook and `decide()`, the only place a status is decided |
| `app/workflow.py` | next steps, review queue, four-eyes, reviewer checklists |
| `app/validators/` | tax IDs, bank codes, names, dates, PDF edit detection |
| `app/llm.py` | the AI's three jobs, for Gemini, Claude and offline |
| `static/` | the UI (plain JavaScript, no build step) |
| `synthetic/`, `bench/`, `scripts/` | test-data generators, the blind test sets, measurement scripts |

[DESIGN.md](DESIGN.md) explains the design choices; [ASSUMPTIONS.md](ASSUMPTIONS.md) lists what I assumed where the brief was open.
