"""The rulebook and the decision engine.

DESIGN PRINCIPLE: AI reads and explains; THIS FILE decides. A model never approves,
rejects or holds a vendor. Every outcome traces back to a rule code in RULEBOOK, and
the same table is shown in the UI so a non-technical reviewer can read and challenge it.

action:
  none      informational, adds risk points only
  vendor    vendor can fix it  -> pending / awaiting_vendor (an email is sent)
  reviewer  needs a human      -> pending / awaiting_reviewer (NOT disclosed to vendor)
  reject    hard compliance stop -> rejected
"""
from __future__ import annotations

from . import config

# code: (category, severity, action, risk_points, title, why-this-rule-exists)
_R = [
    # --- completeness ------------------------------------------------------------
    ("MISSING_FIELD", "completeness", "warn", "vendor", 0, "Required field missing",
     "We cannot pay or verify a vendor without the basic details."),
    ("MISSING_DOC", "completeness", "warn", "vendor", 0, "Required document missing",
     "Tax and bank documents are the evidence behind the form."),
    ("DOC_UNREADABLE", "completeness", "warn", "vendor", 5, "Document unreadable",
     "If we cannot read it we cannot rely on it."),
    ("WRONG_DOCUMENT", "completeness", "warn", "vendor", 5, "Wrong document attached",
     "A frequent honest mistake; easy for the vendor to fix."),
    ("DOC_STALE", "completeness", "warn", "vendor", 5, "Document too old",
     "Bank details change; an old letter proves little."),
    ("DOC_UNGROUNDED", "completeness", "warn", "reviewer", 10, "Extracted value not found in document text",
     "Guards against the AI reading something that is not actually on the page."),
    ("DOC_UNDATED", "completeness", "warn", "vendor", 5, "Bank letter has no date",
     "We can only accept a bank letter we can show is recent (90 days)."),
    ("DOC_NOT_ACCEPTED", "completeness", "warn", "vendor", 5, "Document type not accepted as proof",
     "A bank statement shows transactions, not a bank's confirmation of the account; we ask for a letter or cancelled cheque."),
    ("DOC_SLOTS_SWAPPED", "completeness", "info", "none", 0, "Documents uploaded into each other's slots",
     "Both documents were provided; we read each one as what it actually is."),
    # --- format ------------------------------------------------------------------
    ("TAXID_FORMAT", "format", "warn", "vendor", 5, "Tax ID has the wrong format", "Each country has a fixed ID format."),
    ("TAXID_CHECKSUM", "format", "warn", "vendor", 5, "Tax ID check digit fails", "Usually a typo."),
    ("BANK_FORMAT", "format", "warn", "vendor", 5, "Bank detail has the wrong format", "Payments to a malformed account fail or misroute."),
    ("BANK_CHECKSUM", "format", "warn", "vendor", 5, "Bank detail check digit fails", "Usually a typo; catching it now avoids a failed payment."),
    ("EMAIL_INVALID", "format", "warn", "vendor", 0, "Contact email invalid", "We must be able to reach the vendor."),
    ("DOC_INCOMPLETE", "completeness", "warn", "vendor", 5, "Document does not show the number it is meant to prove",
     "A tax form with an empty tax-number box, or a bank letter without an account number, confirms nothing."),
    # --- consistency (needs two sources to see) ------------------------------------
    ("TAXID_MISMATCH_DOC", "consistency", "warn", "vendor", 15, "Tax ID on form differs from certificate", "One of the two is wrong or the wrong certificate was sent."),
    ("NAME_MISMATCH_TAXCERT", "consistency", "warn", "vendor", 20, "Company name differs from tax certificate", "The tax certificate is the authority on the legal name."),
    ("ADDRESS_MISMATCH_DOC", "consistency", "warn", "vendor", 5, "Street address on the form differs from the tax certificate",
     "The registered address is part of the identity we verify; usually a typo the vendor can confirm."),
    ("ACCOUNT_HOLDER_MISMATCH", "consistency", "warn", "vendor", 25, "Bank account is not in the vendor's name", "Paying a different name is a classic fraud pattern."),
    ("BANK_ACCOUNT_MISMATCH_DOC", "consistency", "warn", "vendor", 20, "Account on form differs from bank letter", "Either a typo or a tampered document."),
    ("BANK_CODE_MISMATCH", "consistency", "warn", "reviewer", 20, "Bank name does not match the bank code", "The IFSC / IBAN bank code / sort code belongs to a different bank from the one stated."),
    ("IBAN_PARTS_MISMATCH", "consistency", "warn", "vendor", 15, "IBAN disagrees with the sort code / account number", "A UK IBAN contains the sort code and account number; both must agree."),
    ("PAN_NAME_MISMATCH", "consistency", "block", "reviewer", 30, "Tax ID belongs to a differently named entity", "The 5th character of a company PAN is the first letter of its name; this one points to another business."),
    ("GSTIN_STATE_MISMATCH", "consistency", "block", "reviewer", 30, "Certificate address is in a different state from its GSTIN", "A GST certificate is issued per state; an address elsewhere means the certificate is internally inconsistent."),
    ("GSTIN_BRANCH_STATE", "consistency", "info", "none", 5, "Form address is in another state from the GSTIN", "Common when a head office uses a branch's registration; the certificate itself is consistent."),
    ("DOC_OCR_RECONCILED", "completeness", "info", "none", 0, "Scan misread reconciled", "A scanned digit read as a look-alike letter (0/O, 1/I, 5/S, 8/B); the form value is confirmed."),
    ("FORM_LEGAL_FORM_MISMATCH", "consistency", "warn", "vendor", 10, "Legal form differs between sources", "'Ltd' and 'LLP' are different legal entities."),
    ("ENTITY_TYPE_MISMATCH_PAN", "consistency", "block", "reviewer", 30, "Tax ID says a different kind of entity", "e.g. a PAN issued to an individual, but the vendor claims to be a company."),
    ("ENTITY_TYPE_FORM_ERROR", "consistency", "warn", "vendor", 10, "Entity type on the form contradicts the tax ID or the company's own name",
     "The tax ID or the name ('Inc', 'GmbH') shows one kind of company but a different type was selected; usually a form slip."),
    ("THIRD_PARTY_PAYEE", "fraud", "block", "reviewer", 40, "Payments would go to someone other than the vendor",
     "An account held by another company or a private person is the classic payment-diversion route; it needs a human check (and e.g. an assignment agreement)."),
    ("BANK_ACCOUNT_DIFFERENT_DOC", "fraud", "block", "reviewer", 40, "Form account is a different account from the bank letter",
     "Not a typo: the account on the form is not the one the bank confirmed, so the form account is unverified."),
    ("TAXCERT_OTHER_ENTITY", "fraud", "block", "reviewer", 40, "Tax registration belongs to another company",
     "The tax ID on the form is registered to a differently named company: borrowed identity, not an upload slip."),
    ("TAXID_DIFFERENT_DOC", "fraud", "block", "reviewer", 40, "Form tax ID is a different number from the certificate",
     "Not a typo: two unrelated valid tax IDs for one company means one of them belongs to someone else."),
    ("DOC_FUTURE_DATED", "fraud", "block", "reviewer", 35, "Document is dated in the future",
     "A bank or tax office cannot issue a letter that has not been written yet; the document may be fabricated."),
    ("OFFSHORE_PAYMENT", "fraud", "block", "reviewer", 35, "Payment requested to a foreign account in a foreign currency",
     "A domestic supplier asking to be paid abroad in another currency is a tax-evasion and money-laundering pattern."),
    ("EMAIL_LOOKALIKE_DOMAIN", "fraud", "block", "reviewer", 40, "Contact email domain imitates the company name with digits",
     "'carro11ton' for 'Carrollton': real companies do not misspell their own name; impersonators register such domains."),
    ("COUNTRY_CONTRADICTION", "consistency", "block", "reviewer", 30, "Country contradicts tax ID or bank details", "Fields that cannot all be true at once."),
    ("BANK_COUNTRY_DIFFERS", "consistency", "info", "none", 15, "Bank is in a different country from the vendor", "Legitimate sometimes, but raises payment-fraud risk."),
    ("CURRENCY_UNUSUAL", "consistency", "info", "none", 5, "Currency unusual for the country", "Worth a glance."),
    ("CURRENCY_CONFIRM", "consistency", "warn", "vendor", 5, "Currency does not fit a domestic-only account", "A domestic account (no IBAN/SWIFT) in a foreign currency is almost always a dropdown slip."),
    ("FREE_EMAIL_DOMAIN", "consistency", "info", "none", 5, "Company uses a free email address", "Weak signal; real companies usually have their own domain."),
    ("NAME_VARIANT", "consistency", "info", "none", 0, "Name differs only in legal-form wording", "'Pvt Ltd' = 'Private Limited'. Not a problem; recorded for the audit trail."),
    ("NAME_VARIANT_AI", "consistency", "info", "none", 10, "Names judged to be the same company (AI-assisted)", "Grey-zone name difference resolved with a recorded explanation."),
    ("NAME_TRADE_NAME", "consistency", "info", "none", 5, "Bank account is in the declared trade name", "Acceptable when the trade name is declared and the legal name is confirmed by the certificate."),
    ("NAME_UNRESOLVED", "consistency", "warn", "vendor", 20, "Names could not be reconciled", "We could not confidently tell if these are the same company."),
    # --- document integrity --------------------------------------------------------
    ("DOC_CONFLICTING_VALUES", "fraud", "block", "reviewer", 45, "Document contains two different values for the same field", "Typical of a genuine document with a new value pasted over the old one."),
    ("DOC_EDITED", "fraud", "info", "none", 5, "Document was edited after it was created", "Saved with later changes. Often harmless (e.g. signed), worth a glance."),
    # --- screening ---------------------------------------------------------------
    ("SANCTIONS_HIT", "compliance", "block", "reject", 100, "Matches the sanctions list", "We are legally barred from paying listed parties."),
    ("SANCTIONS_NEAR", "compliance", "block", "reviewer", 40, "Close to a sanctions-list name", "A human must rule out a false positive."),
    ("LOOKALIKE_VENDOR", "fraud", "block", "reviewer", 40, "Name looks like an existing vendor, but the tax ID differs", "Classic impersonation pattern."),
    ("BANK_ACCOUNT_SHARED", "fraud", "block", "reviewer", 50, "Bank account already belongs to another vendor", "Two unrelated vendors rarely share one account."),
    ("BANK_CHANGE_REQUEST", "fraud", "block", "reviewer", 50, "Existing vendor is asking to change bank details", "The most common payment-fraud route; verify by phone."),
    ("TAXID_DUPLICATE_OTHER_NAME", "fraud", "block", "reviewer", 40, "Tax ID already registered under a different name", "Same registration, different company name."),
    ("CONTACT_CHANGE", "fraud", "block", "reviewer", 45, "Existing vendor's contact email is changing to a different domain",
     "Payment notices and bank-change confirmations go to this address; taking it over is how account-takeover fraud starts."),
    ("CONTACT_SHARED", "fraud", "block", "reviewer", 40, "Contact email already belongs to a different vendor",
     "Two unrelated companies rarely share one mailbox; it can be someone onboarding in another company's name."),
    ("PRIOR_REJECTED", "fraud", "block", "reviewer", 40, "Matches a previously rejected vendor", "Do not let a rejected vendor back in quietly."),
    # --- lifecycle ---------------------------------------------------------------
    ("STALE_NO_RESPONSE", "lifecycle", "block", "reject", 0, "Vendor did not respond in time", "Requests cannot stay open forever."),
    ("RISK_OVER_LIMIT", "lifecycle", "block", "reviewer", 0, "Combined risk score above the auto-approve limit", "Several small flags add up to a human look."),
    ("STAGE_ERROR", "system", "block", "reviewer", 0, "A processing step failed", "The system never guesses; a person takes over."),
    ("REVIEWER_OVERRIDE", "lifecycle", "info", "none", 0, "A reviewer changed the decision", "Recorded with a mandatory reason."),
]

