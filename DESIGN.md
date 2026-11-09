# Design

## Who it is for
A procurement team onboarding dozens of vendors a quarter. A bad onboarding can mean payment fraud or a compliance breach, and today the follow-up lives in someone's inbox.

## Principle
**The AI reads and explains. Code decides. People handle the genuinely ambiguous.**
A model never approves, rejects or holds a vendor. A rule table does, and a procurement lead can read it and change it.

## The process (the live run view)
```
 Submission: form + tax certificate + bank letter
 1 Intake ─────────── new vendor, or a new version of one we know? what changed?
 2 Read documents ─── AI extracts fields AND quotes the text it used; code checks the quote is in the file.
     (AI)             Code also reads the RAW PDF: a field printed twice with different values plus an
                      incremental save = a value pasted over another (DOC_CONFLICTING_VALUES).
 3 Completeness ───── required fields and documents for the country
 4 Validate ───────── tax-ID format + check digits (GSTIN, USt-IdNr, UK VAT, EIN), IBAN mod-97, IFSC,
                      ABA, sort code, email, document age
 5 Cross-reference ── form ↔ certificate ↔ bank letter ↔ bank codes ↔ registration state:
                      country vs tax ID vs bank · PAN entity type vs legal form · PAN initial vs name ·
                      GSTIN state vs certificate address · bank code vs bank name · UK IBAN vs sort code/account ·
                      account holder vs legal / trade name · scan misreads reconciled (scans only)
 6 Resolve names ──── only grey-zone name pairs go to the AI judge, with a written reason
     (AI)
 7 Risk screening ─── sanctions (auto-reject only on an exact match) · lookalike of an existing vendor
                      (incl. padded names) · shared bank account · bank-change request · same tax ID under
                      another name · prior compliance rejection
 8 Decision ───────── rules.decide(): reject > hold for reviewer > ask vendor > risk limit > approve
 9 Communicate ────── vendor email (only fixable items, scanned for internal terms) or reviewer note
     (AI drafts)      (evidence + checklist + safe next step); everything goes into the hash-chained audit
```

## Outcomes and who sees what
| Outcome | When | Vendor sees | Reviewer sees |
|---|---|---|---|
| **Rejected** | Exact sanctions / alias match; or no reply after two reminders | Nothing about screening | The match; escalated to compliance |
| **Pending: needs review** | Fraud-shaped but not proven | "Being reviewed" | Evidence, checklist, safe next step, 48 h SLA |
| **Pending: waiting on vendor** | Fixable: missing / wrong / stale / blurry document, typo | A numbered list and a private link | The same |
| **Approved** | Nothing blocking, risk < 20 | Welcome email | Harmless variants recorded |

## Edge cases (the ones I would demo)
1. **Looks fine, isn't.**
   - Pattern: lookalike names (including "+ India"), a bank-change request from an approved vendor, a bank account shared with an approved vendor.
   - Outcome: held for review with a call-back instruction (to the number *on file*). Approval needs two reviewers.
2. **Documents that lie.**
   - Pattern: a genuine bank letter with a new account number pasted over the old one. The page looks perfect, but the raw PDF still contains the original. Or: a valid GSTIN whose PAN belongs to a company starting with a different letter.
   - Outcome: held for review, with the edited line shown in red on the document.
3. **Looks wrong, isn't.**
   - Pattern: "Pvt Ltd" vs "Private Limited", a declared trade name on the bank account, "Engg" for "Engineering", a scan that reads 0 as O.
   - Outcome: approved, with the variant recorded.
   - Negative twin: an *extra word* in the certificate name is treated as possibly a different company.
4. **Wait and resume.**
   - Pattern: the bank letter is missing.
   - Outcome: the vendor gets an exact request. The reply becomes version 2, the system shows "Resolved: MISSING_DOC", and the vendor is approved. With no reply: two reminders, then closed.

## Human workflow
- **Next actions** on every decision: the concrete path to approval (`app/workflow.py`, no AI).
- **Review queue:** 48-hour SLA, separate procurement and compliance queues, risk-ordered.
- **Reviewer actions:** approve, reject, or ask the vendor. A reason is mandatory.
  - High-risk approvals (bank change, shared account, lookalike, edited document, someone else's tax ID, sanctions near-match) need the checklist ticked **and a second, different reviewer**.
  - "Ask vendor" is scanned for internal terms (fraud, sanctions, risk…), so a reviewer can't leak screening by accident.

## Trust features
- **Grounded extraction:** the AI must quote what it read, and code verifies the quote.
- **Document integrity from the raw file:** it doesn't depend on the AI.
- **Failure means a human:** a crashed step or an AI outage becomes `STAGE_ERROR`, a hold. Never a guess.
- **Hash-chained audit log:** `/api/audit/verify`, plus export.
- **Measured, not claimed:** 1,900 labelled synthetic vendors, with a held-out set and a stress set. See [VALIDATION.md](VALIDATION.md).

## Stack
- **Backend:** FastAPI + SQLite, with Server-Sent Events for the live run view.
- **AI:** Claude or Gemini (models set per job in `.env`), with an offline mode.
- **UI:** plain JavaScript, no build step; pdf.js for document rendering.
- **Hosting:** one Docker container. No Zamp or Pace products.

## Why rules and not a trained classifier
- **Every decision must be explainable to a non-technical buyer and to an auditor**, and each rule here is a line they can read.
- **The costly errors are rare events** (a forged letter, a sanctioned party), and labelled real examples of them are scarce.
- **Policy changes ("bank letters now expire after 60 days") are a one-line edit, not a retrain.**
- **Where judgment is genuinely fuzzy (names), the AI judges, but only the grey zone, and with a written reason.**
