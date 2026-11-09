# Demo script

Two uses: the 5-minute recorded video, and the live interview. Same story, different depth. Before either: regenerate samples (`python scripts/make_samples.py`), click **Reset demo data**, confirm the chip says **Live AI**.

## The 5-minute video

| Time | Show | Say (in your own words) |
|---|---|---|
| 0:00 | Process page | "Vendor onboarding today is someone eyeballing PDFs against a form. I built the process end to end: submission in, approved / pending / rejected out, with the reasoning visible and a message back for anything not approved." |
| 0:30 | New submission > *Clean Indian vendor* > Run it now | "A clean vendor. Watch the nine stages. The AI reads the documents and has to quote what it read; code checks the quote really is on the page. Format and checksums, cross-checks and risk screening are plain code." Point at the verdict: Approved, risk 0. |
| 1:30 | New > *Bank letter missing…* | "Fixable problem, so the vendor gets a precise email, and a private link, not a rejection." Open the email, then **Open as the vendor**: show the vendor page asks only for the bank letter and shows no risk or findings. |
| 2:15 | Back on the vendor > **Demo: vendor sends the missing document** | "Only the failed check re-runs and clears; everything else is kept." Show "Cleared by this resubmission", then Approved. |
| 3:00 | **Edge case 1**, *Lookalike of approved vendor…* | "Globex Logistic vs Globex Logistics, different tax ID, new bank account. This is the classic fraud shape. I do **not** auto-reject: it goes to a person with the evidence and the safe next step (call the number already on file). The vendor is told nothing." Record a reviewer decision with a reason, then show Audit trail > Verify. |
| 3:50 | **Edge case 2**, *'Private Limited' vs 'PVT. LTD.'* then *extra word* | "Harmless variants are noticed and not punished. But an extra word is a different company, so it is not waved through. That one I found by mutation testing my own mock AI." |
| 4:30 | Evaluation > Run all cases | "26 labelled vendors with known answers. If I change a rule and break a decision, this shows it." |
| 4:50 | Rulebook | "Every decision traces to a line here. The AI reads and explains; the rulebook decides." |

If a live model call is slow (free tiers can be), keep talking over the stage tape: it is the most convincing part.

## Live interview order (happy path, then edge cases, no slides)

1. Happy path: *Clean German vendor*. Open "Technical detail" under *Validate* to show VAT and IBAN checksums.
2. Edge: bank change on an approved vendor (*E1b*). Why a human: the same fraud that costs companies real money.
3. Edge: wrong document (*V5*) or blurry scan (*V7*). Show how the ask to the vendor is specific.
4. Edge: sanctions (*R1*). Show that it auto-rejects, and that the vendor gets nothing.
5. Waiting loop: leave a vendor awaiting, press **Advance one week** twice. Show the reminder, then the closure by "system" in History.
6. Let the interviewer submit their own: upload any PDF plus a form. Expect imperfect output on weird files; that is what the hold-for-a-human path is for.

## If something goes wrong live

- A model call fails: the vendor lands in "Needs review" with a system-error reason. Say so; it is the designed behaviour (never guess, never approve).
- Site cold: open it a minute early.
- Wrong state: Reset demo data.