RULEBOOK = {
    code: {"code": code, "category": cat, "severity": sev, "action": act, "points": pts,
           "title": title, "why": why}
    for code, cat, sev, act, pts, title, why in _R
}


def finding(code: str, message: str, field: str | None = None, ask: str | None = None,
            evidence: dict | None = None, points: int | None = None) -> dict:
    r = RULEBOOK[code]
    return {
        "code": code, "category": r["category"], "severity": r["severity"], "action": r["action"],
        "points": r["points"] if points is None else points, "title": r["title"],
        "field": field, "message": message, "vendor_ask": ask, "evidence": evidence or {},
    }


def _dedupe(findings: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in findings:
        k = (f["code"], f.get("field"))
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def decide(findings: list[dict], risk_limit: int | None = None) -> dict:
    """Deterministic decision. Same findings in -> same decision out."""
    limit = config.AUTO_APPROVE_MAX_RISK if risk_limit is None else risk_limit
    fs = _dedupe(findings)
    risk = min(100, sum(f["points"] for f in fs))
    rej = [f for f in fs if f["action"] == "reject"]
    rev = [f for f in fs if f["action"] == "reviewer"]
    ven = [f for f in fs if f["action"] == "vendor"]
    trace = []

    if rej:
        status, sub = "rejected", "rejected_compliance" if any(f["category"] == "compliance" for f in rej) else "rejected_closed"
        trace.append(f"RULE reject: {', '.join(sorted({f['code'] for f in rej}))}")
        driving = rej
    elif rev:
        status, sub = "pending", "awaiting_reviewer"
        trace.append(f"RULE hold-for-human: {', '.join(sorted({f['code'] for f in rev}))}")
        driving = rev
    elif ven:
        status, sub = "pending", "awaiting_vendor"
        trace.append(f"RULE request-info: {', '.join(sorted({f['code'] for f in ven}))}")
        driving = ven
    elif risk >= limit:
        extra = finding("RISK_OVER_LIMIT", f"Risk score {risk} is at or above the auto-approve limit of {limit}.")
        fs.append(extra)
        status, sub = "pending", "awaiting_reviewer"
        trace.append(f"RULE risk-limit: score {risk} >= {limit}")
        driving = [extra]
    else:
        status, sub = "approved", "approved"
        trace.append(f"RULE approve: no blocking findings and risk {risk} < {limit}")
        driving = []

    return {
        "status": status, "substatus": sub, "risk_score": risk, "risk_limit": limit,
        "driving": [f["code"] for f in driving], "trace": trace, "findings": fs,
    }


def plain_english(decision: dict, vendor_name: str) -> str:
    """One-paragraph explanation for a non-technical reader. No AI involved."""
    st, sub = decision["status"], decision["substatus"]
    drv = [f for f in decision["findings"] if f["code"] in decision["driving"]]
    titles = "; ".join(dict.fromkeys(f["title"].lower() for f in drv)) or "no issues"
    if st == "approved":
        return (f"{vendor_name} is approved. Every required detail and document is present, "
                f"they agree with each other, and screening found nothing (risk score {decision['risk_score']}/100).")
    if sub == "awaiting_vendor":
        return (f"{vendor_name} is on hold pending information from the vendor: {titles}. "
                f"An email asking for exactly what is needed has been drafted.")
    if sub == "awaiting_reviewer":
        return (f"{vendor_name} is on hold for a human reviewer: {titles}. "
                f"This is not an automatic rejection: the evidence is suspicious, not conclusive.")
    return f"{vendor_name} is rejected: {titles}."
