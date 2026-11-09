"""Operations around the pipeline: create/merge submissions, reviewer override, seeding."""
from __future__ import annotations

import json
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, db
from .validators import bank as bk
from .validators import names as nm
from .validators import taxid as tx

FORM_KEYS = ["legal_name", "trade_name", "entity_type", "country", "address", "city", "postal_code", "tax_id",
             "contact_name", "contact_email", "contact_phone", "bank_name", "account_holder", "account_number",
             "iban", "ifsc", "swift", "routing_number", "sort_code", "currency"]


def normalize_form(raw: dict) -> dict:
    f = {k: (str(raw.get(k, "") or "").strip()) for k in FORM_KEYS}
    f["country"] = f["country"].upper()
    f["entity_type"] = f["entity_type"].lower().replace(" ", "_")
    f["currency"] = f["currency"].upper()
    for k in ("ifsc", "iban", "swift"):
        f[k] = f[k].replace(" ", "").upper()
    return f


def _match_vendor(conn, form: dict) -> dict | None:
    """Same company coming back? Match on tax ID + equivalent name. A vendor that was
    rejected for compliance is never silently re-opened (it is screened as PRIOR_REJECTED)."""
    if not form["tax_id"]:
        return None
    t = tx.validate_tax_id(form["country"], form["tax_id"]).normalized or form["tax_id"]
    for v in conn.execute("SELECT * FROM vendors WHERE tax_id=? AND country=?", (t, form["country"])):
        if v["status"] == "rejected" and v["substatus"] == "rejected_compliance":
            continue
        if nm.compare_names(form["legal_name"], v["legal_name"])["verdict"] in ("exact", "equivalent", "form_mismatch", "grey") \
                or nm.core_similarity(form["legal_name"], v["legal_name"]) >= config.NAME_GREY_LOW:
            return db.row(v)
    return None


