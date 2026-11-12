"""The onboarding pipeline. Each stage is one step in the live run view.

  intake -> read_documents -> completeness -> validate -> cross_check
         -> name_judgment -> risk_screening -> decision -> communicate

Stages only ADD FINDINGS. `rules.decide()` turns findings into a status. Every stage
writes to the hash-chained audit log, and a crash in any stage becomes a STAGE_ERROR
finding (human takes over) instead of a silent failure or a guessed answer."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
import traceback
from datetime import datetime, timedelta

from . import config, db, rules, screening, workflow
from .llm import (DOC_FIELDS, LLMError, get_llm, grounded, pdf_text, vendor_mail_is_safe)
from .rules import finding
from .validators import bank as bk
from .validators import dates as dt_
from .validators import docintegrity
from .validators import names as nm
from .validators import taxid as tx

DOC_LABEL = {"tax_cert": "tax registration certificate (GST / VAT / W-9)",
             "bank_proof": "bank letter or cancelled cheque",
             "incorporation": "certificate of incorporation"}
FIELD_LABEL = {
    "legal_name": "the registered legal name", "entity_type": "the legal form (e.g. Pvt Ltd, GmbH)",
    "country": "the country", "address": "the registered address", "city": "the city",
    "postal_code": "the postal / PIN code", "tax_id": "the tax registration number",
    "contact_name": "a contact person", "contact_email": "a contact email address",
    "bank_name": "the bank name", "account_holder": "the name on the bank account",
    "currency": "the payment currency", "account_number": "the bank account number",
    "ifsc": "the bank's IFSC code", "iban": "the IBAN", "routing_number": "the US routing number",
    "sort_code": "the UK sort code",
}
CRITICAL = {"legal_name", "tax_id", "account_holder", "account_number", "iban"}
COMMON_REQUIRED = ["legal_name", "entity_type", "country", "address", "city", "postal_code", "tax_id",
                   "contact_name", "contact_email", "bank_name", "account_holder", "currency"]
# letters, digits and . _ % + - before the @; dot-separated labels of letters/digits/hyphens after it
# (so 'name@company,co.uk' is not an address). Unicode letters are allowed (umlaut domains exist).
EMAIL_RE = re.compile(r"^[\w.%+\-']+@(?:[^\W_](?:[\w\-]*[^\W_])?\.)+[^\W\d_]{2,}$")
# which entity types on the form fit a legal form written in the company's own name
_NAME_FORM_FITS = {"private_limited": {"pvt_ltd", "ltd"}, "limited": {"ltd", "pvt_ltd"}, "llp": {"llp"}, "gmbh": {"gmbh"},
                   "ag": {"ag"}, "ug": {"ug", "gmbh"}, "llc": {"llc"}, "inc": {"inc", "corp"}, "plc": {"plc", "ltd"}}
_LEET = [str.maketrans("1035478", "loesatb"), str.maketrans("1035478", "ioesatb")]


def _lookalike_domain(email: str, *company_names: str) -> str | None:
    """'ar@carro11ton-fasteners.com' for 'Carrollton ...': the domain spells a word of the company's
    name with digits in place of letters. Returns that word, or None."""
    dom = email.rsplit("@", 1)[-1].lower().rsplit(".", 1)[0]
    if not any(ch.isdigit() for ch in dom):
        return None
    raw = re.sub(r"[^a-z0-9]", "", dom)
    words = {t for n in company_names for t in nm.split_name(n or "", expand=False)[0] if len(t) >= 5 and t.isalpha()}
    for table in _LEET:
        fixed = raw.translate(table)
        hit = next((w for w in sorted(words) if w in fixed and w not in raw), None)
        if hit:
            return hit
    return None


# ================================================================ context ====
class Ctx:
    def __init__(self, conn, llm, vendor, submission, run_id, emit):
        self.conn, self.llm, self.vendor, self.sub, self.run_id, self.emit = conn, llm, vendor, submission, run_id, emit
        self.form = dict(submission["form"])
        self.paths = dict(submission["docs"])
        self.now = db.now(conn)
        self.findings: list[dict] = []
        self.docs: dict[str, dict] = {}        # doc_type -> extraction result
        self.grey: list[dict] = []             # name pairs awaiting AI judgment
        self.tax: tx.Check | None = None
        self.account_key = ""
        self.emails: list[dict] = []
        self.facts: dict = {}

    @property
    def country(self):
        return self.form.get("country", "")

    def add(self, f):
        self.findings.append(f)

    def usable(self, t):
        return self.docs.get(t, {}).get("usable", False)

    def doc_val(self, t, field):
        return (self.docs.get(t, {}).get("values") or {}).get(field)

    def has_field(self, k):
        return bool(str(self.form.get(k, "") or "").strip())


# ================================================================== runner ====
def _stage(ctx: Ctx, seq: int, name: str, title: str, fn):
    ctx.emit({"type": "stage_start", "stage": name, "title": title, "seq": seq})
    before = len(ctx.findings)
    t0 = time.time()
    status, headline, details = "pass", "", {}
    try:
        out = fn(ctx) or {}
        headline, details = out.get("headline", ""), out.get("details", {})
    except Exception as e:                                   # never crash silently, never guess
        status, headline = "error", f"This step failed ({type(e).__name__}); a person will take over."
        details = {"error": str(e)[:300], "trace": traceback.format_exc()[-600:]}
        ctx.add(finding("STAGE_ERROR", f"Step '{name}' failed: {e}", field=name))
    new = ctx.findings[before:]
    if status != "error" and any(f["action"] != "none" for f in new):
        status = "flag"
    ms = int((time.time() - t0) * 1000)
    ctx.conn.execute(
        "INSERT INTO run_steps(run_id,seq,stage,status,headline,details,ms,at) VALUES(?,?,?,?,?,?,?,?)",
        (ctx.run_id, seq, name, status, headline,
         json.dumps({**details, "new_findings": [f["code"] for f in new]}, default=str), ms, db.iso(db.now(ctx.conn))))
    db.audit(ctx.conn, "stage_completed", {"stage": name, "status": status, "headline": headline,
                                           "findings": [f["code"] for f in new]},
             ctx.vendor["vendor_id"], ctx.run_id)
    ctx.emit({"type": "stage_end", "stage": name, "title": title, "seq": seq, "status": status,
              "headline": headline, "details": details, "ms": ms,
              "findings": [{k: f[k] for k in ("code", "title", "severity", "action", "points", "message", "field")}
                           for f in new]})


# ================================================================== stages ====
def s_intake(ctx: Ctx):
    f = ctx.form
    prev = ctx.conn.execute("SELECT form FROM submissions WHERE vendor_id=? AND version<? ORDER BY version DESC LIMIT 1",
                            (ctx.vendor["vendor_id"], ctx.sub["version"])).fetchone()
    changed = []
    if prev:
        old = json.loads(prev["form"])
        changed = sorted(k for k in set(old) | set(f) if (old.get(k) or "") != (f.get(k) or ""))
    ctx.facts["changed_fields"] = changed
    n_docs = sum(1 for p in ctx.paths.values() if p)
    head = (f"Update to existing vendor {ctx.vendor['vendor_id']} (version {ctx.sub['version']}): {len(changed)} field(s) changed, {n_docs} document(s) on file."
            if prev else f"New submission from {f.get('contact_name') or 'unknown contact'}: {n_docs} document(s) attached.")
    return {"headline": head, "details": {"version": ctx.sub["version"], "changed_fields": changed,
                                          "country": ctx.country, "documents": sorted(k for k, v in ctx.paths.items() if v)}}


_STATEMENT_MARKERS = ("statement of account", "account statement", "kontoauszug", "opening balance", "closing balance")


def _integrity(ctx, dtype, label, path, d=None):
    """Tamper check on the raw file. Runs on every uploaded file, also one we then refuse as the wrong
    kind of document: an edited bank statement is a fraud signal, not just 'please send a letter'."""
    # integrity: read the raw file, not the AI's reading of the page
    integ = docintegrity.analyze(path)
    d["integrity"] = integ
    if integ["conflicts"]:
        parts = "; ".join(f"{k.replace('_', ' ')}: " + " vs ".join(screening.mask(x) if k in ("account_number", "iban") or (k == "overlaid_value" and sum(ch.isdigit() for ch in x) >= 6) else x for x in v)
                          for k, v in integ["conflicts"].items())
        ctx.add(finding("DOC_CONFLICTING_VALUES",
                        f"The {label} contains two different values for the same field ({parts}). One is hidden under the visible page"
                        + (f"; the file was edited {integ['incremental_updates']} time(s) after it was created." if integ["incremental_updates"] else "."),
                        field=dtype, evidence={k: [screening.mask(x) if k in ("account_number", "iban") or (k == "overlaid_value" and sum(ch.isdigit() for ch in x) >= 6) else x for x in v]
                                               for k, v in integ["conflicts"].items()} | {"incremental_updates": integ["incremental_updates"]}))
    elif integ["incremental_updates"]:
        ctx.add(finding("DOC_EDITED", f"The {label} was saved with {integ['incremental_updates']} later edit(s).", field=dtype,
                        evidence={"incremental_updates": integ["incremental_updates"], "producer": integ["producer"]}))


_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b|\b\d{5,6}\b|\b[A-Z]{2}\s+\d{5}\b", re.I)


def _street_numbers(addr: str) -> list[str]:
    """House / plot / unit numbers in an address, postcodes removed ('Kanalweg 19, 04109 Leipzig' -> ['19'])."""
    return re.findall(r"\b\d{1,4}[a-z]?\b", _POSTCODE.sub(" ", addr or "").lower())


def _norm_read(v) -> str:
    return "".join(ch for ch in str(v or "").upper() if ch.isalnum())


def _looks_like_statement(path: str) -> bool:
    """A bank statement (transaction list) is not proof of the account. Checked in code because the
    live model sometimes labels a statement as a bank letter."""
    from .llm import pdf_text
    try:
        t = (pdf_text(path) or "").lower()
    except Exception:
        return False
    return any(m in t for m in _STATEMENT_MARKERS)


def _unopenable(path: str) -> str:
    """'' if the file opens as what its extension says; otherwise the reason."""
    p = Path(path)
    try:
        if not p.exists() or p.stat().st_size < 64:
            return "the file is empty"
        if p.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            r = PdfReader(str(p))
            if r.is_encrypted:
                return "the PDF is password-protected"
            if len(r.pages) == 0:
                return "the PDF has no pages"
            r.pages[0].get_contents()                          # forces the page to be parsed
            return ""
        from PIL import Image
        with Image.open(p) as im:
            im.verify()
        return ""
    except Exception as e:
        return f"the file could not be opened ({type(e).__name__})"


def s_read_documents(ctx: Ctx):
    summaries = {}
    todo = [(t, ctx.paths[t]) for t in ("tax_cert", "bank_proof", "incorporation") if ctx.paths.get(t)]
    # Read all documents at once (each is an independent AI call); results are then checked in a fixed order.
    from concurrent.futures import ThreadPoolExecutor
    # A file that cannot even be opened (empty, corrupt, password-protected, not really a PDF/image) is the
    # vendor's to fix; it is not sent to the AI, which would only fail on it or, worse, invent content.
    broken = {t: why for t, p in todo if (why := _unopenable(p))}
    with ThreadPoolExecutor(max_workers=max(1, len(todo))) as ex:
        futs = {t: ex.submit(ctx.llm.extract_document, t, p) for t, p in todo if t not in broken}
    read = {t: f.result() for t, f in futs.items()}            # re-raises an AI failure -> STAGE_ERROR, as before
    read.update({t: {"doc_type_detected": "other", "legible": False, "fields": {}, "notes": why} for t, why in broken.items()})
    tc, bp = read.get("tax_cert", {}), read.get("bank_proof", {})
    if tc.get("doc_type_detected") == "bank_proof" and bp.get("doc_type_detected") == "tax_cert":
        # both documents are here, just in each other's upload slots: read each as what it is
        read["tax_cert"], read["bank_proof"] = bp, tc
        ctx.paths["tax_cert"], ctx.paths["bank_proof"] = ctx.paths["bank_proof"], ctx.paths["tax_cert"]
        todo = [(t, ctx.paths[t]) for t, _ in todo]
        ctx.add(finding("DOC_SLOTS_SWAPPED", "The tax certificate and the bank letter were uploaded into each other's slots; each was read as what it is.",
                        field="documents"))
    # Photos/scans have no text layer to check the AI against, and a model asked to read a blurry photo
    # tends to GUESS with high confidence. So scanned documents are read a second time, independently:
    # a critical value the two reads disagree on was not really legible. Nothing is tuned to a test set;
    # this is plain self-consistency and works the same for any document, language or layout.
    scans = [(t, p) for t, p in todo if read[t].get("legible", True) and not pdf_text(p).strip()]
    if scans:
        with ThreadPoolExecutor(max_workers=len(scans)) as ex:
            again = {t: ex.submit(ctx.llm.extract_document, t, p) for t, p in scans}
        for t, fut in again.items():
            try:
                second = fut.result()
            except Exception:                              # second read failed: keep the first, note it
                read[t]["second_read"] = "failed"
                continue
            disagree = []
            for k, v in (read[t].get("fields") or {}).items():
                a = _norm_read((v or {}).get("value"))
                b = _norm_read(((second.get("fields") or {}).get(k) or {}).get("value"))
                if k in CRITICAL and a and a != b:
                    disagree.append(k)
                    v["confidence"] = 0.0                  # -> DOC_UNREADABLE for that field below
            read[t]["second_read"] = {"disagreed_on": disagree}
            if not second.get("legible", True):
                read[t]["legible"] = False                 # either read saying 'unreadable' wins
    for dtype, path in todo:
        label = DOC_LABEL[dtype]
        res = read[dtype]
        if res.get("backup"):
            ctx.facts.setdefault("ai_backup", []).append(label)
        d = {"path": path, "detected": res.get("doc_type_detected"), "legible": res.get("legible", True),
             "raw": res.get("fields", {}), "values": {}, "usable": False, "grounding": "n/a",
             "second_read": res.get("second_read")}
        ctx.docs[dtype] = d
        if not d["legible"]:
            ctx.add(finding("DOC_UNREADABLE", f"The {label} could not be read reliably.", field=dtype,
                            ask=f"Your {label} was hard to read. Please upload a clearer, full-page copy (a PDF or a straight-on photo in good light)."))
            summaries[dtype] = "unreadable"
            continue
        if dtype == "bank_proof" and d["detected"] in ("bank_proof", "other") and _looks_like_statement(path):
            d["detected"] = "bank_statement"          # code double-checks the AI: a transaction list is a statement
        if dtype == "bank_proof" and d["detected"] == "bank_statement":
            ctx.add(finding("DOC_NOT_ACCEPTED", "A bank statement was uploaded as proof of the bank account.", field=dtype,
                            ask="You uploaded a bank statement. Please send a letter from your bank confirming the account (or a cancelled cheque), issued in the last 90 days."))
            summaries[dtype] = "bank statement (not accepted)"
            _integrity(ctx, dtype, label, path, d)
            continue
        if d["detected"] != dtype:
            seen = {"tax_cert": "a tax certificate", "bank_proof": "a bank letter", "incorporation": "an incorporation certificate", "bank_statement": "a bank statement",
                    "other": "a different kind of document"}.get(d["detected"], "a different kind of document")
            ctx.add(finding("WRONG_DOCUMENT", f"File uploaded as {label} looks like {seen}.", field=dtype,
                            ask=f"The file uploaded as your {label} looks like {seen}. Please upload your {label}."))
            summaries[dtype] = f"wrong type ({d['detected']})"
            _integrity(ctx, dtype, label, path, d)
            continue
        text = pdf_text(path)
        d["grounding"] = "verified against text layer" if text.strip() else "n/a (scanned image: relying on model confidence)"
        bad = []
        for k, v in res.get("fields", {}).items():
            val = (v or {}).get("value")
            if not val:
                continue
            conf = float(v.get("confidence", 0))
            if k == "issue_date":
                iso = dt_.parse_one(val)
                # dates are compared as DATES: '09.11.2026' on the page grounds '2026-11-09'
                if text.strip() and not (iso and iso in dt_.dates_in(text)):
                    bad.append(k)
                    continue
                if iso:
                    d["values"][k] = iso.isoformat()
                    continue
            if text.strip() and not (grounded(v.get("evidence", ""), text) and grounded(val, text)):
                bad.append(k)
                if k in CRITICAL:
                    ctx.add(finding("DOC_UNGROUNDED", f"'{k}' = '{val}' was reported but is not in the document text.", field=f"{dtype}.{k}"))
                continue
            if conf < config.EXTRACTION_MIN_CONFIDENCE:
                if k in CRITICAL:
                    ctx.add(finding("DOC_UNREADABLE", f"Low confidence ({conf:.0%}) reading '{k}' from the {label}.", field=f"{dtype}.{k}",
                                    ask=f"We could not read the {k.replace('_', ' ')} on your {label} clearly. Please upload a clearer copy."))
                continue
            d["values"][k] = val
        d["usable"] = True
        d["ungrounded"] = bad
        d["scanned"] = not text.strip()
        _integrity(ctx, dtype, label, path, d)
        summaries[dtype] = f"{len(d['values'])} fields read" + (" (scan)" if d["scanned"] else "")
    n = len(ctx.docs)
    note = " (AI model unavailable: the built-in reader read the text of " + ", ".join(ctx.facts["ai_backup"]) + ")" if ctx.facts.get("ai_backup") else ""
    return {"headline": (f"Read {n} document(s): " + ", ".join(f"{k}: {v}" for k, v in summaries.items()) if n else "No documents to read.") + note,
            "details": {"documents": {k: {"detected_as": v["detected"], "legible": v["legible"], "grounding": v["grounding"],
                                          "fields": v["values"]} for k, v in ctx.docs.items()}}}


def _required_account_fields(country, form):
    if country == "IN":
        return ["account_number", "ifsc"]
    if country == "DE":
        return ["iban"]
    if country == "GB":
        return [] if form.get("iban") else ["sort_code", "account_number"]
    if country == "US":
        return ["routing_number", "account_number"]
    return []


def s_completeness(ctx: Ctx):
    missing = []
    if ctx.country not in config.COUNTRIES:
        ctx.add(finding("MISSING_FIELD", f"Country '{ctx.country}' is not one we onboard yet (supported: {', '.join(config.COUNTRIES)}).",
                        field="country", ask="Please tell us your country of registration (we currently onboard India, Germany, the UK and the US)."))
    for k in COMMON_REQUIRED + _required_account_fields(ctx.country, ctx.form):
        if not ctx.has_field(k):
            missing.append(k)
            ctx.add(finding("MISSING_FIELD", f"Required field '{k}' is empty.", field=k, ask=f"Please provide {FIELD_LABEL.get(k, k)}."))
    for dtype in ("tax_cert", "bank_proof"):
        if not ctx.paths.get(dtype):
            missing.append(dtype)
            ctx.add(finding("MISSING_DOC", f"No {DOC_LABEL[dtype]} was attached.", field=dtype,
                            ask=f"Please upload your {DOC_LABEL[dtype]}."))
    return {"headline": f"{len(missing)} required item(s) missing" if missing else "All required fields and documents are present.",
            "details": {"missing": missing}}


def s_validate(ctx: Ctx):
    f, c, checks = ctx.form, ctx.country, []
    if ctx.has_field("tax_id"):
        t = tx.validate_tax_id(c, f["tax_id"])
        ctx.tax = t
        checks.append(("tax_id", t.ok, t.detail))
        if not t.ok:
            code = "TAXID_CHECKSUM" if t.kind == "checksum" else "TAXID_FORMAT"
            ctx.add(finding(code, t.detail, field="tax_id",
                            ask=f"The tax ID you gave ({f['tax_id']}) does not look right: {t.detail} Please check it and send the correct number."))
    def bankchk(field, res):
        checks.append((field, res.ok, res.detail))
        if not res.ok:
            code = "BANK_CHECKSUM" if res.kind == "checksum" else "BANK_FORMAT"
            ctx.add(finding(code, f"{field}: {res.detail}", field=field,
                            ask=f"The {FIELD_LABEL.get(field, field)} you gave does not look right: {res.detail} Please check and resend it."))
    if c == "IN":
        if f.get("ifsc"): bankchk("ifsc", bk.validate_ifsc(f["ifsc"]))
        if f.get("account_number"): bankchk("account_number", bk.validate_account_number("IN", f["account_number"]))
    elif c == "DE":
        if f.get("iban"): bankchk("iban", bk.validate_iban(f["iban"]))
    elif c == "GB":
        if f.get("iban"): bankchk("iban", bk.validate_iban(f["iban"]))
        if f.get("sort_code"): bankchk("sort_code", bk.validate_sort_code(f["sort_code"]))
        if f.get("account_number"): bankchk("account_number", bk.validate_account_number("GB", f["account_number"]))
    elif c == "US":
        if f.get("routing_number"): bankchk("routing_number", bk.validate_aba(f["routing_number"]))
        if f.get("account_number"): bankchk("account_number", bk.validate_account_number("US", f["account_number"]))
    if f.get("swift"): bankchk("swift", bk.validate_swift(f["swift"]))
    if ctx.has_field("contact_email") and not EMAIL_RE.match(f["contact_email"].strip()):
        checks.append(("contact_email", False, "invalid"))
        ctx.add(finding("EMAIL_INVALID", "Contact email is not a valid address.", field="contact_email",
                        ask="The contact email address looks invalid. Please send a working address."))
    # document dates
    if ctx.usable("bank_proof") and not ctx.doc_val("bank_proof", "issue_date"):
        # no date we can verify on the page = we cannot show the letter is recent
        ctx.add(finding("DOC_UNDATED", "No verifiable date was found on the bank letter.", field="bank_proof",
                        ask=f"We could not find a date on your {DOC_LABEL['bank_proof']}. Please send a dated letter from your bank, "
                            f"issued in the last {config.BANK_PROOF_MAX_AGE_DAYS} days."))
    for dtype, maxage in (("bank_proof", config.BANK_PROOF_MAX_AGE_DAYS), ("tax_cert", None)):
        iso = ctx.doc_val(dtype, "issue_date")
        if not iso or not ctx.usable(dtype):
            continue
        try:
            d = datetime.strptime(iso[:10], "%Y-%m-%d")
        except ValueError:
            continue
        age = (ctx.now.replace(tzinfo=None) - d).days
        if age < -1:                                            # one day of slack for time zones
            ctx.add(finding("DOC_FUTURE_DATED", f"The {DOC_LABEL[dtype]} is dated {iso}, which is {-age} days in the future.",
                            field=dtype, evidence={"date_on_document": iso, "days_ahead": -age}))
        elif maxage and age > maxage:
            ctx.add(finding("DOC_STALE", f"The bank letter is {age} days old (limit {maxage}).", field=dtype,
                            ask=f"Your {DOC_LABEL[dtype]} is dated {iso}, which is more than {maxage} days ago. Please send one issued in the last {maxage} days."))
    ctx.account_key = bk.normalize_account_key(c, f)
    bad = [k for k, ok, _ in checks if not ok]
    return {"headline": f"{len(checks)} checks run, {len(bad)} failed." + (f" Failed: {', '.join(bad)}." if bad else ""),
            "details": {"checks": [{"field": k, "ok": ok, "detail": d} for k, ok, d in checks]}}


_OCR_TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}


def _ocr_reconcile(ctx, dtype, field, doc_value, form_value) -> bool:
    """On a SCAN only: a doc value that differs from the form solely where the scan shows a
    look-alike LETTER in a position where the form has the matching DIGIT is an OCR misread,
    not a different number. (A letter there cannot be a valid value, so this cannot hide a
    genuinely different account.)"""
    if not ctx.docs.get(dtype, {}).get("scanned"):
        return False
    d, f = re.sub(r"[^A-Z0-9]", "", doc_value.upper()), re.sub(r"[^A-Z0-9]", "", form_value.upper())
    if len(d) != len(f) or d == f:
        return False
    diffs = [(a, b) for a, b in zip(d, f) if a != b]
    if len(diffs) > 2 or not all(b.isdigit() and _OCR_TO_DIGIT.get(a) == b for a, b in diffs):
        return False
    ctx.add(finding("DOC_OCR_RECONCILED", f"The scanned {DOC_LABEL[dtype]} reads {field.replace('_', ' ')} as '{doc_value}'; "
                    f"{', '.join(f'{a}->{b}' for a, b in diffs)} is a scan misread, so it matches the form.", field=f"{dtype}.{field}",
                    evidence={"read": doc_value, "form": form_value}))
    return True


def _looks_like_typo(a: str, b: str) -> bool:
    """A keying slip (1-2 wrong characters, two neighbours swapped, one extra/missing) vs a different account."""
    if a == b:
        return True
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and (diff[1] == diff[0] + 1 or (a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]))
    if abs(len(a) - len(b)) == 1:
        s, l = (a, b) if len(a) < len(b) else (b, a)
        return any(l[:i] + l[i + 1:] == s for i in range(len(l)))
    return False


def _compare_into(ctx, a, b, where, unresolved_code, ask_mismatch, field, grey_code=None):
    """Compare two names; record variants, queue grey zone for the AI, or flag a mismatch."""
    cmpd = nm.compare_names(a, b)
    v = cmpd["verdict"]
    if v == "exact":
        return cmpd
    if v == "equivalent":
        ctx.add(finding("NAME_VARIANT", f"'{a}' and '{b}': {cmpd['why']}.", field=field, evidence=cmpd))
    elif v == "form_mismatch":
        ctx.add(finding("FORM_LEGAL_FORM_MISMATCH", f"'{a}' vs '{b}': {cmpd['why']}.", field=field, evidence=cmpd,
                        ask=f"The company name differs in legal form between your documents ('{a}' vs '{b}'). Please confirm the exact registered name."))
    elif v == "grey":
        ctx.grey.append({"a": a, "b": b, "field": field, "code": grey_code or unresolved_code, "ask": ask_mismatch, "cmp": cmpd})
    else:
        ctx.add(finding(unresolved_code, f"'{a}' vs '{b}': {cmpd['why']}.", field=field, evidence=cmpd, ask=ask_mismatch))
    return cmpd


def s_cross_check(ctx: Ctx):
    f, c = ctx.form, ctx.country
    notes = []
    # ---- 1. country signals -------------------------------------------------------
    tax_country = tx.detect_tax_id_country(f.get("tax_id", "")) if ctx.has_field("tax_id") else None
    bank_country = None
    if f.get("iban"): bank_country = f["iban"].replace(" ", "")[:2].upper()
    elif f.get("ifsc"): bank_country = "IN"
    elif f.get("swift") and len(f["swift"].strip()) >= 6: bank_country = f["swift"].strip()[4:6].upper()
    ctx.facts["signals"] = {"claimed": c, "tax_id_looks_like": tax_country, "bank_looks_like": bank_country}
    if c in config.COUNTRIES and tax_country and tax_country != c:
        others = [x for x in (tax_country, bank_country) if x and x != c]
        ctx.add(finding("COUNTRY_CONTRADICTION",
                        f"Form says {c}, but the tax ID looks {tax_country}" + (f" and the bank details look {bank_country}" if bank_country and bank_country != c else "") + ".",
                        field="country", evidence=ctx.facts["signals"],
                        points=30 + (5 if len(others) > 1 else 0)))
    elif c in config.COUNTRIES and bank_country and bank_country != c:
        ctx.add(finding("BANK_COUNTRY_DIFFERS", f"Vendor is in {c} but the bank is in {bank_country}.", field="bank", evidence=ctx.facts["signals"]))
        home = config.COUNTRIES[c]["currency"]
        if ctx.has_field("currency") and f["currency"].upper() != home:
            # abroad AND not in the vendor's own currency: the money leaves the country it was earned in
            ctx.add(finding("OFFSHORE_PAYMENT", f"A vendor in {c} asks to be paid in {f['currency'].upper()} into an account in {bank_country}.",
                            field="bank", evidence={**ctx.facts["signals"], "currency": f["currency"].upper(), "home_currency": home}))
    cur_ok = {config.COUNTRIES.get(c, {}).get("currency"), config.COUNTRIES.get(bank_country or "", {}).get("currency")}
    if ctx.has_field("currency") and c in config.COUNTRIES and f["currency"].upper() not in cur_ok:
        if not f.get("iban") and not f.get("swift"):            # a domestic-only account cannot receive this currency
            ctx.add(finding("CURRENCY_CONFIRM", f"Currency {f['currency'].upper()} with domestic-only {c} bank details.", field="currency",
                            ask=f"You selected {f['currency'].upper()} as the payment currency, but the account details are for a domestic "
                                f"{c} account. Please confirm the currency (or send IBAN/SWIFT details for a foreign-currency account)."))
        else:
            ctx.add(finding("CURRENCY_UNUSUAL", f"Currency {f['currency'].upper()} is unusual for {c}.", field="currency"))

    # ---- 2. entity type vs PAN ----------------------------------------------------
    if c == "IN" and ctx.tax and ctx.tax.ok and ctx.tax.info.get("pan_entity"):
        ch = ctx.tax.info["pan_entity"]
        match = tx.pan_matches_declared_entity(f.get("entity_type", ""), ch)
        if match is False and ch != "P" and f.get("entity_type") in ("sole_prop", "partnership", "llp", "pvt_ltd", "ltd"):
            # the tax ID is a company's or firm's and the form says another type: a dropdown slip, not a disguise
            ctx.add(finding("ENTITY_TYPE_FORM_ERROR",
                            f"The PAN inside the GSTIN ({ctx.tax.info['pan']}) is a {tx.PAN_ENTITY[ch]}, but the form says '{f.get('entity_type')}'.",
                            field="entity_type", evidence={"pan": ctx.tax.info["pan"], "pan_char": ch, "declared": f.get("entity_type")},
                            ask=f"Your tax registration shows your business as a {tx.PAN_ENTITY[ch].lower()}, but the form says "
                                f"'{f.get('entity_type', '').replace('_', ' ')}'. Please correct the business type."))
        elif match is False:
            ctx.add(finding("ENTITY_TYPE_MISMATCH_PAN",
                            f"The PAN inside the GSTIN ({ctx.tax.info['pan']}) has type '{ch}' = {tx.PAN_ENTITY[ch]}, but the vendor declares '{f.get('entity_type')}'.",
                            field="tax_id", evidence={"pan": ctx.tax.info["pan"], "pan_char": ch, "pan_means": tx.PAN_ENTITY[ch],
                                                      "declared": f.get("entity_type")}))
    # ---- 2a. entity type vs the legal form in the company's own name ----------------
    name_form = nm.split_name(f.get("legal_name", ""))[1]
    et = f.get("entity_type", "")
    if name_form in _NAME_FORM_FITS and et and et not in _NAME_FORM_FITS[name_form] \
            and not any(x["code"] in ("ENTITY_TYPE_FORM_ERROR", "ENTITY_TYPE_MISMATCH_PAN") for x in ctx.findings):
        ctx.add(finding("ENTITY_TYPE_FORM_ERROR", f"The name '{f.get('legal_name')}' is a {name_form.replace('_', ' ')} company, but the form says '{et}'.",
                        field="entity_type", evidence={"name_says": name_form, "declared": et},
                        ask=f"Your company name ('{f.get('legal_name')}') shows a different business type from the one selected on the form "
                            f"('{et.replace('_', ' ')}'). Please correct the business type."))
    # ---- 2b. India: PAN initial vs name, GSTIN state vs addresses ----------------
    if c == "IN" and ctx.tax and ctx.tax.ok and ctx.tax.info.get("kind") == "GSTIN":
        pan = ctx.tax.info["pan"]
        who = (ctx.doc_val("tax_cert", "legal_name") if ctx.usable("tax_cert") else None) or f.get("legal_name", "")
        if tx.pan_name_initial_ok(pan, who) is False:
            ctx.add(finding("PAN_NAME_MISMATCH",
                            f"The PAN inside the GSTIN ({pan}) is for a business whose name starts with '{pan[4]}', but the vendor is '{who}'.",
                            field="tax_id", evidence={"pan": pan, "pan_5th_char": pan[4], "name": who}))
        gst_state = ctx.tax.info["state_code"]
        cert_pin = tx.pin_from_address(ctx.doc_val("tax_cert", "address") or "") if ctx.usable("tax_cert") else None
        form_pin = tx.pin_from_address(f.get("address", ""), f.get("postal_code", ""))
        if cert_pin and tx.pin_states(cert_pin) and gst_state not in tx.pin_states(cert_pin):
            ctx.add(finding("GSTIN_STATE_MISMATCH",
                            f"The GSTIN is registered in state {gst_state}, but the certificate's own address (PIN {cert_pin}) is in state {'/'.join(sorted(tx.pin_states(cert_pin)))}.",
                            field="tax_cert", evidence={"gstin_state": gst_state, "certificate_pin": cert_pin}))
        elif form_pin and tx.pin_states(form_pin) and gst_state not in tx.pin_states(form_pin):
            ctx.add(finding("GSTIN_BRANCH_STATE",
                            f"Form address (PIN {form_pin}) is outside the GSTIN's state {gst_state}; the certificate is consistent, so this looks like a branch registration.",
                            field="address", evidence={"gstin_state": gst_state, "form_pin": form_pin}))
    # ---- 3. bank code vs bank name (IFSC / IBAN BLZ / sort code) -------------------
    code_ok = (bk.validate_ifsc(f["ifsc"]).ok if f.get("ifsc") else bk.validate_iban(f["iban"]).ok if f.get("iban")
               else bk.validate_sort_code(f["sort_code"]).ok if f.get("sort_code") else False)
    code_bank, code_src = bk.bank_from_code(c, f) if code_ok else (None, "")
    if code_bank and f.get("bank_name") and not bk.bank_names_match(code_bank, f["bank_name"]):
        ctx.add(finding("BANK_CODE_MISMATCH", f"The {code_src} belongs to {code_bank}, but the form says '{f['bank_name']}'.",
                        field="bank_name", evidence={"code": code_src, "code_bank": code_bank, "stated": f["bank_name"]}))
    bad_parts = bk.gb_iban_parts_mismatch(f)
    if bad_parts:
        ctx.add(finding("IBAN_PARTS_MISMATCH", f"The IBAN does not contain the {' and '.join(p.replace('_', ' ') for p in bad_parts)} given on the form.",
                        field="iban", ask="Your IBAN does not match the sort code / account number you gave. Please check all three and resend them."))
    # ---- 4. tax certificate vs form -----------------------------------------------
    legal = f.get("legal_name", "")
    if ctx.usable("tax_cert"):
        dt = ctx.doc_val("tax_cert", "tax_id")
        if dt and ctx.has_field("tax_id"):
            dn = tx.validate_tax_id(c, dt).normalized or re.sub(r"[\s\-]", "", dt).upper()
            fn_ = (ctx.tax.normalized if ctx.tax else re.sub(r"[\s\-]", "", f["tax_id"]).upper())
            if dn != fn_ and _ocr_reconcile(ctx, "tax_cert", "tax_id", dt, fn_):
                pass
            elif dn != fn_ and not _looks_like_typo(dn, fn_) and tx.validate_tax_id(c, dt).ok and (ctx.tax and ctx.tax.ok)                     and not (c == "IN" and len(dn) == 15 and len(fn_) == 15 and dn[2:12] == fn_[2:12]):
                # (same PAN inside two GSTINs = the SAME company's registration in another state: the vendor's to fix)
                # two VALID, unrelated numbers: not a keying slip, one of them is somebody else's registration
                ctx.add(finding("TAXID_DIFFERENT_DOC", f"Form tax ID {fn_} and certificate tax ID {dn} are two different valid numbers.",
                                field="tax_id", evidence={"form": fn_, "certificate": dn}))
            elif dn != fn_:
                ctx.add(finding("TAXID_MISMATCH_DOC", f"Form tax ID {fn_} differs from certificate {dn}.", field="tax_id",
                                evidence={"form": fn_, "certificate": dn},
                                ask=f"The tax ID on your form ({fn_}) is different from the one on your certificate ({dn}). Please confirm which is correct and send the matching certificate."))
        if not dt and ctx.has_field("tax_id") and not any(x.get("field") == "tax_cert.tax_id" for x in ctx.findings):
            # the certificate is readable but shows no tax number at all: it cannot back up the one typed in
            ctx.add(finding("DOC_INCOMPLETE", "The tax document does not show a tax number.", field="tax_cert",
                            ask=f"Your {DOC_LABEL['tax_cert']} does not show your tax number. Please send a completed copy that shows it."))
        cn = ctx.doc_val("tax_cert", "legal_name")
        if cn and legal:
            cmpd = _compare_into(ctx, legal, cn, "tax_cert", "NAME_MISMATCH_TAXCERT",
                                 f"The company name on your form ('{legal}') does not match your tax certificate ('{cn}'). Please send the certificate for the company you are registering, or correct the name.",
                                 "legal_name", grey_code="NAME_UNRESOLVED")
            same_id = dt and ctx.has_field("tax_id") and not any(x["code"] in ("TAXID_MISMATCH_DOC", "TAXID_DIFFERENT_DOC") for x in ctx.findings)
            cert_addr0 = ctx.doc_val("tax_cert", "address") or ""
            sq = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())  # noqa: E731
            here = [x for x in (f.get("postal_code"), f.get("city")) if sq(x)]
            elsewhere = bool(cert_addr0) and bool(here) and not any(sq(x) in sq(cert_addr0) for x in here)
            if cmpd["verdict"] == "grey" and same_id and elsewhere and nm.extra_words(legal, cn):
                # two independent signs of ANOTHER entity: its name lacks a word of ours ('... (Leeds) Ltd' vs '... Ltd')
                # and it is registered in a different town. A parent's or franchisor's registration is not ours.
                ctx.grey[:] = [g for g in ctx.grey if not (g["field"] == "legal_name" and g["b"] == cn)]
                ctx.add(finding("TAXCERT_OTHER_ENTITY", f"The tax ID on the form is registered to '{cn}' at {cert_addr0}, not to '{legal}'.",
                                field="tax_id", evidence={"form_name": legal, "registered_name": cn, "registered_address": cert_addr0, **cmpd}))
            if cmpd["verdict"] == "different" and same_id:
                # the form's tax ID is registered to this OTHER company: borrowed identity, not an upload slip
                ctx.add(finding("TAXCERT_OTHER_ENTITY", f"The tax ID on the form is registered to '{cn}', not to '{legal}'.",
                                field="tax_id", evidence={"form_name": legal, "registered_name": cn, **cmpd}))
    authority = ctx.doc_val("tax_cert", "legal_name") if ctx.usable("tax_cert") and ctx.doc_val("tax_cert", "legal_name") else legal
    # ---- 5. bank letter vs form ----------------------------------------------------
    holder_form = f.get("account_holder", "")
    holder = holder_form
    if ctx.usable("bank_proof"):
        for fld, label in (("iban", "IBAN"), ("account_number", "account number")):
            dv, fv = ctx.doc_val("bank_proof", fld), f.get(fld)
            if dv and fv and re.sub(r"[\s\-]", "", dv).upper() != re.sub(r"[\s\-]", "", fv).upper() \
                    and not _ocr_reconcile(ctx, "bank_proof", fld, dv, fv):
                if _looks_like_typo(re.sub(r"[\s\-]", "", dv).upper(), re.sub(r"[\s\-]", "", fv).upper()):
                    ctx.add(finding("BANK_ACCOUNT_MISMATCH_DOC", f"Form {label} ends {screening.mask(fv)[-4:]}, bank letter ends {screening.mask(dv)[-4:]}.",
                                    field=fld, evidence={"form": screening.mask(fv), "letter": screening.mask(dv)},
                                    ask=f"The {label} on your form does not match your bank letter. Please confirm the correct {label}."))
                else:                                           # not a keying slip: a different account altogether
                    ctx.add(finding("BANK_ACCOUNT_DIFFERENT_DOC", f"Form {label} (ending {screening.mask(fv)[-4:]}) is a different account from the "
                                    f"one the bank letter confirms (ending {screening.mask(dv)[-4:]}).",
                                    field=fld, evidence={"form": screening.mask(fv), "letter": screening.mask(dv)}))
        di, fi = ctx.doc_val("bank_proof", "ifsc"), f.get("ifsc")
        if di and fi and di.strip().upper() != fi.strip().upper():
            ctx.add(finding("BANK_ACCOUNT_MISMATCH_DOC", "IFSC on form differs from the bank letter.", field="ifsc",
                            ask="The IFSC on your form does not match your bank letter. Please confirm the correct IFSC."))
        dh = ctx.doc_val("bank_proof", "account_holder")
        if dh and ctx.docs["bank_proof"].get("scanned") and nm.ocr_fix_name(dh) != dh:
            fixed = nm.ocr_fix_name(dh)
            ctx.add(finding("DOC_OCR_RECONCILED", f"The scanned bank letter reads the holder as '{dh}'; read as '{fixed}' (digit/letter scan misread).",
                            field="bank_proof.account_holder", evidence={"read": dh, "corrected": fixed}))
            dh = fixed
        if dh:
            holder = dh
            if holder_form and nm.compare_names(holder_form, dh)["verdict"] == "different":
                ctx.add(finding("ACCOUNT_HOLDER_MISMATCH", f"Form says holder '{holder_form}' but the bank letter says '{dh}'.",
                                field="account_holder", evidence={"form": holder_form, "letter": dh},
                                ask=f"The account holder on your form ('{holder_form}') differs from your bank letter ('{dh}'). Please confirm the correct account holder."))
    if holder:
        trade = f.get("trade_name", "")
        vs_legal = nm.compare_names(holder, authority) if authority else None
        vs_trade = nm.compare_names(holder, trade) if trade else None
        ask = (f"The bank account is in the name '{holder}', which does not match your registered company name ('{authority}'). "
               f"Please provide an account in the company's name, or a letter from your bank/company explaining the difference.")
        if vs_legal and vs_legal["verdict"] in ("exact", "equivalent"):
            if vs_legal["verdict"] == "equivalent":
                ctx.add(finding("NAME_VARIANT", f"Account holder '{holder}' = legal name '{authority}': {vs_legal['why']}.", field="account_holder", evidence=vs_legal))
        elif vs_trade and vs_trade["verdict"] in ("exact", "equivalent"):
            ctx.add(finding("NAME_TRADE_NAME", f"Account holder '{holder}' matches the declared trade name '{trade}', not the legal name '{authority}'.",
                            field="account_holder", evidence={"holder": holder, "trade_name": trade, "legal_name": authority}))
        elif vs_legal and vs_legal["verdict"] == "form_mismatch":
            ctx.add(finding("FORM_LEGAL_FORM_MISMATCH", f"Account holder '{holder}' vs '{authority}': {vs_legal['why']}.", field="account_holder", evidence=vs_legal,
                            ask=f"The account holder '{holder}' and your registered name '{authority}' differ in legal form. Please confirm the exact registered name."))
        elif vs_legal and vs_legal["verdict"] == "grey" and nm.replaced_word(holder, authority):
            # same family name, one whole word swapped ('... Foods' / '... Trading'): a sister company, not a spelling
            ctx.add(finding("THIRD_PARTY_PAYEE", f"Account holder '{holder}' is a different company from the vendor '{authority}' "
                            f"(the names differ by a whole word: {' / '.join(' '.join(x) for x in nm.replaced_word(holder, authority))}).",
                            field="account_holder", evidence={"holder": holder, "legal_name": authority, **vs_legal}))
        elif vs_legal and vs_legal["verdict"] == "grey":
            ctx.grey.append({"a": holder, "b": authority, "field": "account_holder", "code": "ACCOUNT_HOLDER_MISMATCH", "ask": ask, "cmp": vs_legal})
        elif vs_legal:
            # a clearly different company or a private person: payments would leave the vendor
            ctx.add(finding("THIRD_PARTY_PAYEE", f"Account holder '{holder}' is not the vendor '{authority}': {vs_legal['why']}.",
                            field="account_holder", evidence={"holder": holder, "legal_name": authority, **vs_legal}))
    if ctx.usable("bank_proof") and not (ctx.doc_val("bank_proof", "account_number") or ctx.doc_val("bank_proof", "iban")) \
            and not any(str(x.get("field", "")).startswith("bank_proof.") for x in ctx.findings):
        ctx.add(finding("DOC_INCOMPLETE", "The bank document does not show an account number or IBAN.", field="bank_proof",
                        ask=f"Your {DOC_LABEL['bank_proof']} does not show the account number. Please send one that shows the account number or IBAN."))
    # ---- 5b. street number: form vs the tax certificate (text-layer documents only) ------
    cert_addr = ctx.doc_val("tax_cert", "address") if ctx.usable("tax_cert") and not ctx.docs["tax_cert"].get("scanned") else None
    form_no, cert_nos = _street_numbers(f.get("address", "")), _street_numbers(cert_addr or "")
    squash = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())  # noqa: E731
    same_place = bool(cert_addr) and any(x and squash(x) in squash(cert_addr) for x in (f.get("postal_code"), f.get("city")))
    # same postcode/city but another street number = a slip to confirm; a different city is a different
    # site (e.g. a branch registration), which other rules handle
    if same_place and form_no and cert_nos and form_no[0] not in cert_nos:
        ctx.add(finding("ADDRESS_MISMATCH_DOC", f"Form address '{f.get('address')}' has street number {form_no[0]}; the tax certificate says '{cert_addr}'.",
                        field="address", evidence={"form": f.get("address"), "certificate": cert_addr},
                        ask=f"The address on your form ('{f.get('address')}') differs from your tax certificate ('{cert_addr}'). Please confirm your registered address."))
    # ---- 6. contact email -----------------------------------------------------------
    dom = (f.get("contact_email", "").split("@")[-1] or "").lower()
    if dom in config.FREE_EMAIL_DOMAINS and f.get("entity_type") not in ("sole_prop", ""):
        ctx.add(finding("FREE_EMAIL_DOMAIN", f"Company contact uses a free email domain ({dom}).", field="contact_email"))
    fake = _lookalike_domain(f.get("contact_email", ""), f.get("legal_name", ""), f.get("trade_name", "")) if "@" in f.get("contact_email", "") else None
    if fake:
        ctx.add(finding("EMAIL_LOOKALIKE_DOMAIN", f"The contact domain '{dom}' spells '{fake}' with digits in place of letters.",
                        field="contact_email", evidence={"domain": dom, "imitates": fake}))
    new = [x["code"] for x in ctx.findings if x["category"] in ("consistency",)]
    return {"headline": f"Cross-referenced form, documents and bank details: {len(new)} observation(s)." if new else "Form, documents and bank details are consistent.",
            "details": {"signals": ctx.facts["signals"], "authoritative_legal_name": authority, "account_holder_used": holder,
                        "grey_zone_pairs": [(g["a"], g["b"]) for g in ctx.grey]}}


def s_name_judgment(ctx: Ctx):
    if not ctx.grey:
        return {"headline": "No ambiguous name differences; the AI was not needed.", "details": {}}
    out = []
    for g in ctx.grey:
        j = ctx.llm.judge_names(g["a"], g["b"], {"country": ctx.country, "declared_legal_form": ctx.form.get("entity_type"),
                                                 "deterministic_comparison": g["cmp"]["why"]})
        yes = j.get("same_entity") == "yes" and float(j.get("confidence", 0)) >= config.LLM_MIN_CONFIDENCE
        extra = nm.extra_words(g["a"], g["b"])
        if yes and extra:                                       # code checks the AI: a whole extra word may be
            yes = False                                         # another registered company -> ask, don't assume
            j = {**j, "overridden": True,
                 "reasoning": f"The AI said 'same company', but one name has an extra word ({', '.join(extra)}) that can "
                              f"denote a different registered company, so the rulebook does not accept it without confirmation. "
                              f"AI said: {j.get('reasoning', '')}"}
        if j.get("backup"):
            ctx.facts.setdefault("ai_backup", []).append("name comparison")
        out.append({"a": g["a"], "b": g["b"], **j, "accepted": yes})
        if yes:
            ctx.add(finding("NAME_VARIANT_AI", f"'{g['a']}' and '{g['b']}' judged the same company ({j.get('confidence'):.0%}): {j.get('reasoning')}",
                            field=g["field"], evidence={**g["cmp"], "ai": j}))
        else:
            ctx.add(finding(g["code"], f"'{g['a']}' vs '{g['b']}' could not be reconciled: {j.get('reasoning')}",
                            field=g["field"], evidence={**g["cmp"], "ai": j}, ask=g["ask"]))
    ok = sum(1 for o in out if o["accepted"])
    return {"headline": f"AI reviewed {len(out)} ambiguous name pair(s): {ok} accepted as the same company, {len(out) - ok} left unresolved.",
            "details": {"judgments": out}}


def s_screening(ctx: Ctx):
    vid, f = ctx.vendor["vendor_id"], ctx.form
    cert_name = ctx.doc_val("tax_cert", "legal_name") if ctx.usable("tax_cert") else None
    names = [x for x in (f.get("legal_name"), f.get("trade_name"), cert_name) if x]
    # the PAYEE is screened too: a clean company asking to be paid into a listed party's account is a hard stop
    letter_holder = ctx.doc_val("bank_proof", "account_holder") if ctx.usable("bank_proof") else None
    payees = [x for x in (f.get("account_holder"), letter_holder) if x and x not in names]
    hit = screening.sanctions_best_match(names + payees)
    tax_norm = ctx.tax.normalized if ctx.tax and ctx.tax.ok else (f.get("tax_id") or "")
    facts = {"sanctions_best_score": hit["score"] if hit else 0}
    if hit and hit.get("exact", hit["score"] >= config.SANCTIONS_REJECT):
        ctx.add(finding("SANCTIONS_HIT", f"'{hit['candidate']}' matches sanctions-list entry {hit['entry_id']} ('{hit['listed_as']}').", field="legal_name", evidence=hit))
    elif hit and hit["score"] >= config.SANCTIONS_REVIEW:
        ctx.add(finding("SANCTIONS_NEAR", f"'{hit['candidate']}' is {hit['score']:.0%} similar to sanctions entry {hit['entry_id']} ('{hit['listed_as']}').", field="legal_name", evidence=hit))
    for v in screening.find_same_taxid(ctx.conn, vid, ctx.country, tax_norm):
        ctx.add(finding("TAXID_DUPLICATE_OTHER_NAME", f"Tax ID {tax_norm} is already registered to {v['legal_name']} ({v['vendor_id']}, {v['status']}).",
                        field="tax_id", evidence=v))
    for v in screening.find_shared_account(ctx.conn, vid, ctx.account_key):
        ctx.add(finding("BANK_ACCOUNT_SHARED", f"This bank account already belongs to {v['legal_name']} ({v['vendor_id']}, {v['status']}).",
                        field="bank", evidence=v))
    for v in screening.find_lookalikes(ctx.conn, vid, names, tax_norm):
        ctx.add(finding("LOOKALIKE_VENDOR", f"Name looks like existing vendor '{v['legal_name']}' ({v['vendor_id']}, {v['status']}): {v.get('why', 'similar')}, but the tax ID is different.",
                        field="legal_name", evidence=v))
    old_key = (ctx.vendor.get("data") or {}).get("approved_account_key")
    if old_key and ctx.account_key and old_key != ctx.account_key:
        ctx.add(finding("BANK_CHANGE_REQUEST", f"Approved vendor {vid} is submitting different bank details than the ones we have approved.",
                        field="bank", evidence={"approved_account": screening.mask(old_key.split(':')[-1]), "submitted_account": screening.mask(ctx.account_key.split(':')[-1])}))
    # ---- contact channel: who receives payment notices and bank-change confirmations ----
    email = (f.get("contact_email") or "").strip().lower()
    if email:
        prev = screening.previous_contact(ctx.conn, vid, ctx.sub.get("submission_id"))
        approved_before = bool((ctx.vendor.get("data") or {}).get("approved_account_key"))
        if approved_before and prev and prev.split("@")[-1] != email.split("@")[-1]:
            ctx.add(finding("CONTACT_CHANGE", f"Approved vendor {vid} is changing its contact email from the {prev.split('@')[-1]} domain to {email.split('@')[-1]}.",
                            field="contact_email", evidence={"on_file_domain": prev.split("@")[-1], "submitted_domain": email.split("@")[-1]}))
        for v in screening.find_shared_contact(ctx.conn, vid, email):
            ctx.add(finding("CONTACT_SHARED", f"Contact email {email} is already used by {v['legal_name']} ({v['vendor_id']}, {v['status']}).",
                            field="contact_email", evidence=v))
    for v in screening.other_vendors(ctx.conn, vid):
        if v["status"] == "rejected" and v["substatus"] == "rejected_compliance" and (
                (v["tax_id"] and v["tax_id"] == tax_norm) or (v["account_key"] and v["account_key"] == ctx.account_key)):
            ctx.add(finding("PRIOR_REJECTED", f"Matches {v['legal_name']} ({v['vendor_id']}), previously rejected.", field="legal_name", evidence={"vendor_id": v["vendor_id"]}))
    n = sum(1 for x in ctx.findings if x["category"] in ("fraud", "compliance"))
    return {"headline": f"Screened against sanctions list and {ctx.conn.execute('SELECT COUNT(*) c FROM vendors').fetchone()['c'] - 1} existing vendor(s): "
                        + (f"{n} flag(s) raised." if n else "no flags."),
            "details": facts}


def s_decision(ctx: Ctx):
    d = rules.decide(ctx.findings)
    ctx.findings = d["findings"]
    ctx.facts["decision"] = d
    return {"headline": f"{d['status'].upper()} ({d['substatus'].replace('_', ' ')}), risk {d['risk_score']}/100. {d['trace'][0]}",
            "details": {"status": d["status"], "substatus": d["substatus"], "risk_score": d["risk_score"],
                        "risk_limit": d["risk_limit"], "rule_trace": d["trace"], "driving_codes": d["driving"]}}


def _next_step(codes):
    if codes & {"SANCTIONS_HIT", "SANCTIONS_NEAR", "PRIOR_REJECTED"}:
        return "escalate to Compliance; do not contact the vendor about the screening result"
    if codes & {"DOC_CONFLICTING_VALUES"}:
        return ("treat the document as edited: confirm the account directly with the bank, using contact details from the bank's "
                "own website (never from this submission), before any approval")
    if codes & {"BANK_CHANGE_REQUEST", "BANK_ACCOUNT_SHARED", "LOOKALIKE_VENDOR", "TAXID_DUPLICATE_OTHER_NAME", "CONTACT_CHANGE", "CONTACT_SHARED"}:
        return ("phone the number ALREADY ON FILE for the existing vendor (not the number in this submission) "
                "and confirm the details before any approval")
    return "contact the vendor through the registered channel to clarify, and do not approve until reconciled"


def s_communicate(ctx: Ctx):
    d, vend = ctx.facts["decision"], ctx.vendor
    ref, name = vend["vendor_id"], ctx.form.get("legal_name") or "your company"
    token_url = f"{config.BASE_URL.rstrip('/')}/portal/{vend['token']}"
    due = db.now(ctx.conn) + timedelta(days=config.VENDOR_RESPONSE_DAYS)
    sub, st = d["substatus"], d["status"]
    drafts = []

    def write(audience, kind, ctxd, to):
        try:
            mail = ctx.llm.draft_email(audience, kind, ctxd)
            if audience == "vendor" and not vendor_mail_is_safe(mail["subject"] + mail["body"]):
                raise LLMError("vendor email mentioned internal screening terms")
        except Exception as e:                     # guardrail or outage -> deterministic template
            from .llm import _template_email
            mail = _template_email(audience, kind, ctxd)
            mail["fallback_reason"] = str(e)[:120]
        drafts.append({"audience": audience, "kind": kind, "to": to, **mail})

    base = {"vendor_name": name, "reference": ref, "portal_url": token_url, "due_date": due.strftime("%d %b %Y")}
    to_vendor = ctx.form.get("contact_email", "")
    if sub == "awaiting_vendor":
        asks = list(dict.fromkeys(f["vendor_ask"] for f in d["findings"] if f["action"] == "vendor" and f.get("vendor_ask")))
        write("vendor", "request", {**base, "asks": asks}, to_vendor)
    elif st == "approved":
        write("vendor", "approved", base, to_vendor)
    elif sub == "awaiting_reviewer" or sub == "rejected_compliance":
        codes = set(d["driving"]) | {f["code"] for f in d["findings"] if f["action"] in ("reviewer", "reject")}
        items = [f"{f['title']}: {f['message']}" for f in d["findings"] if f["action"] in ("reviewer", "reject", "vendor")]
        checks = [a["action"] for a in workflow.next_actions(d["findings"], st, sub) if a["who"] in ("reviewer", "compliance")]
        write("reviewer", "review", {**base, "status": st, "asks": items,
                                     "next_step": _next_step(codes) + (". Checklist: " + " | ".join(checks) if checks else "")},
              "compliance@internal" if codes & workflow.COMPLIANCE else "procurement-review@internal")
    elif sub == "rejected_closed":
        write("vendor", "rejected", base, to_vendor)
    for m in drafts:
        ctx.conn.execute("INSERT INTO emails(vendor_id,run_id,audience,kind,to_addr,subject,body,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (ref, ctx.run_id, m["audience"], m["kind"], m["to"], m["subject"], m["body"], "drafted", db.iso(db.now(ctx.conn))))
        db.audit(ctx.conn, "email_drafted", {"audience": m["audience"], "kind": m["kind"], "subject": m["subject"]}, ref, ctx.run_id)
    ctx.emails = drafts
    ctx.facts["due_at"] = db.iso(due) if sub == "awaiting_vendor" else None
    head = (f"Drafted {len(drafts)} message(s): " + ", ".join(f"{m['audience']} ({m['kind']})" for m in drafts)) if drafts \
        else "No message needed for this outcome."
    if sub == "rejected_compliance":
        head += " The vendor is NOT told why (compliance practice)."
    return {"headline": head, "details": {"emails": drafts}}


# ================================================================== entry ====
STAGES = [
    ("intake", "Intake", s_intake),
    ("read_documents", "Read documents", s_read_documents),
    ("completeness", "Completeness check", s_completeness),
    ("validate", "Format & checksum validation", s_validate),
    ("cross_check", "Cross-reference fields", s_cross_check),
    ("name_judgment", "Resolve ambiguous names", s_name_judgment),
    ("risk_screening", "Risk screening", s_screening),
    ("decision", "Decision", s_decision),
    ("communicate", "Communicate", s_communicate),
]


def process_submission(conn, submission_id: str, llm=None, emit=None) -> dict:
    emit = emit or (lambda e: None)
    llm = llm or get_llm()
    sub = db.row(conn.execute("SELECT * FROM submissions WHERE submission_id=?", (submission_id,)).fetchone())
    vendor = db.row(conn.execute("SELECT * FROM vendors WHERE vendor_id=?", (sub["vendor_id"],)).fetchone())
    vendor["data"] = vendor["data"] if isinstance(vendor["data"], dict) else {}
    run_id = db.next_id(conn, "runs", "run_id", "R-", 5)
    started = db.iso(db.now(conn))
    conn.execute("INSERT INTO runs(run_id,vendor_id,submission_id,status,llm_mode,started_at) VALUES(?,?,?,?,?,?)",
                 (run_id, vendor["vendor_id"], submission_id, "running", llm.mode, started))
    db.audit(conn, "run_started", {"submission": submission_id, "version": sub["version"], "llm_mode": llm.mode}, vendor["vendor_id"], run_id)
    emit({"type": "run_started", "run_id": run_id, "vendor_id": vendor["vendor_id"], "llm_mode": llm.mode, "version": sub["version"]})
    prior = conn.execute("SELECT result FROM runs WHERE vendor_id=? AND status!='running' AND run_id!=? ORDER BY run_id DESC LIMIT 1",
                         (vendor["vendor_id"], run_id)).fetchone()
    ctx = Ctx(conn, llm, vendor, sub, run_id, emit)
    t0 = time.time()
    for i, (name, title, fn) in enumerate(STAGES, 1):
        _stage(ctx, i, name, title, fn)
        if name == "decision" and any(f["code"] == "STAGE_ERROR" for f in ctx.findings) and "decision" not in ctx.facts:
            pass
    if "decision" not in ctx.facts:                              # decision stage itself crashed
        ctx.facts["decision"] = rules.decide(ctx.findings)
    d = ctx.facts["decision"]
    # --- what changed since the last run (the resubmission story) --------------------
    resolved = []
    if prior and prior["result"]:
        old = json.loads(prior["result"])
        old_codes = {(f["code"], f.get("field")) for f in old.get("findings", []) if f["action"] != "none"}
        new_codes = {(f["code"], f.get("field")) for f in d["findings"] if f["action"] != "none"}
        resolved = sorted(f"{c}:{fld}" if fld else c for c, fld in old_codes - new_codes)
    explanation = rules.plain_english(d, ctx.form.get("legal_name") or vendor["vendor_id"])
    result = {"status": d["status"], "substatus": d["substatus"], "risk_score": d["risk_score"], "risk_limit": d["risk_limit"],
              "explanation": explanation, "rule_trace": d["trace"], "driving": d["driving"], "findings": d["findings"],
              "resolved_since_last_run": resolved, "emails": ctx.emails, "llm_mode": llm.mode,
              "ai_backup": ctx.facts.get("ai_backup", []),
              "next_actions": workflow.next_actions(d["findings"], d["status"], d["substatus"]),
              "documents": {k: {"detected_as": v.get("detected"), "grounding": v.get("grounding"), "fields": v.get("values")} for k, v in ctx.docs.items()},
              "seconds": round(time.time() - t0, 2)}
    finished = db.iso(db.now(conn))
    conn.execute("UPDATE runs SET status=?,substatus=?,risk_score=?,finished_at=?,result=? WHERE run_id=?",
                 (d["status"], d["substatus"], d["risk_score"], finished, json.dumps(result, default=str), run_id))
    # --- update vendor master ----------------------------------------------------------
    data = dict(vendor["data"])
    for k in ("reviewer_asks", "first_approver", "first_approval_reason"):   # a new run starts a fresh review
        data.pop(k, None)
    if d["status"] == "approved":
        data["approved_account_key"] = ctx.account_key
        data["approved_legal_name"] = ctx.form.get("legal_name")
    tax_norm = ctx.tax.normalized if ctx.tax and ctx.tax.ok else (ctx.form.get("tax_id") or "")
    conn.execute("""UPDATE vendors SET legal_name=?,country=?,tax_id=?,account_key=?,status=?,substatus=?,risk_score=?,
                    updated_at=?,due_at=?,latest_run_id=?,version=?,data=? WHERE vendor_id=?""",
                 (ctx.form.get("legal_name"), ctx.country, tax_norm, ctx.account_key, d["status"], d["substatus"], d["risk_score"],
                  finished, ctx.facts.get("due_at"), run_id, sub["version"], json.dumps(data), vendor["vendor_id"]))
    db.audit(conn, "decision", {"status": d["status"], "substatus": d["substatus"], "risk_score": d["risk_score"],
                                "codes": [f["code"] for f in d["findings"]], "trace": d["trace"], "resolved": resolved},
             vendor["vendor_id"], run_id)
    emit({"type": "run_finished", "run_id": run_id, "vendor_id": vendor["vendor_id"], "status": d["status"],
          "substatus": d["substatus"], "risk_score": d["risk_score"], "explanation": explanation,
          "resolved_since_last_run": resolved})
    return {"run_id": run_id, "vendor_id": vendor["vendor_id"], **result}
