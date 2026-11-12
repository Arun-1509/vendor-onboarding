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
DOC_TYPES = ["tax_cert", "bank_proof", "bank_statement", "incorporation", "other"]


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
    if "statement of account" in t or "kontoauszug" in t or "account statement" in t:
        return "bank_statement"
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
                  "bank_statement = an account statement listing transactions; "
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

    # Google retires model names for new keys without notice ("no longer available to new users").
    # So we ask the key which models it can use, once, and pick from that list.
    _available: list | None = None
    _dead: set = set()                                          # models that answered 404 for this key
    _no_think: set = set()                                      # models that reject thinkingConfig
    _cool: dict = {}                                            # model -> time until which it is rate-limited
    _retry_after: float = 5.0                                   # seconds Google last asked us to wait
    trace: list = []                                            # last calls: model, status, seconds (no content)
    _key_cool: dict = {}                                        # key index -> time until which it is rate-limited

    @classmethod
    def _key(cls) -> tuple[int, str]:
        """The first key that is not rate-limited (or the one that frees up soonest)."""
        import time
        keys = config.GEMINI_KEYS or [config.GEMINI_API_KEY]
        now = time.time()
        i = next((i for i in range(len(keys)) if cls._key_cool.get(i, 0) <= now),
                 min(range(len(keys)), key=lambda i: cls._key_cool.get(i, 0)))
        return i, keys[i]

    @classmethod
    def _thinking(cls, m: str):
        """Reading a form is extraction, not reasoning: keep 'thinking' minimal so a call takes seconds, not minutes."""
        if m in cls._no_think:
            return None
        if "2.5" in m:
            return {"thinkingBudget": 0 if "flash" in m else 128}
        return {"thinkingLevel": "low"}

    @classmethod
    def _trace(cls, m, status, t0, think):
        import time
        cls.trace = (cls.trace + [{"model": m, "status": status, "s": round(time.time() - t0, 1), "thinking": think}])[-12:]

    def available_models(self) -> list:
        if GeminiLLM._available is None:
            try:
                r = self.http.get("https://generativelanguage.googleapis.com/v1beta/models",
                                  params={"key": self._key()[1], "pageSize": 1000})
                ms = r.json().get("models", []) if r.status_code == 200 else []
                GeminiLLM._available = [m["name"].split("/")[-1] for m in ms
                                        if "generateContent" in m.get("supportedGenerationMethods", [])]
            except Exception:
                return []                                       # try again next call
        return GeminiLLM._available

    @staticmethod
    def _rank(name: str):
        """Higher is better: a text 'flash' model, newest version, stable before preview, full before lite."""
        import re
        bad = ("image", "tts", "audio", "live", "embed", "vision", "thinking", "exp", "learnlm", "gemma", "computer", "robotics", "native")
        if not name.startswith("gemini-") or "flash" not in name or any(b in name for b in bad):
            return None
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        return (0 if "lite" in name else 1, 1 if name.endswith("latest") else 0,
                float(v.group(1)) if v else 0, 0 if "preview" in name else 1)

    def models_for(self, primary: str) -> list:
        """Configured model and fallback if the key can use them; otherwise the best ones it can use."""
        avail = self.available_models()
        wanted = [primary, config.GEMINI_FALLBACK]
        if not avail:
            picks = wanted
        else:
            ranked = sorted((n for n in avail if self._rank(n)), key=self._rank, reverse=True)
            picks = [m for m in wanted if m in avail] + ranked
        import time
        live = [m for m in dict.fromkeys(picks) if m not in GeminiLLM._dead][:4] or list(dict.fromkeys(wanted))
        now = time.time()                                       # rate-limited models go last, not first
        return [m for m in live if GeminiLLM._cool.get(m, 0) <= now] + [m for m in live if GeminiLLM._cool.get(m, 0) > now]

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
        last, first = None, None
        import time
        models = self.models_for(primary)
        # A short text question (name judgment, email) must not hold a live run for a minute when Google is
        # overloaded: it gets a short timeout and a total time budget, then the caller's plan B takes over.
        has_file = any("inline_data" in p for p in body["contents"][0]["parts"])
        timeout = config.LLM_TIMEOUT_S if has_file else min(config.LLM_TIMEOUT_S, 6.0)
        deadline = time.time() + (35.0 if has_file else 10.0)
        for m in models + models[:2] * 2:                       # up to 2 more passes, only if all were busy
            if m in GeminiLLM._dead:
                continue
            if time.time() > deadline:
                last = last or LLMError("the AI model did not answer in time")
                break
            if last is not None and first is None:
                first = last
            if m is models[0] and last is not None:
                if "429" not in str(last) and "Timeout" not in type(last).__name__:
                    break                                       # real errors: don't loop
                time.sleep(min(max(GeminiLLM._retry_after, 2.0), 30.0))   # Google says how long; cap it
            think = self._thinking(m)
            for attempt in range(2 + len(config.GEMINI_KEYS)):  # one retry for blips, + one per spare key
                b = body if think is None else {**body, "generationConfig": {**body["generationConfig"], "thinkingConfig": think}}
                t0 = time.time()
                try:
                    ki, key = self._key()
                    r = self.http.post(self.URL.format(model=m), params={"key": key}, json=b, timeout=timeout)
                except Exception as e:
                    self._trace(m, type(e).__name__, t0, think)
                    GeminiLLM._cool[m] = time.time() + 300      # hung: try the next model now, this one much later
                    last = e
                    break
                self._trace(m, r.status_code if ki == 0 else f"{r.status_code} (key {ki + 1})", t0, think)
                if r.status_code == 429 and len(config.GEMINI_KEYS) > 1:
                    GeminiLLM._key_cool[ki] = time.time() + 60
                    if self._key()[0] != ki:                    # another key has quota: switch now, same model
                        continue
                if r.status_code == 400 and think is not None and "thinking" in r.text.lower():
                    GeminiLLM._no_think.add(m)                  # this model rejects the setting: send it plain
                    think = None
                    continue
                if r.status_code == 429:                        # free-tier quota: move on, don't wait
                    GeminiLLM._cool[m] = time.time() + 60
                    import re
                    hint = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', r.text or "")
                    GeminiLLM._retry_after = float(hint.group(1)) if hint else 5.0
                    last = LLMError(f"{m} returned 429 (rate limit)")
                    break
                if r.status_code in (500, 502, 503, 504):       # overloaded: one quick retry, then the next model
                    last = LLMError(f"{m} returned {r.status_code}")
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    GeminiLLM._cool[m] = time.time() + 60
                    break
                if r.status_code != 200:
                    if r.status_code == 404:
                        GeminiLLM._dead.add(m)
                    last = LLMError(f"{m} returned {r.status_code}: {r.text[:160]}")
                    break
                try:
                    data = r.json()
                    cand = data["candidates"][0]
                    text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                    last = LLMError("model returned JSON that is not an object")
                except Exception as e:                          # safety block, empty, or malformed JSON
                    last = LLMError(f"{m} gave an unusable answer: {e}")
                break
        raise LLMError(f"LLM call failed: {first or last}" + (f" (fallback: {last})" if first and first is not last else ""))


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


