"""The human side of the process: what to do next, who does it, and by when.

  next_actions()   for every decision, the concrete path to approval (vendor asks or reviewer checks)
  review_queue()   items waiting for a person, ordered by SLA and risk
  review_action()  approve / reject / ask the vendor, with a mandatory reason; HIGH-RISK approvals
                   need a completed checklist AND a second, different approver (four-eyes)
"""
from __future__ import annotations

import json
from datetime import timedelta

from . import config, db, rules, service
from .llm import _template_email, vendor_mail_is_safe

REVIEW_SLA_HOURS = 48
# Approving any of these needs two different people (classic payment-fraud / compliance routes).
FOUR_EYES = {"BANK_CHANGE_REQUEST", "BANK_ACCOUNT_SHARED", "LOOKALIKE_VENDOR", "DOC_CONFLICTING_VALUES",
             "THIRD_PARTY_PAYEE", "BANK_ACCOUNT_DIFFERENT_DOC", "TAXCERT_OTHER_ENTITY", "TAXID_DIFFERENT_DOC",
             "DOC_FUTURE_DATED", "OFFSHORE_PAYMENT", "EMAIL_LOOKALIKE_DOMAIN",
             "TAXID_DUPLICATE_OTHER_NAME", "CONTACT_CHANGE", "CONTACT_SHARED", "SANCTIONS_NEAR", "PRIOR_REJECTED", "PAN_NAME_MISMATCH", "GSTIN_STATE_MISMATCH"}
COMPLIANCE = {"SANCTIONS_NEAR", "SANCTIONS_HIT", "PRIOR_REJECTED"}

# What a reviewer must actually verify before approving. Plain verbs, out-of-band where it matters.
PLAYBOOK = {
    "BANK_CHANGE_REQUEST": ["Phone the vendor on the number ALREADY ON FILE (not the one in this submission) and confirm the change",
                            "Keep paying the previously approved account until this is cleared"],
    "BANK_ACCOUNT_SHARED": ["Confirm with the EXISTING vendor (number on file) whether they know this company",
                            "Ask for a bank letter showing why two companies share one account"],
    "LOOKALIKE_VENDOR": ["Compare against the existing vendor's record: is this a real separate company?",
                         "Verify the new company in the official company register"],
    "DOC_CONFLICTING_VALUES": ["Open the original file: a value was pasted over another one",
                               "Ask the bank directly (contact details from the bank's website) to confirm the account"],
    "CONTACT_CHANGE": ["Phone the vendor on the number ALREADY ON FILE and confirm the new contact address is theirs",
                       "Until confirmed, send nothing (no bank details, no links) to the new address"],
    "CONTACT_SHARED": ["Ask the EXISTING vendor (number on file) whether this mailbox and this company are theirs",
                       "Verify the new company in the official company register"],
    "TAXID_DUPLICATE_OTHER_NAME": ["Check which company the tax ID really belongs to (government portal)"],
    "THIRD_PARTY_PAYEE": ["Ask the vendor (registered channel) why payments should go to this account holder",
                          "Require an assignment / factoring agreement signed by both parties, or an account in the vendor's own name",
                          "Confirm the account with the bank using contact details from the bank's own website"],
    "BANK_ACCOUNT_DIFFERENT_DOC": ["Do not use the form account: it is not the one the bank confirmed",
                                   "Ask the bank directly (contact details from its website) which account belongs to the vendor"],
    "TAXCERT_OTHER_ENTITY": ["Look up the tax ID on the government portal: who is it registered to?",
                             "Ask the vendor for its OWN registration certificate; do not approve on another company's"],
    "TAXID_DIFFERENT_DOC": ["Look up BOTH tax IDs on the government portal: which one is registered to this company?",
                            "Do not ask the vendor which is right before you know; one of them may be another company's"],
    "DOC_FUTURE_DATED": ["Ask the bank directly (contact details from its website) whether it issued this letter",
                         "Do not accept a re-dated copy from the vendor as proof"],
    "OFFSHORE_PAYMENT": ["Ask the vendor (registered channel) for the business reason for being paid abroad",
                         "Check with tax / treasury whether paying this supplier offshore is permitted"],
    "EMAIL_LOOKALIKE_DOMAIN": ["Find the company's real website and phone number independently and confirm who registered",
                               "Send nothing to the look-alike address until the company confirms it"],
    "ENTITY_TYPE_MISMATCH_PAN": ["Check the PAN on the income-tax portal: individual or company?"],
    "PAN_NAME_MISMATCH": ["Look up the GSTIN on the GST portal and confirm the registered name matches the vendor"],
    "GSTIN_STATE_MISMATCH": ["Look up the GSTIN on the GST portal and compare the registered address with the certificate"],
    "COUNTRY_CONTRADICTION": ["Ask the vendor which entity is contracting; the tax ID and bank are from another country"],
    "BANK_CODE_MISMATCH": ["Confirm with the vendor which bank holds the account; the bank code points elsewhere"],
    "SANCTIONS_NEAR": ["Compliance: compare address, country and owners with the list entry before clearing the match"],
    "PRIOR_REJECTED": ["Compliance: review why the earlier vendor was rejected before any contact"],
    "RISK_OVER_LIMIT": ["Review the small flags together and decide if they explain each other"],
    "STAGE_ERROR": ["Re-run the submission; if it fails again, review the documents by hand"],
    "DOC_UNGROUNDED": ["Open the document and confirm the extracted value is really printed on it"],
}


