# Interview prep

## 30-second pitch
"Vendor onboarding is a fraud surface disguised as paperwork. I built a process that reads a vendor's form and documents, cross-checks every source against every other, and decides: approve, ask the vendor, hold for a person, or reject. The AI reads and explains; a rulebook decides; people only see what's genuinely ambiguous, and high-risk approvals need two of them. I measured it on 1,900 labelled synthetic vendors. On the held-out set it cut wrongful approvals from 48 to zero."

## Code tour (2 minutes)
| Say | Open |
|---|---|
| "Each stage only adds findings; one function decides." | `app/pipeline.py` (STAGES), then `app/rules.py` (`RULEBOOK`, `decide`) |
| "The AI has three jobs and none of them decides." | `app/llm.py` docstring |
| "This catches the edited bank letter without trusting the AI." | `app/validators/docintegrity.py` |
| "The human side: next actions, queue, four-eyes." | `app/workflow.py` |
| "How I know it works." | `synthetic/generator.py` (SCENARIOS), `app/quality.py` (cost weights), `VALIDATION.md` |

## Likely questions, with short answers
**How do you know it works?**
- I generated labelled vendors: consistent companies with one deliberate change each, across 37 scenarios.
- There are three sets:
  - tune on dev;
  - report on a held-out test set (99.9% on the first look);
  - and a stress set of variations I wrote before measuring (91.7% on the first look, 65% for the original).
- I report first-look numbers because those runs found real bugs I then fixed.

**Isn't 100% suspicious?**
- Yes. That's why I lead with the first-look numbers and say plainly that I wrote both the generator and the rules.
- Synthetic data is cleaner than reality. The next step is a pilot on a few hundred historic submissions with known outcomes, plus `live_check.py` on the real model.

**Why not train a model to classify vendors?**
- I wouldn't have labelled fraud examples; they are rare and sensitive.
- An auditor needs a reason they can read.
- Policy changes should be one-line edits, not retraining.
- The AI is used where judgment really is fuzzy (reading documents, ambiguous names), and even there code checks it.

**What stops the AI from inventing a value?**
- It must quote the text it read, and code checks the quote exists in the file.
- Critical fields that fail the check hold the case for a human (`DOC_UNGROUNDED`).

**Why not auto-reject fraud-shaped cases?**
- Suspicion isn't proof, and a wrongful rejection costs a real supplier.
- Exact sanctions matches are the only automatic rejections; everything fraud-shaped goes to a person, with four-eyes for the dangerous ones.

**Why don't you tell the vendor why they're being reviewed?**
- Telling a fraudster "your bank account belongs to another vendor" teaches them what to forge next.
- That's why "disclosure leaks" are weighted ×10 in my metric, and why the reviewer's "ask vendor" text is scanned for internal terms.

**What can't it catch?**
- A document retyped from scratch, with no edit trace. Only bank-side verification (penny-drop / confirmation of payee) catches that, so every bank change and new account gets a human call-back on a number already on file.
- It also doesn't check registries, ownership (UBO) or adverse media; those are listed in ASSUMPTIONS.md.

**What happens when the AI is down?**
- The step fails into `STAGE_ERROR`, which means hold for a human. It never approves on a failure.

**What does "four-eyes" protect against?**
- A single compromised or rushed reviewer approving a bank change.
- The second approver must be a different person, and both are in the audit chain.

**Why Render?**
- The brief needs a live link.
- Render builds the Dockerfile from GitHub and gives a URL.
- The free tier sleeps and forgets data on restart, so the app re-seeds itself on startup.

**What would you build next?**
1. A pilot on real historic data.
2. Confirmation-of-payee / penny-drop for bank ownership.
3. GST / VAT registry lookups.
4. SSO with real reviewer identities instead of typed names.
5. A scheduler instead of the demo clock.
6. Per-vendor-type policies.

## Numbers to remember
- **Held-out set:** 86.3% → **99.9%** (first look), unsafe approvals **48 → 0**, disclosure leaks **23 → 0**.
- **Stress set:** 65.0% → **91.7%** (first look).
- **Volume:** 1,900 synthetic vendors, 37 + 9 scenarios, 4 countries; 29 hand-made demo cases; 91 tests.
- **Policy settings:** auto-approve only below risk 20; bank letters expire after 90 days; review SLA 48 h; two reminders, then closed.