def create_submission(conn, raw_form: dict, doc_paths: dict, source: str = "form", vendor_id: str | None = None) -> tuple[str, str]:
    """-> (vendor_id, submission_id). Reuses the vendor on resubmission and keeps every version."""
    form = normalize_form(raw_form)
    now = db.iso(db.now(conn))
    vendor = None
    if vendor_id:
        vendor = db.row(conn.execute("SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)).fetchone())
    if vendor is None:
        vendor = _match_vendor(conn, form)
    if vendor is None:
        vid = db.next_id(conn, "vendors", "vendor_id", "V-", 4)
        conn.execute("INSERT INTO vendors(vendor_id,legal_name,country,tax_id,account_key,status,substatus,risk_score,token,created_at,updated_at,version,data)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (vid, form["legal_name"], form["country"], "", "", "new", "new", 0, secrets.token_urlsafe(16), now, now, 0, "{}"))
        db.audit(conn, "vendor_created", {"legal_name": form["legal_name"], "country": form["country"], "source": source}, vid)
        version = 1
    else:
        vid = vendor["vendor_id"]
        version = (conn.execute("SELECT COALESCE(MAX(version),0) m FROM submissions WHERE vendor_id=?", (vid,)).fetchone()["m"]) + 1
        conn.execute("UPDATE vendors SET reminders_sent=0 WHERE vendor_id=?", (vid,))
    prev_docs = {}
    if version > 1:        # keep earlier documents unless the vendor replaced them
        r = conn.execute("SELECT docs FROM submissions WHERE vendor_id=? ORDER BY version DESC LIMIT 1", (vid,)).fetchone()
        prev_docs = json.loads(r["docs"]) if r else {}
    docs = {**prev_docs, **{k: v for k, v in doc_paths.items() if v}}
    sid = db.next_id(conn, "submissions", "submission_id", "S-", 5)
    conn.execute("INSERT INTO submissions(submission_id,vendor_id,version,form,docs,created_at,source) VALUES(?,?,?,?,?,?,?)",
                 (sid, vid, version, json.dumps(form), json.dumps(docs), now, source))
    db.audit(conn, "submission_received", {"submission": sid, "version": version, "documents": sorted(docs), "source": source}, vid)
    return vid, sid


def store_upload(vendor_hint: str, filename: str, data: bytes) -> str:
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")[:80] or "file"
    d = config.UPLOAD_DIR / vendor_hint
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{secrets.token_hex(4)}_{safe}"
    p.write_bytes(data)
    return str(p)


def reviewer_override(conn, vendor_id: str, new_status: str, reason: str, actor: str = "reviewer") -> dict:
    if new_status not in ("approved", "rejected", "pending"):
        raise ValueError("new_status must be approved, rejected or pending")
    if not reason or len(reason.strip()) < 8:
        raise ValueError("A reason of at least 8 characters is required for every override.")
    v = db.row(conn.execute("SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)).fetchone())
    if not v:
        raise KeyError(vendor_id)
    data = v["data"] if isinstance(v["data"], dict) else {}
    sub = {"approved": "approved", "rejected": "rejected_manual", "pending": "awaiting_reviewer"}[new_status]
    if new_status == "approved":      # the reviewer accepts the submitted bank details
        data["approved_account_key"] = v["account_key"]
        data["approved_legal_name"] = v["legal_name"]
    conn.execute("UPDATE vendors SET status=?,substatus=?,updated_at=?,data=? WHERE vendor_id=?",
                 (new_status, sub, db.iso(db.now(conn)), json.dumps(data), vendor_id))
    conn.execute("INSERT INTO overrides(vendor_id,old_status,new_status,reason,actor,at) VALUES(?,?,?,?,?,?)",
                 (vendor_id, v["status"], new_status, reason.strip(), actor, db.iso(db.now(conn))))
    db.audit(conn, "reviewer_override", {"from": v["status"], "to": new_status, "reason": reason.strip(), "actor": actor}, vendor_id)
    return {"vendor_id": vendor_id, "status": new_status, "substatus": sub}


def seed_vendor_master(conn, path: Path | None = None) -> int:
    """Insert the already-approved vendors screening compares against."""
    entries = json.loads((path or config.DATA_DIR / "seed_vendors.json").read_text())["vendors"]
    n = 0
    for e in entries:
        if conn.execute("SELECT 1 FROM vendors WHERE vendor_id=?", (e["vendor_id"],)).fetchone():
            continue
        form = normalize_form(e["form"])
        key = bk.normalize_account_key(form["country"], form)
        t = tx.validate_tax_id(form["country"], form["tax_id"]).normalized
        now = db.iso(db.now(conn))
        data = {"approved_account_key": key if e["status"] == "approved" else "", "approved_legal_name": form["legal_name"]}
        conn.execute("INSERT INTO vendors(vendor_id,legal_name,country,tax_id,account_key,status,substatus,risk_score,token,created_at,updated_at,version,data)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (e["vendor_id"], form["legal_name"], form["country"], t, key, e["status"], e.get("substatus", e["status"]),
                      0, secrets.token_urlsafe(16), now, now, 1, json.dumps(data)))
        sid = db.next_id(conn, "submissions", "submission_id", "S-", 5)
        conn.execute("INSERT INTO submissions(submission_id,vendor_id,version,form,docs,created_at,source) VALUES(?,?,?,?,?,?,?)",
                     (sid, e["vendor_id"], 1, json.dumps(form), "{}", now, "seed"))
        db.audit(conn, "vendor_seeded", {"legal_name": form["legal_name"], "status": e["status"]}, e["vendor_id"])
        n += 1
    return n


# ===================================================== vendor lifecycle (waiting) ====
def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _latest_form(conn, vendor_id: str) -> dict:
    r = conn.execute("SELECT form FROM submissions WHERE vendor_id=? ORDER BY version DESC LIMIT 1", (vendor_id,)).fetchone()
    return json.loads(r["form"]) if r else {}


def vendor_asks(conn, vendor_id: str) -> list[str]:
    """What the vendor still has to do, taken from the latest run's findings."""
    r = conn.execute("SELECT result FROM runs WHERE vendor_id=? AND status!='running' ORDER BY run_id DESC LIMIT 1", (vendor_id,)).fetchone()
    if not r or not r["result"]:
        return []
    fs = json.loads(r["result"]).get("findings", [])
    return list(dict.fromkeys(f["vendor_ask"] for f in fs if f.get("action") == "vendor" and f.get("vendor_ask")))


def _insert_email(conn, vid, audience, kind, to, mail, status="drafted"):
    conn.execute("INSERT INTO emails(vendor_id,run_id,audience,kind,to_addr,subject,body,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (vid, None, audience, kind, to, mail["subject"], mail["body"], status, db.iso(db.now(conn))))


def tick(conn) -> list[dict]:
    """The waiting loop. Call whenever time passes (a scheduler in production; a button in the demo).
       overdue request -> reminder (x MAX_REMINDERS) -> close as 'no response'."""
    from .llm import _template_email
    now, actions = db.now(conn), []
    for v in conn.execute("SELECT * FROM vendors WHERE substatus='awaiting_vendor' AND due_at IS NOT NULL").fetchall():
        if now < _parse(v["due_at"]):
            continue
        vid, form = v["vendor_id"], _latest_form(conn, v["vendor_id"])
        to = form.get("contact_email", "")
        ctx = {"vendor_name": v["legal_name"], "reference": vid, "asks": vendor_asks(conn, vid),
               "portal_url": f"{config.BASE_URL.rstrip('/')}/portal/{v['token']}"}
        if v["reminders_sent"] < config.MAX_REMINDERS:
            n = v["reminders_sent"] + 1
            new_due = now + timedelta(days=config.REMINDER_GAP_DAYS)
            ctx.update(reminder_no=n, due_date=new_due.strftime("%d %b %Y"))
            _insert_email(conn, vid, "vendor", "reminder", to, _template_email("vendor", "reminder", ctx))
            conn.execute("UPDATE vendors SET reminders_sent=?, due_at=?, updated_at=? WHERE vendor_id=?",
                         (n, db.iso(new_due), db.iso(now), vid))
            db.audit(conn, "reminder_sent", {"number": n, "new_deadline": db.iso(new_due)}, vid)
            actions.append({"vendor_id": vid, "name": v["legal_name"], "action": "reminder", "number": n})
        else:
            reason = f"No response within the deadline after {config.MAX_REMINDERS} reminders (rule STALE_NO_RESPONSE)."
            _insert_email(conn, vid, "vendor", "closed", to, _template_email("vendor", "closed", ctx))
            conn.execute("UPDATE vendors SET status='rejected', substatus='rejected_closed', due_at=NULL, updated_at=? WHERE vendor_id=?",
                         (db.iso(now), vid))
            conn.execute("INSERT INTO overrides(vendor_id,old_status,new_status,reason,actor,at) VALUES(?,?,?,?,?,?)",
                         (vid, v["status"], "rejected", reason, "system", db.iso(now)))
            db.audit(conn, "request_closed_no_response", {"reason": reason}, vid)
            actions.append({"vendor_id": vid, "name": v["legal_name"], "action": "closed"})
    return actions


PORTAL_TEXT = {
    "open": "We need a few things from you to finish onboarding.",
    "review": "Thank you. Your registration is being reviewed by our team. We will contact you if we need anything else.",
    "approved": "You are approved as a vendor. Nothing more is needed.",
    "closed": "This request is closed. Please contact the procurement team if you believe this is a mistake.",
    "checking": "Thanks. We are checking what you sent.",
}


def portal_view(conn, token: str, running: bool = False) -> dict | None:
    """What a VENDOR may see. Never exposes internal findings, fraud signals or risk."""
    v = conn.execute("SELECT * FROM vendors WHERE token=?", (token,)).fetchone()
    if not v:
        return None
    sub = v["substatus"]
    state = ("checking" if running else "open" if sub == "awaiting_vendor" else "approved" if v["status"] == "approved"
             else "review" if sub == "awaiting_reviewer" else "closed")
    form = _latest_form(conn, v["vendor_id"])
    docs = conn.execute("SELECT docs FROM submissions WHERE vendor_id=? ORDER BY version DESC LIMIT 1", (v["vendor_id"],)).fetchone()
    days_left = None
    if state == "open" and v["due_at"]:
        days_left = max(0, (_parse(v["due_at"]) - db.now(conn)).days)
    return {"reference": v["vendor_id"], "company": v["legal_name"], "state": state, "message": PORTAL_TEXT[state],
            "asks": vendor_asks(conn, v["vendor_id"]) if state == "open" else [], "due_at": v["due_at"] if state == "open" else None,
            "days_left": days_left, "form": form, "documents_on_file": sorted(json.loads(docs["docs"]).keys()) if docs else []}


def reset_demo(conn) -> int:
    """Wipe everything and re-seed. For rehearsals: gives a clean, repeatable demo."""
    for t in ("vendors", "submissions", "runs", "run_steps", "emails", "overrides", "audit", "settings"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")
    return seed_vendor_master(conn)
