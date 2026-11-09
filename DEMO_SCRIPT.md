# Demo script

**Before recording or the interview:**
1. Open the site a minute early (Render free tier sleeps).
2. Overview → **Reset demo data**.
3. Check the chip at the bottom left (Offline AI or Live AI) and be ready to say which one it is.

## The 5-minute video (Deliverable 2)
| Time | Screen | What to say (in your own words) |
|---|---|---|
| 0:00 | **How it works** | "Before a company pays a vendor, someone checks the form, the tax certificate and the bank letter, then chases the vendor. I automated that end to end. The rule I built around: the AI reads and explains, code decides, people handle what's genuinely ambiguous." |
| 0:30 | **New submission → Happy path → Clean German vendor → Run** | "Nine stages, live. The AI reads the documents and has to quote what it read; code checks the quote is in the file. Then check digits, cross-references, screening." Point at the document panel: "Green boxes are values verified on the page." Decision: approved, risk 0, a welcome email drafted. |
| 1:20 | **Documents that lie → Bank letter looks perfect… → Run** | "This one looks perfect. But code reads the raw PDF: the account-number line exists twice. Someone pasted a new number over the genuine one." Point at the **red box**. "So it's held for a reviewer, the vendor is told nothing, and the checklist says to call the bank using its own website's number." |
| 2:20 | **Review queue → that vendor** | "The reviewer gets the evidence and a checklist. Approving a case like this needs two different people." Approve as Asha → "Needs 2nd approval". Try Asha again → blocked. Approve as Ben → approved. "Both decisions are in a hash-chained audit trail." Click **Verify chain**. |
| 3:20 | **Wait and resume → Run**, then **↩ Simulate the vendor replying** | "Something fixable, so the vendor gets exactly one request and a private link. The reply becomes version 2, the system shows what was resolved, and the vendor is approved." |
| 4:00 | **Model quality** | "How do I know it works? 1,900 labelled synthetic vendors. On the held-out set it goes from 86% to 99.9% on the first look, and from 48 approvals that should never have happened to zero." Click **Spot-check 12 fresh vendors**: "these companies didn't exist a second ago." |
| 4:45 | **Rulebook** | "Every decision traces to a line here that a procurement lead can read and change. The AI can't." |

Keep talking while the stages stream; the live run is the most convincing part.

## Live interview runbook (happy path first, then edge cases)
1. **Happy path:** *Clean Indian vendor*. Open "Show evidence" under *Format & check digits* (GSTIN checksum) and *Cross-reference* (PAN initial, GSTIN state, IFSC bank).
2. **Edge 1, looks fine, isn't:** *Approved vendor 'Northwind' sends new bank details*. Show the version diff (old bank → new bank) and four-eyes approval.
3. **Edge 2, documents that lie:** *Bank letter … pasted over*, red box on the document; then *GSTIN … belongs to a company starting with 'M'*.
4. **Edge 3, looks wrong, isn't:** *'Pvt Ltd' vs 'Private Limited'* is approved with the variant recorded. Then the negative twin: an *extra word* in the name goes to the vendor for clarification.
5. **Edge 4, wait and resume:** as in the video. Then leave one vendor waiting and press **Advance one week** twice: a reminder, then closure by the system.
6. **Compliance:** *Sanctions match* is auto-rejected and the vendor gets nothing; *near-match* goes to compliance.
7. **Unseen input:** New submission → **Surprise me → Generate & run**. The banner compares the system's decision with the ground-truth label. Or let them upload their own PDF.

## If something goes wrong live
- **A model call fails:** the vendor lands in "Needs review" with a system-error reason. Say so: that is the designed behaviour (never guess, never approve).
- **The page is slow on first load:** the free tier is waking up. Say so.
- **The data is in a strange state:** Reset demo data.