# ============================================================ backup ====
# What each document must yield before the built-in reader's answer is trusted.
_BACKUP_NEEDS = {"tax_cert": [("legal_name",), ("tax_id",)],
                 "bank_proof": [("account_holder",), ("account_number", "iban"), ("issue_date",)],
                 "incorporation": [("legal_name",)]}


class _TextOnlyReader(MockLLM):
    """The built-in reader restricted to what is really on the page: the text layer of a PDF.
    (No simulated OCR: a photo or scan cannot be read without the AI.)"""

    def _text_for(self, path: str) -> tuple[str, bool]:
        return pdf_text(path), True


class ResilientLLM:
    """The live model, with a safe plan B for when it cannot answer (free-tier quota used up, outage).

    Plan B never guesses. A document is read by the built-in reader ONLY if it is a text PDF and the
    reader finds every critical field in it; the values are then grounded against the page like any
    AI answer. Anything else (photos, scans, unusual layouts) still fails, which holds the vendor for
    a person, exactly as before. Every use is recorded on the run, so nothing is passed off as AI."""
    mode = "live"
    _down_until = 0.0                                           # shared: skip the live model while it is out of quota

    def __init__(self, live):
        self.live = live
        self.backup_used: list[str] = []
        self._plain = _TextOnlyReader()

    def __getattr__(self, name):                                # provider, trace, ... come from the live client
        return getattr(self.live, name)

    def _live(self, fn, *a):
        import time
        if time.time() < ResilientLLM._down_until:
            raise LLMError("the AI model is out of quota (checked moments ago)")
        try:
            return fn(*a)
        except Exception as e:
            if "429" in str(e):                                 # quota: don't make the next runs wait for it too
                ResilientLLM._down_until = time.time() + 180
            raise

    def extract_document(self, doc_type: str, path: str) -> dict:
        try:
            return self._live(self.live.extract_document, doc_type, path)
        except Exception as e:
            if not pdf_text(path).strip():
                raise
            r = self._plain.extract_document(doc_type, path)
            got = r.get("fields") or {}
            complete = r.get("doc_type_detected") == doc_type and all(any(k in got for k in alt) for alt in _BACKUP_NEEDS.get(doc_type, []))
            # a recognised wrong document (a statement, another slot's file) is also a safe, complete answer
            if not complete and r.get("doc_type_detected") in ("other", doc_type):
                raise
            self.backup_used.append(doc_type)
            return {**r, "backup": True, "backup_reason": str(e)[:160]}

    def judge_names(self, a: str, b: str, context: dict) -> dict:
        try:
            return self._live(self.live.judge_names, a, b, context)
        except Exception as e:
            self.backup_used.append("name_judgment")
            j = self._plain.judge_names(a, b, context)          # rule-based and conservative
            return {**j, "backup": True, "reasoning": j["reasoning"] + " (AI unavailable: decided by the built-in name rules.)",
                    "backup_reason": str(e)[:160]}

    def draft_email(self, audience: str, kind: str, ctx: dict) -> dict:
        return self._live(self.live.draft_email, audience, kind, ctx)   # the pipeline already falls back to a template


def get_llm(backup: bool = False):
    """`backup=True` (interactive runs) adds the safe plan B; benchmarks measure the bare model."""
    if config.MOCK_LLM:
        return MockLLM()
    live = GeminiLLM() if config.PROVIDER == "gemini" else AnthropicLLM()
    return ResilientLLM(live) if backup else live
