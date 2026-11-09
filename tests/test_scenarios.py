"""End-to-end: every labelled case in samples/cases.json through the real pipeline (mock AI),
plus safety properties that must hold for ANY input."""
import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ["MOCK_LLM"] = "1"

from app import db, evaluation, pipeline, service  # noqa: E402
from app.llm import MockLLM, vendor_mail_is_safe  # noqa: E402

CASES = evaluation.load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_labelled_case(case):
    r = evaluation.run_case(case, MockLLM())
    assert r["passed"], [s["fails"] for s in r["steps"]]


def _run(case_id, mutate=None):
    c = json.loads(json.dumps(next(x for x in CASES if x["id"] == case_id)))
    if mutate:
        mutate(c)
    t = tempfile.TemporaryDirectory()
    conn = db.connect(Path(t.name) / "t.db")
    service.seed_vendor_master(conn)
    vid, sid = service.create_submission(conn, c["form"], evaluation._abs(c["docs"]))
    res = pipeline.process_submission(conn, sid, MockLLM())
    return res, conn, t


def test_corrupting_a_clean_vendor_always_stops_auto_approval():
    for key, val in [("account_number", "50200098765499"), ("legal_name", "Kestrel Analytics Technologies Pvt Ltd"),
                     ("entity_type", "llp"), ("tax_id", "07AAFCK2231L1ZZ"), ("country", "DE")]:
        res, conn, t = _run("H1_happy_india", lambda c, k=key, v=val: c["form"].__setitem__(k, v))
        assert res["status"] != "approved", f"corrupting {key} was auto-approved"
        conn.close(); t.cleanup()


def test_vendor_never_sees_fraud_or_screening_language():
    for c in CASES:
        res, conn, t = _run(c["id"])
        for m in res["emails"]:
            if m["audience"] == "vendor":
                assert vendor_mail_is_safe(m["subject"] + m["body"]), (c["id"], m["body"])
        conn.close(); t.cleanup()


def test_sanctions_rejection_sends_no_vendor_email():
    res, conn, t = _run("R1_sanctions_match")
    assert res["status"] == "rejected"
    assert all(m["audience"] == "reviewer" for m in res["emails"])
    conn.close(); t.cleanup()


def test_reviewer_hold_sends_nothing_to_the_vendor():
    res, conn, t = _run("E1b_bank_change_request")
    assert res["substatus"] == "awaiting_reviewer"
    assert [m["audience"] for m in res["emails"]] == ["reviewer"]
    conn.close(); t.cleanup()


def test_pipeline_crash_becomes_human_review_not_approval():
    class Boom(MockLLM):
        def extract_document(self, *a, **k):
            raise RuntimeError("API down")
    c = next(x for x in CASES if x["id"] == "H1_happy_india")
    with tempfile.TemporaryDirectory() as t:
        conn = db.connect(Path(t) / "t.db")
        service.seed_vendor_master(conn)
        vid, sid = service.create_submission(conn, c["form"], evaluation._abs(c["docs"]))
        res = pipeline.process_submission(conn, sid, Boom())
        assert res["status"] == "pending" and res["substatus"] == "awaiting_reviewer"
        assert any(f["code"] == "STAGE_ERROR" for f in res["findings"])
        conn.close()


def test_override_requires_a_reason_and_is_audited():
    res, conn, t = _run("E1a_lookalike_new_bank")
    vid = res["vendor_id"]
    with pytest.raises(ValueError):
        service.reviewer_override(conn, vid, "approved", "ok")
    service.reviewer_override(conn, vid, "approved", "Verified by phone call to the number on file for V-0001.")
    assert conn.execute("SELECT status FROM vendors WHERE vendor_id=?", (vid,)).fetchone()["status"] == "approved"
    assert conn.execute("SELECT COUNT(*) n FROM audit WHERE event='reviewer_override'").fetchone()["n"] == 1
    assert db.verify_audit(conn)["ok"]
    conn.close(); t.cleanup()


def test_resubmission_keeps_one_vendor_and_all_versions():
    c = next(x for x in CASES if x["id"] == "E3a_missing_bank_letter_then_resubmit")
    with tempfile.TemporaryDirectory() as t:
        conn = db.connect(Path(t) / "t.db")
        service.seed_vendor_master(conn)
        v1, s1 = service.create_submission(conn, c["form"], evaluation._abs(c["docs"]))
        pipeline.process_submission(conn, s1, MockLLM())
        v2, s2 = service.create_submission(conn, c["form"], evaluation._abs(c["follow_up"]["add_docs"]))   # no vendor_id given
        assert v1 == v2, "same tax ID + same name must map to the same vendor record"
        res = pipeline.process_submission(conn, s2, MockLLM())
        assert res["status"] == "approved"
        assert conn.execute("SELECT COUNT(*) n FROM submissions WHERE vendor_id=?", (v1,)).fetchone()["n"] == 2
        conn.close()


def test_guardrail_replaces_a_drifting_ai_email_with_the_safe_template():
    """If the model writes fraud/sanctions language into a VENDOR email, code must swap it out."""
    class Drifty(MockLLM):
        def draft_email(self, audience, kind, ctx):
            return {"subject": "Problem", "body": "We flagged you as suspicious fraud; your risk score is high."}
    c = next(x for x in CASES if x["id"] == "V3_account_in_other_name")
    with tempfile.TemporaryDirectory() as t:
        conn = db.connect(Path(t) / "t.db")
        service.seed_vendor_master(conn)
        vid, sid = service.create_submission(conn, c["form"], evaluation._abs(c["docs"]))
        res = pipeline.process_submission(conn, sid, Drifty())
        mail = next(m for m in res["emails"] if m["audience"] == "vendor")
        assert vendor_mail_is_safe(mail["subject"] + mail["body"])
        assert "fallback_reason" in mail                       # and we recorded that we had to intervene
        stored = conn.execute("SELECT body FROM emails WHERE vendor_id=?", (vid,)).fetchone()["body"]
        assert "suspicious" not in stored.lower()
        conn.close()
