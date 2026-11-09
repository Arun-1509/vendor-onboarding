"""The AI layer. Three jobs only, none of which decide anything:

  1. extract_document : read a PDF/image into structured fields WITH the exact text it read
  2. judge_names      : explain whether two grey-zone names are the same company
  3. draft_email      : write the vendor/reviewer message from facts the rules already produced

Two interchangeable implementations:
  AnthropicLLM : real Claude calls (needs ANTHROPIC_API_KEY)
  MockLLM      : deterministic, offline; reads our generated sample documents
`get_llm()` picks one. The UI/run record always says which mode produced a run.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from . import config
from .validators import names as nm

TAX_CERT_FIELDS = ["legal_name", "tax_id", "address", "issue_date"]
BANK_PROOF_FIELDS = ["account_holder", "bank_name", "account_number", "iban", "ifsc",
                     "swift", "routing_number", "sort_code", "issue_date"]
INCORP_FIELDS = ["legal_name", "registration_number", "issue_date"]
DOC_FIELDS = {"tax_cert": TAX_CERT_FIELDS, "bank_proof": BANK_PROOF_FIELDS, "incorporation": INCORP_FIELDS}
DOC_TYPES = ["tax_cert", "bank_proof", "incorporation", "other"]


class LLMError(Exception):
    pass


# ============================================================ helpers ====
def pdf_text(path: str) -> str:
    """Text layer of a PDF ('' for scans/images). Used for grounding checks."""
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        return ""
    try:
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    except Exception:
        return ""


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def grounded(evidence: str, text: str) -> bool:
    return bool(evidence) and _alnum(evidence) in _alnum(text)


# ============================================================== mock ====
_LABELS = {
    "legal name": "legal_name", "registered name": "legal_name", "name of taxpayer": "legal_name",
    "business name": "legal_name", "account holder": "account_holder",
    "account holder name": "account_holder", "beneficiary": "account_holder",
    "gstin": "tax_id", "vat id": "tax_id", "vat number": "tax_id", "ein": "tax_id", "tax id": "tax_id",
    "address": "address", "registered address": "address", "date of issue": "issue_date",
    "date": "issue_date", "issue date": "issue_date", "bank": "bank_name", "bank name": "bank_name",
    "account number": "account_number", "iban": "iban", "ifsc": "ifsc", "swift": "swift",
    "bic": "swift", "routing number": "routing_number", "sort code": "sort_code",
    "registration number": "registration_number", "cin": "registration_number",
}


def _mock_doc_type(text: str) -> str:
    t = text.lower()
    if "incorporation" in t:
        return "incorporation"
    if any(k in t for k in ("registration certificate", "vat registration", "form w-9", "certificate of registration")):
        return "tax_cert"
    if "bank" in t and any(k in t for k in ("letter", "cheque", "confirmation")):
        return "bank_proof"
    return "other"


class MockLLM:
    mode = "mock"

    def _text_for(self, path: str) -> tuple[str, bool]:
        """-> (text, legible). Images use a `.mock.json` sidecar standing in for OCR."""
        p = Path(path)
        side = Path(str(p) + ".mock.json")
        if side.exists():
            d = json.loads(side.read_text())
            return d.get("text", ""), d.get("legible", True)
        return pdf_text(path), True

    def extract_document(self, doc_type: str, path: str) -> dict:
        text, legible = self._text_for(path)
        if not legible or not text.strip():
            return {"doc_type_detected": "other", "legible": False, "fields": {}, "notes": "unreadable"}
        out = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            label, _, value = line.partition(":")
            key = _LABELS.get(label.strip().lower())
            value = value.strip()
            if key and value and key not in out:
                out[key] = {"value": value, "evidence": line.strip(), "confidence": 0.95}
        wanted = DOC_FIELDS.get(doc_type, [])
        return {"doc_type_detected": _mock_doc_type(text), "legible": True,
                "fields": {k: v for k, v in out.items() if k in wanted}, "notes": ""}

    def judge_names(self, a: str, b: str, context: dict) -> dict:
        sim = nm.core_similarity(a, b)
        if nm.is_abbreviation_of(a, b) or nm.is_abbreviation_of(b, a):
            return {"same_entity": "yes", "confidence": 0.86, "variant_type": "abbreviation",
                    "reasoning": "One name abbreviates a word that the other spells out; the rest is identical."}
        if sim >= 0.85:
            return {"same_entity": "unsure", "confidence": 0.55, "variant_type": "typo",
                    "reasoning": "Very similar, but the difference could be a different company."}
        return {"same_entity": "no", "confidence": 0.7, "variant_type": "different_entity",
                "reasoning": "The core names differ materially."}

    def draft_email(self, audience: str, kind: str, ctx: dict) -> dict:
        return _template_email(audience, kind, ctx)


# ============================================================== real ====
def _doc_block(path: str) -> dict:
    p = Path(path)
    ext = p.suffix.lower()
    media = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext)
    if not media:
        raise LLMError(f"Unsupported file type {ext}")
    data = base64.standard_b64encode(p.read_bytes()).decode()
    kind = "document" if ext == ".pdf" else "image"
    return {"type": kind, "source": {"type": "base64", "media_type": media, "data": data}}


class AnthropicLLM:
    mode = "live"

    def __init__(self):
        import anthropic
        self._a = anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                          timeout=config.LLM_TIMEOUT_S, max_retries=2)

    def _call(self, model: str, system: str, content: list, tool: str, schema: dict, max_tokens=2000) -> dict:
        tools = [{"name": tool, "description": "Return the structured result.", "input_schema": schema}]
        last = None
        for m in dict.fromkeys([model, config.MODEL_FALLBACK]):     # primary, then fallback
            for choice in ({"type": "tool", "name": tool}, {"type": "auto"}):
                try:
                    r = self.client.messages.create(
                        model=m, max_tokens=max_tokens, system=system, tools=tools,
                        tool_choice=choice, messages=[{"role": "user", "content": content}])
                except self._a.BadRequestError as e:      # e.g. forced tool-choice unsupported -> try 'auto'
                    last = e
                    continue
                except self._a.APIError as e:             # outage/overload -> next model
                    last = e
                    break
                if getattr(r, "stop_reason", "") == "refusal":
                    last = LLMError(f"{m} refused")
                    break                                  # go to fallback model
                for b in r.content:
                    if b.type == "tool_use" and b.name == tool:
                        return dict(b.input)
                last = LLMError("model did not return the tool call")
        raise LLMError(f"LLM call failed: {last}")

    def extract_document(self, doc_type: str, path: str) -> dict:
        fields = DOC_FIELDS.get(doc_type, [])
        field_schema = {"type": "object", "properties": {
            "value": {"type": ["string", "null"], "description": "Exact value as printed; null if absent"},
            "evidence": {"type": "string", "description": "The exact characters copied from the document that this value came from"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
            "required": ["value", "evidence", "confidence"]}
        schema = {"type": "object", "properties": {
            "doc_type_detected": {"type": "string", "enum": DOC_TYPES},
            "legible": {"type": "boolean"},
            "fields": {"type": "object", "properties": {f: field_schema for f in fields}},
            "notes": {"type": "string"}}, "required": ["doc_type_detected", "legible", "fields"]}
        system = ("You extract fields from vendor-onboarding documents for an accounts-payable team. "
                  "Copy values exactly as printed. NEVER infer, guess, normalise or complete a value. "
                  "If a field is not visibly present, return null with empty evidence. "
                  "If the page is too blurry/cropped to read reliably, set legible=false. "
                  "Dates as YYYY-MM-DD. Say what kind of document this really is in doc_type_detected "
                  "(tax_cert = tax/VAT/GST registration or W-9; bank_proof = bank letter or cancelled cheque; "
                  "incorporation; or other, e.g. a utility bill).")
        content = [_doc_block(path), {"type": "text", "text":
                   f"The vendor says this is a '{doc_type}'. Extract: {', '.join(fields)}."}]
        return self._call(config.MODEL_EXTRACT, system, content, "record_extraction", schema, 1500)

    def judge_names(self, a: str, b: str, context: dict) -> dict:
        schema = {"type": "object", "properties": {
            "same_entity": {"type": "string", "enum": ["yes", "no", "unsure"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "variant_type": {"type": "string", "enum": ["legal_suffix", "trade_name", "abbreviation", "typo", "different_entity"]},
            "reasoning": {"type": "string", "description": "<= 40 words, plain English"}},
            "required": ["same_entity", "confidence", "variant_type", "reasoning"]}
        system = ("You help an AP team decide whether two company names written by different sources refer to the "
                  "same legal entity. Be conservative: a different registered company must NOT be called the same. "
                  "If unsure say 'unsure'. Your answer is advisory; a rule engine makes the final decision.")
        msg = json.dumps({"name_a": a, "name_b": b, "context": context})
        return self._call(config.MODEL_JUDGE, system, [{"type": "text", "text": msg}], "record_judgment", schema, 600)

    def draft_email(self, audience: str, kind: str, ctx: dict) -> dict:
        schema = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
                  "required": ["subject", "body"]}
        rules = {
            "vendor": ("Write to the VENDOR. List ONLY the items in `asks`, numbered, each in plain words with the "
                       "specific example of what is needed. Warm, brief, professional. Include the portal link and the "
                       "deadline. NEVER mention fraud, suspicion, risk scores, sanctions, screening or internal rules."),
            "reviewer": ("Write an internal note to the procurement REVIEWER. Say what was flagged and why it matters, "
                         "list the evidence, and recommend the next step (e.g. call the number already on file for the "
                         "existing vendor, NOT the number in this submission)."),
        }[audience]
        system = "You write short, clear business emails for a procurement team. " + rules + " Use only the facts given."
        return self._call(config.MODEL_WRITE, system,
                          [{"type": "text", "text": json.dumps({"kind": kind, **ctx})}], "record_email", schema, 900)


# ============================================================== gemini ====
_GEMINI_DROP = {"additionalProperties", "$schema", "default", "title"}


def _gemini_schema(node):
    """Convert our JSON Schema into the OpenAPI subset Gemini accepts (no type lists, uppercase-agnostic)."""
    if isinstance(node, list):
        return [_gemini_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k in _GEMINI_DROP:
            continue
        if k == "type" and isinstance(v, list):               # ["string","null"] -> string + nullable
            non_null = [t for t in v if t != "null"]
            out["type"] = non_null[0] if non_null else "string"
            if "null" in v:
                out["nullable"] = True
        elif k == "properties":
            out[k] = {pk: _gemini_schema(pv) for pk, pv in v.items()}
        else:
            out[k] = _gemini_schema(v)
    return out


class GeminiLLM(AnthropicLLM):
    """Same prompts, schemas and grounding as the Claude path; only the transport differs.
    Free tier: aistudio.google.com. Uses the REST API directly (httpx), so no extra SDK."""
    mode = "live"
    provider = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, client=None):
        import httpx
        self.http = client or httpx.Client(timeout=config.LLM_TIMEOUT_S)

    @staticmethod
    def _parts(content: list) -> list:
        parts = []
        for b in content:
            if b.get("type") == "text":
                parts.append({"text": b["text"]})
            else:                                               # our _doc_block: base64 pdf/image
                src = b["source"]
                parts.append({"inline_data": {"mime_type": src["media_type"], "data": src["data"]}})
        return parts

    def _call(self, model: str, system: str, content: list, tool: str, schema: dict, max_tokens=2000) -> dict:
        primary = config.GEMINI_JUDGE_MODEL if model == config.MODEL_JUDGE else config.GEMINI_MODEL
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": self._parts(content)}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": max(max_tokens, 1500) + 2000,
                                     "responseMimeType": "application/json",
                                     "responseSchema": _gemini_schema(schema)}}
        last = None
        for m in dict.fromkeys([primary, config.GEMINI_FALLBACK]):
            for attempt in range(2):                            # one retry for rate limits / blips
                try:
                    r = self.http.post(self.URL.format(model=m), params={"key": config.GEMINI_API_KEY}, json=body)
                except Exception as e:
                    last = e
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    last = LLMError(f"{m} returned {r.status_code}")
                    import time
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    last = LLMError(f"{m} returned {r.status_code}: {r.text[:160]}")
                    break
                try:
                    data = r.json()
                    cand = data["candidates"][0]
                    text = "".join(p.get("text", "") for p in cand["content"]["parts"])
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                    last = LLMError("model returned JSON that is not an object")
                except Exception as e:                          # safety block, empty, or malformed JSON
                    last = LLMError(f"{m} gave an unusable answer: {e}")
                break
        raise LLMError(f"LLM call failed: {last}")


# ============================================================ templates ====
def _template_email(audience: str, kind: str, ctx: dict) -> dict:
    name, ref = ctx.get("vendor_name", "your company"), ctx.get("reference", "")
    link, due = ctx.get("portal_url", ""), ctx.get("due_date", "")
    if audience == "reviewer":
        items = "\n".join(f"  {i}. {a}" for i, a in enumerate(ctx.get("asks", []), 1))
        rejected = ctx.get("status") == "rejected"
        return {"subject": f"[{'Auto-rejected: compliance' if rejected else 'Review needed'}] {name} ({ref})",
                "body": f"{name} ({ref}) was {'automatically rejected on a compliance rule' if rejected else 'put on hold for manual review'}.\n\nWhat was flagged:\n{items}\n\n"
                        f"Recommended next step: {ctx.get('next_step', 'verify out-of-band before approving')}.\n"
                        f"Nothing about this has been disclosed to the vendor."}
    if kind == "approved":
        return {"subject": f"Welcome aboard: {name} is approved ({ref})",
                "body": f"Hello,\n\nThank you for completing onboarding. {name} is now approved as a vendor "
                        f"(reference {ref}). You can submit invoices as usual.\n\nProcurement Team"}
    if kind == "rejected":
        return {"subject": f"Update on your vendor registration ({ref})",
                "body": f"Hello,\n\nThank you for your interest. After review we are unable to proceed with the "
                        f"registration of {name} at this time. If you believe this is an error, please reply to "
                        f"this email.\n\nProcurement Team"}
    items = "\n".join(f"  {i}. {a}" for i, a in enumerate(ctx.get("asks", []), 1))
    if kind == "reminder":
        n = ctx.get("reminder_no", 1)
        return {"subject": f"Reminder {n}: we still need a few items for {name} ({ref})",
                "body": f"Hello,\n\nThis is a friendly reminder: we are still waiting for the following to finish onboarding {name}:\n\n{items}\n\n"
                        f"You can send these securely here: {link}\nPlease reply by {due}; after that we will have to close the request.\n\nThank you,\nProcurement Team"}
    if kind == "closed":
        return {"subject": f"Your vendor registration was closed ({ref})",
                "body": f"Hello,\n\nWe did not receive the information we asked for, so we have closed the registration of {name}. "
                        f"You are welcome to register again at any time: your earlier details will be reused.\n\nProcurement Team"}
    return {"subject": f"Action needed to finish onboarding {name} ({ref})",
            "body": f"Hello,\n\nThanks for registering {name}. To finish onboarding we need the following:\n\n{items}\n\n"
                    f"You can send these securely here: {link}\nPlease reply by {due}; after that we will have to "
                    f"close the request.\n\nThank you,\nProcurement Team"}


_BANNED_IN_VENDOR_MAIL = re.compile(
    r"fraud|suspicio|sanction|screening|lookalike|look-alike|risk score|blacklist|watchlist|impersonat", re.I)


def vendor_mail_is_safe(text: str) -> bool:
    return not _BANNED_IN_VENDOR_MAIL.search(text or "")


def get_llm():
    if config.MOCK_LLM:
        return MockLLM()
    return GeminiLLM() if config.PROVIDER == "gemini" else AnthropicLLM()
