"""Reviewer workflow: next actions, queue, four-eyes approval, request-info guardrails."""
import tempfile
from pathlib import Path

import pytest

from app import db, evaluation, pipeline, service, workflow
from app.llm import MockLLM

CASES = {c["id"]: c for c in evaluation.load_cases()}


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as t:
        c = db.connect(Path(t) / "t.db")
        service.seed_vendor_master(c)
        yield c
        c.close()


def run(conn, cid):
    c = CASES[cid]
    vid, sid = service.create_submission(conn, c["form"], evaluation._abs(c["docs"]))
    return vid, pipeline.process_submission(conn, sid, MockLLM())


def status(conn, vid):
    return conn.execute("SELECT status, substatus FROM vendors WHERE vendor_id=?", (vid,)).fetchone()["substatus"]


def test_every_decision_has_next_actions(conn):
    for cid in ("H1_happy_india", "E1b_bank_change_request", "V6_stale_bank_letter", "R1_sanctions_match"):
        _, res = run(conn, cid)
        assert res["next_actions"], cid
    _, res = run(conn, "E1b_bank_change_request")
    assert any("ALREADY ON FILE" in a["action"] for a in res["next_actions"])


def test_bank_change_needs_checklist_and_two_reviewers(conn):
    vid, res = run(conn, "E1b_bank_change_request")
    assert res["substatus"] == "awaiting_reviewer"
    q = workflow.review_queue(conn)
    item = next(i for i in q if i["vendor_id"] == vid)
    assert item["four_eyes"] and item["checklist"]
    with pytest.raises(ValueError, match="checklist"):
        workflow.review_action(conn, vid, "approve", "asha", "Called the number on file, confirmed.")
    r = workflow.review_action(conn, vid, "approve", "asha", "Called the number on file, confirmed.", checklist_done=True)
    assert r["substatus"] == "awaiting_second_approval"
    with pytest.raises(ValueError, match="Four-eyes"):
        workflow.review_action(conn, vid, "approve", "Asha", "Approving again myself", checklist_done=True)
    r = workflow.review_action(conn, vid, "approve", "ben", "Second check of call notes done.", checklist_done=True)
    assert r["substatus"] == "approved" and r["approvers"] == ["asha", "ben"]
    assert status(conn, vid) == "approved"


def test_request_info_reroutes_to_vendor_and_blocks_internal_terms(conn):
    vid, _ = run(conn, "E4b_country_contradiction")
    with pytest.raises(ValueError, match="screening terms"):
        workflow.review_action(conn, vid, "request_info", "asha", "Need the contracting entity",
                               asks=["Explain why our fraud check flagged your tax ID"])
    r = workflow.review_action(conn, vid, "request_info", "asha", "Need the contracting entity",
                               asks=["Please confirm which legal entity will sign the contract and invoice us."])
    assert r["substatus"] == "awaiting_vendor"
    tok = conn.execute("SELECT token FROM vendors WHERE vendor_id=?", (vid,)).fetchone()["token"]
    assert service.portal_view(conn, tok)["asks"] == ["Please confirm which legal entity will sign the contract and invoice us."]


def test_reason_is_mandatory_and_only_reviewer_items_can_be_actioned(conn):
    vid, _ = run(conn, "E1a_lookalike_new_bank")
    with pytest.raises(ValueError, match="reason"):
        workflow.review_action(conn, vid, "reject", "asha", "no")
    hid, _ = run(conn, "H2_happy_germany")
    with pytest.raises(ValueError, match="not waiting"):
        workflow.review_action(conn, hid, "reject", "asha", "Trying to reject an approved vendor")
