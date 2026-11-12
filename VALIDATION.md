# Validation: how I know it works

> Short version: 1,900 labelled synthetic vendors, three separate sets, run through the real pipeline.
> On the held-out set, accuracy goes from **86.3% to 99.9% (first look)**, and approvals that should not have happened go from **48 to 0**.

## Why synthetic data
The brief says to create our own inputs, and real vendor files would hold bank details and tax IDs I shouldn't have. So I wrote a generator (`synthetic/`) that builds vendors the way a fraudster or a busy vendor would:

1. Start from a **consistent** company. It gets checksum-valid IDs (GSTIN with the right PAN letters and state code, German USt-IdNr, UK VAT, US EIN), a valid IBAN / IFSC / sort code / routing number, and a matching bank.
2. Render its documents in several layouts: certificate, bank letterhead with prose, cancelled cheque, and phone scan.
3. Apply **one deliberate change** (two for combination cases). Each change is one of **37 scenarios**: clean, harmless variants, fixable gaps, fraud patterns and compliance hits.
4. The **label comes from the scenario**, never from the pipeline.

Generated per dataset: a vendor master of 25 approved / rejected vendors, so lookalikes, shared accounts and bank changes have something real to collide with.

## Three sets, used differently
| Set | Size | Purpose |
|---|---|---|
| **Dev** (seed 11) | 800 | I improved the rules while looking at this one. |
| **Test** (seed 29) | 800 | Held out. Same generator, different seed. Not used for tuning. |
| **Stress** (seed 47) | 300 | 9 scenario types written *before* measuring, deliberately outside what the rules were designed for: non-prefix abbreviations (Engg, Mfg), transliterated lookalikes (Shree→Shri), two OCR misreads, digitally signed PDFs, umlauts, cross-country impostors, an edited tax certificate, and two-letter sanctions misspellings. |

## What is measured, and why these numbers
Error types are weighted by what they cost in onboarding, not treated as equal:

| Error | Weight | Why |
|---|---|---|
| Unsafe approval (label says do not approve) | ×50 | Fraud or compliance loss |
| Missed sanctions reject | ×50 | Legal exposure |
| Disclosure leak (fraud-shaped case emailed to the vendor as "please fix") | ×10 | Tells a fraudster which detail to forge next |
| Over-escalation (fixable gap sent to a reviewer) | ×2 | Reviewer time |
| False hold (clean vendor not approved) | ×1 | Vendor friction |

"Strict" accuracy also requires the **right reason**, i.e. the expected rule codes must be among the findings.

## Results (offline mode; the original engine and v2 run on identical data)
| Set | Engine | Accuracy | Unsafe approvals | Disclosure leaks | False holds | Clean vendors auto-approved | Weighted cost |
|---|---|---|---|---|---|---|---|
| Test | original | 86.3% | **48** | 23 | 20 | 93.5% | 2,669 |
| Test | **v2, first look** | **99.9%** | **0** | **0** | 1 | 99.7% | 1 |
| Test | v2, after one fix | 100% | 0 | 0 | 0 | 100% | 0 |
| Stress | original | 65.0% | 1 | 38 | 58 | 66.7% | 496 |
| Stress | **v2, first look** | **91.7%** | 1 | 0 | 24 | 86.2% | 74 |
| Stress | v2, after three fixes | 100% | 0 | 0 | 0 | 100% | 0 |
| Dev | original → v2 | 86.9% → 100% | 43 → 0 | 29 → 0 | 20 → 0 | | 2,473 → 0 |

**The honest numbers are the first looks.** Each first run on the test and stress sets exposed real, general bugs, which I fixed, so the later 100% rows are no longer independent:
- **Test:** my new abbreviation dictionary expanded "Ind" to "industries", which broke the German "Industriebedarf".
- **Stress:**
  - "Supp1y" (a scan misread inside a name);
  - "Orioo Vale yetals" (sorted-token matching misaligned the words of a sanctioned name, so it was approved);
  - "Engg / Mfg" (standard abbreviations).

