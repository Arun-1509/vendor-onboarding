# Demo script

**Before recording or the interview:**
1. Open the site a minute early (Render free tier sleeps).
2. Overview → **Reset demo data**.
3. Check the chip at the bottom left (Offline AI or Live AI) and be ready to say which one it is.

## The 5-minute video (Deliverable 2)
Everything is on **New submission → Start here**: five numbered steps, each with a **Next** button in the purple strip at the top.

| Time | Screen | What to say (in your own words) |
|---|---|---|
| 0:00 | **Overview** | "Before a company pays a vendor, someone checks the form, the tax certificate and the bank letter, then chases the vendor. I automated that end to end. My rule: the AI reads and explains, code decides, people handle what is genuinely ambiguous." Click **Start the tour**. |
| 0:25 | **Step 1: Happy path** | "Nine stages, live. The AI reads both documents and has to quote what it read; code checks the quote is really in the file. Then check digits, cross-references, screening." Point at the document: "Green boxes are values verified on the page." Approved, risk 0, welcome email drafted. Click **Next**. |
| 1:10 | **Step 2: Payment-fraud attempt** | "An approved vendor 'updates' its bank account. Every field is valid on its own; only comparing with what is on file catches it. It is held for two reviewers, and the vendor is told nothing, so a fraudster learns nothing." Click **Next**. |
| 1:50 | **Step 3: A document that lies** | "This bank letter looks perfect. But code reads the raw PDF: a new account number was pasted over the original." Point at the **red box**. "Held for a reviewer, with a checklist: call the bank on the number from its own website." Click **Next**. |
| 2:35 | **Step 4: Missing document, then the reply** | "A brand-new company forgot its bank letter. It gets one precise request and a private link." Click **Simulate the vendor replying**. "The reply is version 2; only the failed check is re-run, and it is approved." Click **Next**. |
| 3:20 | **Step 5: Looks wrong, is fine** | "'Fab' on one document, 'Fabrication' on another. The AI judge explains why it is the same company and the rulebook accepts it. No false alarm." Click **Finish: see the dashboard**. |
| 3:45 | **Overview** | "The dashboard: every run, its status, what is waiting on a vendor and what needs a person." |
| 4:00 | **Model quality** | "How do I know it works? On my own test data it scores 99.9%, but I wrote that data, so I did not trust it. I had other AI models, including ChatGPT, write test cases without seeing my code. The first tries scored 62, 78 and 70 percent. Here is what they found and what I fixed." Open **What each test found**. Optional: click **Spot-check 4 fresh vendors**: "these companies did not exist a second ago." |
| 4:40 | **Rulebook** | "Every decision traces to a line here that a procurement lead can read and change. The AI cannot." |

If a step is slow, keep talking: the free AI tier is sometimes busy, and the answer is still correct. An amber "AI model unavailable" notice means the built-in reader took over for that document; say so.

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
