"""Optional real email delivery. SAFETY RULE: the public demo NEVER emails a vendor's address.
Every message goes to DEMO_INBOX (your own address) with a banner saying who it would have gone to."""
from __future__ import annotations

import httpx

from . import config, db


def send_to_test_inbox(conn, email_id: int, client: httpx.Client | None = None) -> dict:
    m = conn.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
    if not m:
        return {"ok": False, "error": "email not found"}
    if not config.RESEND_API_KEY or not config.DEMO_INBOX:
        return {"ok": False, "error": "Email delivery is not configured. Set RESEND_API_KEY and DEMO_INBOX to send demo emails to your own inbox."}
    body = f"[DEMO] This would have been sent to: {m['to_addr']}\n[DEMO] Audience: {m['audience']} ({m['kind']})\n\n{m['body']}"
    payload = {"from": config.EMAIL_FROM, "to": [config.DEMO_INBOX], "subject": f"[DEMO] {m['subject']}", "text": body}
    c = client or httpx.Client(timeout=15)
    try:
        r = c.post("https://api.resend.com/emails", json=payload, headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"})
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Could not reach the email provider: {e}"}
    if r.status_code >= 300:
        return {"ok": False, "error": f"Email provider refused it ({r.status_code}): {r.text[:160]}"}
    conn.execute("UPDATE emails SET status='sent_to_test_inbox' WHERE id=?", (email_id,))
    db.audit(conn, "email_sent_to_test_inbox", {"email_id": email_id, "kind": m["kind"], "would_have_gone_to": m["to_addr"]}, m["vendor_id"])
    return {"ok": True, "sent_to": config.DEMO_INBOX}