## What the original engine got wrong (and what v2 added)
| Gap found on the dev set | v2 change |
|---|---|
| Forged bank letters (new number pasted over a genuine one) were **emailed back to the vendor as a typo** | Reads the raw PDF: two values for one field plus an incremental save → `DOC_CONFLICTING_VALUES`, reviewer, red box on the document |
| Bank codes checked only for India | IBAN bank code (BLZ) for Germany, sort code and IBAN bank code for the UK, and a check that a UK IBAN contains the stated sort code and account number |
| A valid GSTIN belonging to another business passed | PAN 5th character vs the company's initial (`PAN_NAME_MISMATCH`) |
| A certificate whose address is in a different state from its GSTIN passed | PIN → state map (`GSTIN_STATE_MISMATCH`). A head office using a branch registration is recorded only (`GSTIN_BRANCH_STATE`) |
| "Aravali Cables **Services** Pvt Ltd" impersonating "Aravali Cables Pvt Ltd" was **approved** | Padded-name lookalike rule (generic padding words) |
| A one-letter misspelling of a sanctioned name was **auto-rejected** | Only an exact match (any order, accents, legal form ignored) auto-rejects; fuzzy matches go to compliance |
| Scan misreads (0↔O, 1↔I) caused false "account mismatch" requests | Reconciled **only on scans**, and only where a letter sits in a digit position (that can't be a valid value) |

## Reproduce
```bash
python scripts/synth.py --split dev --n 800 --seed 11
python scripts/synth.py --split test --n 800 --seed 29
python scripts/synth.py --split stress --n 300 --seed 47
python scripts/validate.py test --tag v2 --show 10
python scripts/summarize_quality.py          # -> reports/summary.json, shown on the Model quality page
```
In the app, **Model quality → Spot-check 4 fresh vendors now** generates brand-new vendors with a random seed and scores them live.

## Harder tests: a blind answer key and messy documents (2026-11-09, quota-hit vendors rerun 2026-11-10)
The 1,900 synthetic vendors share two weaknesses: I wrote both the generator and the rules, and every document has the same look. So I added two sets that don't (`bench/`, `python scripts/bench.py`):

| Set | What it is | First look | After fixes |
|---|---|---|---|
| **Blind answer key** (56) | Scenarios and expected outcomes written by an independent author (a separate AI agent) who was not allowed to see the code or rules. It was given only the business policy, the vendor master and the sanctions list. Its documents were rendered in varied layouts. | **62.5%**: 9 unsafe approvals, 1 missed sanctions reject | 100% |
| **Messy documents** (60) | The usual decisions, but every document is drawn differently: tables, prose letters, multi-page, stamps over text, German, country date styles, phone photos (skewed, shadowed, JPEG), value-only paste-overs. | **81.7%**: 11 unsafe approvals | 100% |

"First look" and "after fixes" both use a perfect reader (exactly what is printed) to isolate the rules. The live model is measured separately; see below.

**What the first look found (all of it was invisible to the 1,900-vendor set):**
1. **Dates.** A bank letter dated "10/07/2026" or "9 November 2026" never matched the ISO date the reader returns. Grounding dropped the date, and the 90-day check silently skipped it.
   - Fix: dates are compared as dates.
   - Fix: an undated letter now asks the vendor (`DOC_UNDATED`).
2. **Value-only edits.** Pasting over just the account *number* (not the whole "IBAN: …" line) was not detected.
   - Fix: two different texts drawn at the same point of a page are flagged, whatever the field.
3. **The payee was never screened.** A clean company asking to be paid into a sanctioned party's account was not rejected.
   - Fix: account holders are screened.
4. **A sanctioned entity's distinctive name** plus another industry word ("Meridian Crest Logistics") was approved.
   - Fix: such names go to compliance review.
   - Unchanged: "Orion Valley Bakery" is still approved.
5. **Third-party payees** (a factoring company, a director's personal account) were sent back to the vendor.
   - Fix: they now go to a reviewer, with four-eyes approval.
   - This is a **policy change**, and it changed my own earlier labels (the generator's holder-is-a-person scenario and demo case V3). I relabelled them openly rather than keep the old answer.
6. **A wholly different account** on the form versus the bank letter was treated as a typo.
   - Fix: a typo (one or two characters, swapped neighbours) asks the vendor; a different account goes to a reviewer.
   - Also fixed: a tax ID registered to a differently named company now goes to a reviewer (demo case V8 relabelled, consistent with E5b).
7. **False alarms on honest vendors.**
   - "M/s" prefix, "DBA" account titles, "UG (haftungsbeschränkt)" and swapped upload slots are now accepted as variants.
   - A company PAN with the wrong dropdown is now a vendor fix, not fraud.
8. **Documents and currency.**
   - A bank statement is not accepted as bank proof, so the vendor is asked for a letter.
   - A foreign currency on a domestic-only account asks the vendor to confirm.

**Unchanged after the fixes:**
- The dev, held-out and stress sets are still at 100% (0 unsafe approvals). The two over-escalations the fixes briefly introduced were traced to a name-containment bug and fixed.
- All 29 demo cases pass.
- All 101 tests pass, including `tests/test_robustness_fixes.py`.

**Honest caveat:** "after fixes = 100%" is measured on the same sets that revealed the gaps, so treat it as "the gaps are closed", not as an accuracy claim. The accuracy claim is the first look.

**Live model on these sets:** Model quality → *Run live benchmark* (admin key). It runs all 116 vendors through the deployed model, paced under the free-tier limit (about 25 minutes). It reports decision accuracy plus **reading accuracy**: each printed field read by the model, compared with the ground truth, by document style.

## Live AI check (real Gemini on the deployed site, 2026-11-09)
All 29 demo cases were run against the public URL with a real Gemini key.

**First look: 9/29.** Every document read failed, and each case safely went to a reviewer (`STAGE_ERROR`) instead of being guessed.
- **Cause:** Google had retired the configured model names for new keys.
- **Fix:** the app now asks the key which models it can use and picks the newest fast one (`gemini-flash-lite-latest`).

**Speed: about 4 minutes per case → 4–8 seconds.**
- Reasoning ("thinking") is set to low for extraction, because copying fields off a page needs none.
- The documents are read in parallel.
- A busy model is skipped rather than retried.

**One genuine difference between the live AI and the offline mode.**
- Gemini judged "Kestrel Analytics **Technologies** Pvt Ltd" to be the same company as "Kestrel Analytics Pvt Ltd" (90% confident).
- A code guardrail now overrides that: an extra whole word can be another registered company, so the vendor is asked instead.
- After the guardrail, the offline held-out and stress results were unchanged (0 unsafe approvals).

**Final full run: 26/29.**
- 2 failures were the free tier's ~15 requests/minute limit, because the test fired cases back to back. The app now waits the delay Google returns, up to 3 passes.
- 1 was correct behaviour on leftover test data: an earlier run had approved a near-identical name, so the case was flagged as a lookalike.
- Each AI call is logged with its timing under `models.recent_calls` at `/api/health`.

## Live AI benchmark: real Gemini on all 116 harder-test vendors (2026-11-09)

Run on the deployed site (free Gemini tier, ~3 model calls per vendor). Raw results: `reports/bench_live.json`.

| Set | Vendors answered | Live decisions correct | Dangerous errors |
|---|---|---|---|
| Blind answer key | 56 / 56 | **55 / 56 (98.2%)** | 1 (fixed, below) |
| Messy documents | 59 / 60 | **58 / 59 (98.3%)** | 0 |
| **Both** | **115 / 116** | **113 / 115 (98.3%)** | **0** |

- **Free-tier quota:** 34 messy-document vendors ran out of the free daily Gemini quota mid-run. Every one became STAGE_ERROR and was held for a reviewer. None was approved, so the fail-safe worked. A second full run the next day hit the quota even harder (65 vendors held, 0 approved). The 34 were then rerun on their own, paced at one vendor every 30 s; 33 got an answer and 1 (D019) still hit the quota and is left out of the accuracy rather than counted as a pass. The benchmark now stops itself after 3 unanswered vendors in a row instead of retrying for hours.
- **The one miss in the rerun (D041):** the bank letter was a deliberately unreadable phone photo; the expected outcome was asking the vendor for a clearer copy. Gemini guessed values from the blur instead of saying it could not read them; the guesses did not match the form, so the vendor went to a reviewer. Wrong queue, but the safe direction. Not patched: treating "photo + several mismatches" as "just ask for a clearer copy" would hand fraudsters an easy bypass (upload a blurry letter).
- **The one live miss (B27):** the model called a bank *statement* a bank letter, so it was accepted as bank proof. The perfect reader never made this mistake. **Fix:** code now double-checks the AI. A bank-proof file whose text says "statement of account", "opening/closing balance" or "Kontoauszug" is treated as a statement and the vendor is asked for a letter. On all 300+ benchmark and sample PDFs this matched only B27.
- **Reading accuracy:** 873 / 882 printed fields read exactly (99.0%). The 9 misses are four phone-photo address misreads in the rerun (a dropped or changed postcode digit, one garbled street) plus, in the first run, an address digit dropped in a table, and phone-photo misreads (a postcode letter, an address, a GSTIN with a stray prefix). The GSTIN one is caught by the checksum, so no bad ID gets through. The first scoring counted dates read exactly as printed (e.g. `09.12.2024`) as wrong because it wanted ISO form; that was a scoring bug, fixed in `app/bench.py`.

## Unseen test written by ChatGPT (2026-11-10)

To test generalisation with an author from a different AI company, ChatGPT was given only a description of the
process, the five existing vendors and the sanctions list (`bench/holdout/CHATGPT_PROMPT.md`), not the code or rules.
Its 37 cases and expected outcomes are kept verbatim (`bench/holdout/chatgpt_raw.json`). The only edit was mechanical:
its `{{TAX_ID}}`-style placeholders were replaced with checksum-valid numbers (`synthetic/holdout.py`).

| | Decisions correct | Unsafe approvals | Sanctions rejected |
|---|---|---|---|
| **First look (the number to quote)** | **29 / 37 (78.4%)** | 3 | 4 / 4 |
| After the general fixes below | 35 / 37 | 0 | 4 / 4 |

(An earlier 73% was a test-harness bug: ChatGPT copied existing vendors' account numbers but not their bank codes, and I filled random ones, so two loyal vendors looked like bank changes. Fixed in the harness, not the model.)

**What it found, and the general rule that now covers it**
- A lookalike of an existing vendor reused its tax ID and bank account but moved the contact to ProtonMail: approved. Now an existing vendor's contact moving to another email domain goes to a reviewer (`CONTACT_CHANGE`).
- A new company used an existing vendor's exact mailbox: approved. Now one mailbox shared by two companies goes to a reviewer (`CONTACT_SHARED`).
- An edited bank statement was only refused as the wrong document. Now every uploaded file gets the tamper check, including refused ones.
- "O'Connell" vs "OConnell", and "Brand, a trade name of Company LLC", were false holds. Apostrophes now join words; "a trade/trading/business name of X" resolves to X.
- "Kanalweg 19" on the form vs "Kanalweg 9" on the certificate: approved. Same postcode/city but another street number now asks the vendor to confirm (`ADDRESS_MISMATCH_DOC`). A different city is NOT flagged: the first version of this rule wrongly held 10 real branch-registration vendors in the dev/test sets, so it was narrowed before shipping.

**Kept as policy differences (2):** H05 "Meridian Crest Marine Services" shares the full distinctive part of a sanctioned name; ChatGPT would approve, we send it to compliance (screening practice: a human clears near-matches). H33's story says a new account number was emailed afterwards, which no form or document in the submission shows.

**Did the fixes memorise the 37 cases?** `scripts/generalization_check.py` builds 50 fresh vendors sharing nothing with the holdout (new names, all 4 countries, other phrasings), each rule with positives and near-miss negatives: **49/50**. The one miss is a seed-data quirk the system rightly flags (existing vendor Acme has a Karnataka GSTIN but a Mumbai address). Dev, test, stress, blind, messy-documents and the 29 demo cases all stay at 100%.

## Second unseen test, by a new independent author (2026-11-12)

A third author (a separate AI agent) was given only the process description (`bench/fresh/BRIEF.md`): no code, no rules,
no earlier test sets. It was told the obvious patterns were already covered and asked for 44 cases a previous author was
unlikely to have written. Its answer is kept verbatim (`bench/fresh/author_raw.json`); placeholders were filled with
checksum-valid numbers as before.

| | Decisions correct | Approved but should not have been | Fraud-shaped cases sent to the vendor | Honest vendors held |
|---|---|---|---|---|
| **First look (the number to quote)** | **31 / 44 (70.5%)** | 4 | 5 | 3 |
| After the general fixes below | 43 / 44 | 0 | 0 | 0 |

Sanctions: 6 / 6 rejected on the first look. Three of the four wrong approvals were form slips the vendor should have been
asked to fix (not fraud); one was a look-alike email domain.

**What it found, and the general rule that now covers it**
- **Approved with something plainly wrong.** 'LLC' selected for a company named '... Inc.'; a W-9 whose tax-number box was
  empty; a contact email with a comma in it. Now: the dropdown is checked against the legal form in the company's own name;
  a document must actually show the number it is meant to prove (`DOC_INCOMPLETE`); email addresses are validated properly.
- **A look-alike contact domain** ('carro11ton' for Carrollton) was approved. Now a domain that becomes a word of the company
  name once digits are read as letters goes to a reviewer (`EMAIL_LOOKALIKE_DOMAIN`). 'carrollton24.com' is not flagged.
- **Fraud-shaped cases went back to the vendor**, which tells a fraudster what to fix:
  - a bank letter dated in the future (it was treated like an old letter) -> reviewer (`DOC_FUTURE_DATED`);
  - two different *valid* tax IDs on form and certificate -> reviewer (`TAXID_DIFFERENT_DOC`). A keying slip fails the check
    digit, so two valid numbers cannot be a slip. The same PAN inside two GSTINs is the same company's other-state
    registration and stays a vendor fix;
  - a cheque in a sister company's name ('... Agro Foods' vs '... Agro Trading') -> third-party payee. A whole word swapped
    is another legal entity; a typo or an abbreviation is not;
  - a franchisee using the franchisor's tax certificate -> reviewer, when the registered name lacks a word of the vendor's
    name *and* is registered in another town (two independent signs);
  - an Indian supplier asking for dollars into a US account -> reviewer (`OFFSHORE_PAYMENT`): bank abroad *and* not the
    vendor's home currency.
- **Honest spellings were held.** 'Mueller und Soehne' vs 'Müller & Söhne', 'AG' vs 'Aktiengesellschaft', '3S' vs '3 S'
  are now the same name.

**Kept as a policy difference (1):** N22, a German company with German documents that picked 'United Kingdom' in the
dropdown. The author would ask the vendor; this system sends any country contradiction to a person. Wrong queue, safe direction.

**Two of the 13 misses were partly my test harness, not the rules** (fixed in `synthetic/holdout.py`, labels untouched): it
generated an Indian tax number with a digit where a letter must be (company name '3S ...'), and gave N22 British instead
of German numbers. Both cases still failed for a real reason, so the first-look score is unchanged.

**Side effects, caught by re-running everything before shipping**
- The different-valid-tax-ID rule changed my own label for one scenario (`taxid_form_vs_cert`, 29 vendors in dev/test) from
  "ask the vendor" to "reviewer", and demo case V4 was rebuilt as the honest version (same company, other-state
  registration). This is a **policy change**, made openly, like the third-party-payee one.
- The name-vs-dropdown rule flagged 10 of my own stress vendors. It was right: the generator had named them 'GmbH' while
  leaving 'AG' in the dropdown. Fixed in the generator, not the rule.
- The first version of the swapped-word rule treated 'Tech' vs 'Technik' as different words (1 false alarm in the test
  set). It now compares names both as written and with abbreviations expanded.
- A personal SSN typed in the EIN box (blind case B26) briefly went to a reviewer; an SSN-shaped number is now a format
  error the vendor fixes.

**Did the fixes memorise the 44 cases?** `scripts/generalization_check.py` now builds 133 fresh vendors (new random names,
all four countries): for every rule, the situation it is for *and* a near-miss it must leave alone. **132 / 133.** The one
miss is the known seed-data quirk (Acme has a Karnataka GSTIN but a Mumbai address). Dev, test, stress (1,900 vendors),
blind, messy-documents and the 29 demo cases are all at 100%; ChatGPT holdout 35/37 as before.

**The pattern across the three unseen sets** (62.5%, 78.4%, 70.5% on first look): every new author finds new gaps. The
dangerous kind is shrinking (10 -> 3 -> 1 fraud-shaped approvals), but a fourth author would find more. That is the honest
state of a rule-based system, and it is why every doubtful case goes to a person and bank details are confirmed by phone.

## What happens when the AI is unavailable, or the input is nonsense (2026-11-12)
- **AI quota used up or an outage.** Before: every run was held for a reviewer ("step failed"), safe but useless. Now a text
  PDF is read by the built-in reader, *only if* it finds every critical field; the values are verified against the file as
  usual and the run says so in an amber notice. Photos, scans and unusual layouts are never read this way and still wait
  for a person. With the AI switched off, all 29 demo cases reach the same decision or a human; none is approved that
  should not be (`tests/test_ai_backup.py`).
- **Anything typed or uploaded by a stranger.** Empty form, a random PDF, a corrupt or empty file, a password-protected PDF,
  a 20,000-character name, script tags: each ends in a clear "here is what we need" to the vendor or a reviewer hold,
  never a crash. A file that cannot be opened is not sent to the AI at all.
- **A restart wipes the free host's data.** Ten example vendors are generated and run through the real pipeline at start-up
  (offline reader, no AI quota), marked "example", so the dashboard and review queue are never empty.

## Limits (say these before you're asked)
- **I wrote both the generator and the rules.** The stress set and the first-look numbers are how I guard against fooling myself, but synthetic data is cleaner than real inboxes. The real test is a pilot on a few hundred historic submissions with known outcomes.
- **Offline mode simulates the document reader.** `scripts/live_check.py` measures the live model:
  - field-level agreement with the file's own text;
  - end-to-end decisions on a stratified held-out sample.
- **A forger who retypes a whole document from scratch leaves no edit trace.** Only bank-side verification (penny-drop / confirmation of payee) catches that. That is why bank changes and new accounts always need a human call-back.
