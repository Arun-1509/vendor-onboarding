# Design: vendor onboarding, from submission to decision

## Who it's for
A procurement team onboarding a few dozen vendors a quarter, where a bad onboarding can mean payment fraud or a compliance breach, and where follow-up currently lives in someone's inbox.

## Principle
> **AI reads and explains. Code decides.**
> A model never approves, rejects or holds a vendor. A rule table does, and a procurement lead can read it.

## The process (what the live run view shows)

```
 Submission (form + tax doc + bank doc)
        │
 1 Intake ───────────── new vendor or update to an existing one? what changed since last time?
 2 Read documents ───── AI extracts fields AND quotes the text it read; code checks the quote
        │                is really in the document (blocks invented values). Unreadable / wrong
        │                document type → flagged.
 3 Completeness ─────── required fields + documents for that country
 4 Validate ─────────── tax-ID format + checksum, IBAN/IFSC/ABA/sort-code, email, document dates
 5 Cross-reference ──── form ↔ tax certificate ↔ bank letter ↔ each other:
        │                country vs tax ID vs bank vs currency · PAN entity type vs declared legal form
        │                IFSC bank vs stated bank · account holder vs legal/trade name
 6 Resolve names ────── only the GREY ZONE goes to the AI judge, with a recorded explanation
 7 Risk screening ───── sanctions list · lookalike of existing vendor · shared bank account
        │                same tax ID under another name · bank-change request · prior rejection
 8 Decision ─────────── rules.decide(): reject > hold-for-human > ask-vendor > risk-limit > approve
 9 Communicate ──────── vendor email (what's needed, nothing about screening) or reviewer note
        │                (what was flagged + the safe next step); every step → audit log
        ▼
 approved │ pending / awaiting vendor │ pending / awaiting reviewer │ rejected
```

## Why this order of precedence
| Outcome | When | Why |
|---|---|---|
| **rejected** | sanctions hit (built); request closed after no response (stage 2: needs the reminder scheduler) | Legal/hard rules only. Nothing is told to the vendor on a sanctions hit. |
| **pending → awaiting reviewer** | fraud-shaped evidence: lookalike, shared account, bank change, entity/country contradiction, sanctions near-miss, step failure, risk ≥ 20 | Suspicion isn't proof. A human verifies out-of-band. Vendor is *not* told. |
| **pending → awaiting vendor** | anything the vendor can fix: missing/unreadable/wrong/stale document, typo'd ID, name that needs explaining | Vendor gets a precise, numbered list with a link to respond. |
| **approved** | nothing blocking, risk < 20 | Fully explained, including the benign variants it noticed ("Pvt Ltd" = "Private Limited"). |

## Four deliberate edge cases
1. **Looks fine, isn't.** A lookalike of an approved vendor with a different tax ID and bank; a bank-change request from an approved vendor; a new vendor using an existing vendor's account. → held for a human with a call-back instruction.
2. **Looks wrong, isn't.** Legal-form wording differences, a declared trade name on the bank account, a "Fab" vs "Fabrication" abbreviation judged by the AI with a recorded reason. → approved, with the reasoning visible. Plus a **negative** twin: an *extra word* is not waved through.
3. **Wait and resume.** Incomplete submission → email asks only for what's missing → vendor responds → the same vendor record gets a new version → the decision shows exactly which findings were **resolved**. *(Resubmission + resolved-findings are built and tested; the vendor response page and overdue reminders are stage 2.)*
4. **Subtle contradiction.** A GSTIN whose embedded PAN is an *individual's* while the vendor claims to be a Pvt Ltd; an IFSC whose bank differs from the named bank; a form that says Germany while the tax ID, IFSC and account are Indian. Each field is individually well-formed; only cross-referencing reveals it.

## Trust features (what makes it more than a form validator)
- **Grounded extraction:** AI must quote the text it read; code verifies the quote exists in the PDF text layer.
- **Hash-chained audit log:** editing or deleting history breaks verification (`/api/audit/verify`). Replaces "whatever's in someone's inbox".
- **Failure = human, never a guess:** a crashed step or unavailable AI produces a `STAGE_ERROR` hold.
- **Mandatory-reason overrides**, logged.
- **Vendor-email guardrail:** code scans every AI-drafted vendor email for fraud/sanctions/risk language and swaps in a safe template if found.
- **Evaluation suite:** 26 labelled vendors, run against the live pipeline (`python scripts/run_eval.py`), including mutation tests that corrupt a clean vendor and assert it is no longer auto-approved.

## Stack
FastAPI + SQLite, Server-Sent Events for the live run view, Claude via the Anthropic API (models set per job in `.env`), React front end (stage 3). No Zamp or Pace products.
