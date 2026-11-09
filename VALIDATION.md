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
In the app, **Model quality → Spot-check 12 fresh vendors now** generates brand-new vendors with a random seed and scores them live.

## Limits (say these before you're asked)
- **I wrote both the generator and the rules.** The stress set and the first-look numbers are how I guard against fooling myself, but synthetic data is cleaner than real inboxes. The real test is a pilot on a few hundred historic submissions with known outcomes.
- **Offline mode simulates the document reader.** `scripts/live_check.py` measures the live model:
  - field-level agreement with the file's own text;
  - end-to-end decisions on a stratified held-out sample.
- **A forger who retypes a whole document from scratch leaves no edit trace.** Only bank-side verification (penny-drop / confirmation of payee) catches that. That is why bank changes and new accounts always need a human call-back.
