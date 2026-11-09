"""The waiting loop, the vendor-facing page, reset, and email safety."""
import json
import tempfile
from pathlib import Path

import httpx
import pytest

from app import config, db, emailer, evaluation, pipeline, service
from app.llm import MockLLM

CASES = {c["id"]: c for c in evaluation.load_cases()}


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as t:
        c = db.connect(Path(t) / "t.db")
        service.seed_vendor_master(c)
        yield c
        c.close()


def run(conn, cid, follow=False, vendor_id=None):
    c = CASES[cid]
    if follow:
        fu = c["follow_up"]
        vid, sid = service.create_submission(conn, c["form"], evaluation._abs(fu["add_docs"]), vendor_id=vendor_id)
    else:
        vid, sid = service.create_submission(conn, c["form"], evaluation._abs(c["docs"]))
    return vid, pipeline.process_submission(conn, sid, MockLLM())


def vendor(conn, vid):
    return dict(conn.execute("SELECT * FROM vendors WHERE vendor_id=?", (vid,)).fetchone())


def test_nothing_happens_before_the_deadline(conn):
    vid, res = run(conn, "E3a_missing_bank_letter_then_resubmit")
    assert res["substatus"] == "awaiting_vendor"
    db.advance_clock(conn, 3)
    assert service.tick(conn) == []


def test_reminders_then_closure_with_audit(conn):
    vid, _ = run(conn, "E3a_missing_bank_letter_then_resubmit")
    db.advance_clock(conn, 8)
    a = service.tick(conn)
    assert [x["action"] for x in a] == ["reminder"] and vendor(conn, vid)["reminders_sent"] == 1
    db.advance_clock(conn, 4)
    assert service.tick(conn)[0]["number"] == 2
    db.advance_clock(conn, 4)
    assert service.tick(conn)[0]["action"] == "closed"
    v = vendor(conn, vid)
    assert (v["status"], v["substatus"]) == ("rejected", "rejected_closed")
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM emails WHERE vendor_id=? ORDER BY id", (vid,))]
    assert kinds == ["request", "reminder", "reminder", "closed"]
    assert conn.execute("SELECT actor FROM overrides WHERE vendor_id=?", (vid,)).fetchone()["actor"] == "system"
    assert db.verify_audit(conn)["ok"]
    assert service.tick(conn) == []                                  # closed means closed: no further emails


def test_vendor_who_answers_resets_the_cycle_and_is_approved(conn):
    vid, _ = run(conn, "E3a_missing_bank_letter_then_resubmit")
    db.advance_clock(conn, 8)
    service.tick(conn)
    assert vendor(conn, vid)["reminders_sent"] == 1
    _, res = run(conn, "E3a_missing_bank_letter_then_resubmit", follow=True, vendor_id=vid)
    assert res["status"] == "approved" and "MISSING_DOC:bank_proof" in res["resolved_since_last_run"]
    assert vendor(conn, vid)["reminders_sent"] == 0
    db.advance_clock(conn, 30)
    assert service.tick(conn) == []                                  # approved vendors are never chased


def test_vendor_page_never_leaks_internal_signals(conn):
    # held for a reviewer: the vendor sees a neutral message and no asks
    vid, _ = run(conn, "E1a_lookalike_new_bank")
    v = service.portal_view(conn, vendor(conn, vid)["token"])
    assert v["state"] == "review" and v["asks"] == []
    blob = json.dumps(v).lower()
    for bad in ("lookalike", "fraud", "risk", "sanction", "reviewer", "v-0001"):
        assert bad not in blob
    # compliance rejection looks like a plain "closed"
    vid, _ = run(conn, "R1_sanctions_match")
    v = service.portal_view(conn, vendor(conn, vid)["token"])
    assert v["state"] == "closed" and "sanction" not in json.dumps(v).lower()
    # waiting on the vendor: sees exactly the asks
    vid, _ = run(conn, "E3a_missing_bank_letter_then_resubmit")
    v = service.portal_view(conn, vendor(conn, vid)["token"])
    assert v["state"] == "open" and any("bank letter" in a for a in v["asks"]) and v["days_left"] is not None
    assert service.portal_view(conn, "nope") is None


def test_reset_gives_a_clean_repeatable_demo(conn):
    run(conn, "H1_happy_india")
    db.advance_clock(conn, 5)
    n = service.reset_demo(conn)
    assert n == 5 and db.clock_offset_days(conn) == 0
    assert conn.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"] == 0
    assert db.verify_audit(conn)["ok"]


def test_email_goes_only_to_the_demo_inbox_never_the_vendor(conn, monkeypatch):
    vid, _ = run(conn, "E3a_missing_bank_letter_then_resubmit")
    eid = conn.execute("SELECT id FROM emails WHERE vendor_id=?", (vid,)).fetchone()["id"]
    assert not emailer.send_to_test_inbox(conn, eid)["ok"]                     # not configured -> clear error, nothing sent
    monkeypatch.setattr(config, "RESEND_API_KEY", "k")
    monkeypatch.setattr(config, "DEMO_INBOX", "me@example.com")
    sent = {}
    def handler(req):
        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"id": "x"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert emailer.send_to_test_inbox(conn, eid, client)["ok"]
    assert sent["to"] == ["me@example.com"] and sent["subject"].startswith("[DEMO]")
    assert "accounts@orchidpackaging" in sent["text"]                           # vendor address appears only as text
    assert conn.execute("SELECT status FROM emails WHERE id=?", (eid,)).fetchone()["status"] == "sent_to_test_inbox"
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key")))
    assert "refused" in emailer.send_to_test_inbox(conn, eid, bad)["error"]
