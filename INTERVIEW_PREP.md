# Interview prep

## The one-sentence design
**The AI reads and explains; code decides.** Nothing the model says can set a status. `rules.decide()` is the only place that happens.

## Read the code in this order (about 90 minutes)
1. `app/rules.py`: the rulebook and `decide()`. Everything else feeds this.
2. `app/pipeline.py`: the 9 stages, in the order a run takes them.
3. `app/validators/names.py`: the name logic (exact / equivalent / form mismatch / grey / different).
4. `app/llm.py`: extraction with grounding, the judge, email drafting, the email guardrail.
5. `app/screening.py`: sanctions, lookalikes, shared accounts.
6. `app/service.py`: submissions, resubmission, the waiting loop (`tick`), the vendor portal.
7. `app/db.py`: the schema and the hash-chained audit log.
8. `tests/test_scenarios.py`: the 26 labelled cases, and the mutation tests.

## Questions to expect, and the honest answers

**Why not let the model decide?** It is non-deterministic, can be talked into things by document text, and cannot explain a decision to an auditor. A rulebook can be read, argued with and changed by a procurement lead. The model's job is to turn messy PDFs into fields and to explain.

**How do you stop it hallucinating a field?** Every extracted value comes with the quote it was read from. Code checks the quote exists in the PDF text layer. If not, the field is dropped. A crashed step becomes a human hold.

**Why is the lookalike not rejected?** A lookalike might be a fraudster or a legitimate subsidiary. Rejecting would be accusing someone with no proof, and telling them why teaches a fraudster what to fix. So a human decides and the vendor learns nothing. Only sanctions hits and non-response auto-reject.

**What counts as the grey zone for names?** After normalising and stripping legal forms, similarity ≥0.93 is "same", below 0.72 is "different", and in between goes to the judge, accepted only at confidence ≥0.80. Abbreviations must have equal word counts, which fixed a real bug where a dropped word was waved through.

**What do the validators not do?** Format and checksum only. No live registry lookup (GST portal, VIES). That is the biggest honest gap; a real build adds those APIs behind the same stage.

**How is it kept from emailing real people?** Mail only ever goes to `DEMO_INBOX`, never the vendor's address.

**What if the API is down?** Documents cannot be read, so a `STAGE_ERROR` hold goes to a reviewer. Never an approval.

**What would you do next?** Registry lookups; OCR for scanned PDFs beyond the vision path; per-country rule packs owned by compliance; Slack/ERP integration; a reviewer queue with SLAs; sanctions via a real list provider.

**Why does it work with Gemini and Claude?** The model only reads, explains and drafts; code decides. So the provider is a swappable transport (`GeminiLLM` reuses the same prompts, schemas and grounding check). That is the design paying off.

**Why does it work with Gemini and Claude?** The model only reads, explains and drafts; code decides. So the provider is a swappable transport (`GeminiLLM` reuses the same prompts, schemas and grounding check). That is the design paying off.

## Be upfront about
- The live model path (Gemini or Claude) was tested with fake clients and the offline mock, then verified with `scripts/check_model.py` and the live evaluation. Say what you ran.
- The sanctions list is fictional data.
- Thresholds (risk 20, similarity 0.93/0.72) are reasoned starting points, not tuned on real vendor data.
- Hand-built labelled cases are not production traffic.

## Numbers you can quote
26 labelled cases, 9 stages, about 40 rules, 4 countries (IN, DE, GB, US), auto-approve under risk 20, 7-day response window with 2 reminders 3 days apart, 6 MB upload limit.