def next_actions(findings: list[dict], status: str, substatus: str) -> list[dict]:
    """The path to approval, in plain words. Same findings -> same actions (no AI)."""
    if status == "approved":
        return [{"who": "system", "action": "Vendor master record created from the verified data, ready to load into the ERP."},
                {"who": "system", "action": "Payments can be set up on the verified bank account."}]
    if substatus == "rejected_compliance":
        return [{"who": "compliance", "action": "Confirm the sanctions match and file the case. Do not contact the vendor about it."}]
    out, seen = [], set()
    for f in findings:
        if f["action"] == "reviewer":
            for step in PLAYBOOK.get(f["code"], [f"Resolve: {f['title']}"]):
                if step not in seen:
                    seen.add(step)
                    out.append({"who": "compliance" if f["code"] in COMPLIANCE else "reviewer", "action": step, "code": f["code"]})
    if substatus == "awaiting_vendor":
        for f in findings:
            if f["action"] == "vendor" and f.get("vendor_ask") and f["vendor_ask"] not in seen:
                seen.add(f["vendor_ask"])
                out.append({"who": "vendor", "action": f["vendor_ask"], "code": f["code"]})
    if any(f["code"] in FOUR_EYES and f["action"] == "reviewer" for f in findings):
        out.append({"who": "reviewer", "action": "Approval needs two different reviewers (four-eyes rule)."})
    return out


# ------------------------------------------------------------------ queue ----
def _latest_result(conn, vendor_id):
    r = conn.execute("SELECT result FROM runs WHERE vendor_id=? AND status!='running' ORDER BY run_id DESC LIMIT 1", (vendor_id,)).fetchone()
    return json.loads(r["result"]) if r and r["result"] else {}


def review_queue(conn) -> list[dict]:
    now = db.now(conn)
    items = []
    for v in conn.execute("SELECT * FROM vendors WHERE substatus IN ('awaiting_reviewer','awaiting_second_approval')"):
        res = _latest_result(conn, v["vendor_id"])
        drv = res.get("driving", [])
        data = json.loads(v["data"] or "{}")
        since = service._parse(v["updated_at"])
        due = since + timedelta(hours=REVIEW_SLA_HOURS)
        hours_left = (due - now).total_seconds() / 3600
        items.append({
            "vendor_id": v["vendor_id"], "legal_name": v["legal_name"], "country": v["country"], "substatus": v["substatus"],
            "risk_score": v["risk_score"], "codes": drv, "titles": [rules.RULEBOOK[c]["title"] for c in drv if c in rules.RULEBOOK],
            "queue": "compliance" if set(drv) & COMPLIANCE else "procurement",
            "four_eyes": bool(set(drv) & FOUR_EYES), "first_approver": data.get("first_approver"),
            "waiting_since": v["updated_at"], "due_at": db.iso(due), "hours_left": round(hours_left, 1), "overdue": hours_left < 0,
            "checklist": [s for c in drv for s in PLAYBOOK.get(c, [])],
        })
    items.sort(key=lambda x: (not x["overdue"], x["hours_left"] - x["risk_score"] / 4))
    return items


