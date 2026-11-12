# Assumptions & limitations

The guide says to treat ambiguity as part of the exercise: make an assumption, write it down, move on. These are mine, so I can state them in the pitch instead of being asked.

## Scope
1. **Countries:** India, Germany, UK, US. Each has its own tax-ID, bank-ID and document rules. Adding a country means one validator + one row of required fields.
2. **Volume:** "dozens of vendors a quarter". So: a single process, SQLite, no queue. I chose depth of logic over scale.
3. **Required documents:** a tax registration document (GST certificate / VAT certificate / W-9) and a bank letter or cancelled cheque. Incorporation certificate is optional.

## What is real vs mocked
| Piece | Status |
|---|---|
| Document reading (PDF/image) | **Real** with Gemini on the live site (Claude also supported). Offline **mock** mode reads our generated sample PDFs; every run is labelled `mock` or `live`. If the AI is unavailable, text PDFs are read by a built-in reader only when every critical field is found, and the run says so. |
| Tax-ID and bank checks | **Real format + checksum** (GSTIN, PAN, German USt-IdNr, UK VAT, EIN prefix, IBAN mod-97, ABA, IFSC, SWIFT). **No registry lookup**, so a passing ID is *plausible*, not *verified with the government*. |
| Bank-code directories | **Demo subsets**: a few IFSC prefixes, German BLZ codes and UK sort-code ranges. Production uses the full RBI IFSC master, the Bundesbank BLZ file and the UK EISCD. An unknown code is simply not checked (never a false alarm). |
| PIN → GST state map | Two-digit PIN prefixes mapped to every state they touch, because postal circles don't follow state lines exactly. That makes it conservative: it can miss some mismatches, but it won't invent one. |
| Document integrity | **Real**: reads the PDF's own text layer and file structure. It catches pasted-over values; it cannot catch a document retyped from scratch. |
| Sanctions screening | **Mock list of three FICTIONAL entities.** Real deployment = a maintained feed (OFAC / UN / EU / UK). Fuzzy matching logic is real. |
| Existing vendors | Seeded demo vendor master. |
| Bank-account ownership | Checked against the bank letter's printed holder name. **No bank API** (penny-drop / account verification) is called. |
| ERP | The approved vendor becomes a **vendor master record** (JSON / CSV download). No live ERP connection; loading it is an import, not a rebuild. |
| Email | Drafted and stored in an outbox; **not sent to real addresses** from the public demo. Sending to a verified test inbox is a switch, not a rebuild. |

## Policy decisions (opinions I'd defend)
- **Only an EXACT sanctions match auto-rejects** (same name once case, accents, punctuation, legal form and word order are ignored, or a listed alias). A one-letter difference can be evasion or an innocent company, so it goes to compliance. Changed in v2: the original auto-rejected fuzzy matches at 93% similarity.
- **Four-eyes:** approving a bank change, a shared account, a lookalike, an edited document, someone else's tax ID or a sanctions near-match needs a ticked checklist and two different reviewers.
- **A head office using a branch's GST registration is normal**, so it is recorded but not blocking. A certificate whose *own* address is in a different state from its GSTIN is inconsistent and goes to review.
- **Scan misreads are reconciled only on scans**, and only where a look-alike letter sits where the form has the matching digit. A letter there can't be a valid value, so this can't hide a genuinely different account.
1. **Only compliance hits are auto-rejected.** Everything that merely *looks* like fraud (lookalike name, shared bank account, bank change, entity-type contradiction) is held for a **human**, because suspicion is not proof and a wrongful rejection has a business cost. Automation approves the clean, asks vendors for the fixable, and escalates the suspicious.
2. **"Pending" has two meanings**, shown separately: *awaiting vendor* (fixable; an email goes out) and *awaiting reviewer* (a person decides; **nothing is disclosed to the vendor**).
3. **The vendor is never told why they were screened.** Vendor emails are checked by code for fraud/sanctions/risk language and fall back to a safe template if the AI drifts. A sanctions rejection sends the vendor nothing.
4. **Bank-change requests from an already-approved vendor are the highest-risk event** (classic payment-fraud route). The recommended action is a call-back to the number already on file, never the number in the new submission. Until a reviewer clears it, payments should keep using the last *approved* bank details (stored as `approved_account_key`).
5. **The AI never decides.** It reads documents (and must quote the text it read), explains grey-zone name differences, and drafts messages. A rule table decides. If the AI is unavailable or a step crashes, the result is *human review*, never a guess and never an approval.
6. **Auto-approve requires risk < 20.** Several small flags can add up to a human look. Numbers live in `app/config.py` / `app/rules.py` and are shown in the UI.
7. **Bank letters older than 90 days are stale**; tax certificates don't expire.
8. **A name difference in legal-form wording** ("Pvt Ltd" vs "Private Limited") is not a problem. A **different legal form** ("Ltd" vs "LLP") is a different entity. An **extra or missing word** ("Kestrel Analytics" vs "Kestrel Analytics Technologies") is treated as possibly a *different company* and goes to the vendor for clarification.
9. **A vendor rejected for compliance is never silently re-opened**; a resubmission is screened as `PRIOR_REJECTED`.

## Known limitations (honest list)
- Only checks what is on the page; a skilled forger with a valid-looking GSTIN/IBAN would pass format checks, which is exactly why bank changes and lookalikes go to a human call-back.
- Director/UBO checks, adverse media, credit checks and insurance certificates: out of scope.
- The mock AI is deliberately conservative and simple, and the live model will behave differently. `scripts/live_check.py` measures live extraction (field agreement with the file text) and live decisions on a held-out sample.
- Validation data is synthetic, and I wrote both the generator and the rules. See VALIDATION.md for how the held-out and stress sets limit that, and for the first-look numbers.
- One reviewer role, no authentication on the demo. A real deployment needs SSO and roles.
- Time is simulated in the demo (clock offset) to show reminders; a real system would use a scheduler.