# ----------------------------------------------------------------- actions ----
def review_action(conn, vendor_id: str, action: str, actor: str, reason: str, checklist_done: bool = False,
                  asks: list[str] | None = None) -> dict:
    if action not in ("approve", "reject", "request_info"):
        raise ValueError("action must be approve, reject or request_info")
    if not reason or len(reason.strip()) < 8:
        raise ValueError("A reason of at least 8 characters is required for every decision.")
    actor = (actor or "").strip() or "reviewer"
    v = db.row(conn.execute("SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)).fetchone())
    if not v:
        raise KeyError(vendor_id)
    if v["substatus"] not in ("awaiting_reviewer", "awaiting_second_approval"):
        raise ValueError(f"{vendor_id} is not waiting for a reviewer (it is '{v['substatus']}').")
    data = v["data"] if isinstance(v["data"], dict) else {}
    res = _latest_result(conn, vendor_id)
    drv = set(res.get("driving", []))
    now = db.iso(db.now(conn))
    form = service._latest_form(conn, vendor_id)
    to = form.get("contact_email", "")
    base = {"vendor_name": v["legal_name"], "reference": vendor_id,
            "portal_url": f"{config.BASE_URL.rstrip('/')}/portal/{v['token']}"}

    def log(new_status, new_sub, extra=None):
        conn.execute("UPDATE vendors SET status=?, substatus=?, updated_at=?, data=? WHERE vendor_id=?",
                     (new_status, new_sub, now, json.dumps(data), vendor_id))
        conn.execute("INSERT INTO overrides(vendor_id,old_status,new_status,reason,actor,at) VALUES(?,?,?,?,?,?)",
                     (vendor_id, v["substatus"], new_sub, reason.strip(), actor, now))
        db.audit(conn, "review_action", {"action": action, "from": v["substatus"], "to": new_sub, "actor": actor,
                                         "reason": reason.strip(), "checklist_done": checklist_done, **(extra or {})}, vendor_id)
        return {"vendor_id": vendor_id, "status": new_status, "substatus": new_sub, **(extra or {})}

    if action == "approve":
        high = drv & FOUR_EYES
        if high and not checklist_done:
            raise ValueError("Tick the verification checklist first: this case involves " + ", ".join(sorted(high)) + ".")
        if high and v["substatus"] == "awaiting_reviewer":
            data["first_approver"] = actor
            data["first_approval_reason"] = reason.strip()
            return log("pending", "awaiting_second_approval", {"note": "First approval recorded. A second, different reviewer must approve."})
        if v["substatus"] == "awaiting_second_approval" and data.get("first_approver", "").lower() == actor.lower():
            raise ValueError(f"Four-eyes rule: {actor} already gave the first approval. A different reviewer must approve.")
        data["approved_account_key"] = v["account_key"]
        data["approved_legal_name"] = v["legal_name"]
        out = log("approved", "approved", {"approvers": [x for x in (data.get("first_approver"), actor) if x]})
        service._insert_email(conn, vendor_id, "vendor", "approved", to, _template_email("vendor", "approved", base))
        return out

    if action == "reject":
        out = log("rejected", "rejected_manual")
        if not drv & COMPLIANCE:        # never tell a sanctions-screened party anything
            service._insert_email(conn, vendor_id, "vendor", "rejected", to, _template_email("vendor", "rejected", base))
        return out

    # request_info: the reviewer decides the vendor CAN fix it; ask in neutral words
    asks = [a.strip() for a in (asks or []) if a and a.strip()]
    if not asks:
        raise ValueError("Write at least one thing to ask the vendor.")
    due = db.now(conn) + timedelta(days=config.VENDOR_RESPONSE_DAYS)
    mail = _template_email("vendor", "request", {**base, "asks": asks, "due_date": due.strftime("%d %b %Y")})
    if not vendor_mail_is_safe(mail["subject"] + mail["body"]):
        raise ValueError("The request mentions internal screening terms (fraud, sanctions, risk...). Rephrase it neutrally.")
    data["reviewer_asks"] = asks
    conn.execute("UPDATE vendors SET due_at=?, reminders_sent=0 WHERE vendor_id=?", (db.iso(due), vendor_id))
    service._insert_email(conn, vendor_id, "vendor", "request", to, mail)
    return log("pending", "awaiting_vendor", {"asks": asks})
