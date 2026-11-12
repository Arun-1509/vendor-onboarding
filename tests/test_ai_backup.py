"""When the live model cannot answer (quota, outage) the run must stay SAFE and stay USEFUL:
text PDFs are read by the built-in reader (and say so); anything it cannot fully read is held for a person."""
import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ["MOCK_LLM"] = "1"

from app import db, evaluation, pipeline, service  # noqa: E402
from app.llm import LLMError, MockLLM, ResilientLLM  # noqa: E402

CASES = evaluation.load_cases()


class Down(MockLLM):
    """A live model that is out of quota."""
    mode = "live"
    calls = 0

    def extract_document(self, *a, **k):
        Down.calls += 1
        raise LLMError("gemini-flash returned 429 (rate limit)")

    judge_names = draft_email = extract_document


@pytest.fixture(autouse=True)
def _reset():
    ResilientLLM._down_until = 0.0
    Down.calls = 0
    yield
    ResilientLLM._down_until = 0.0


def _run(case_id, llm):
    c = next(x for x in CASES if x["id"] == case_id)
    t = tempfile.TemporaryDirectory()
    conn = db.connect(Path(t.name) / "t.db")
    service.seed_vendor_master(conn)
    vid, sid = service.create_submission(conn, c["form"], evaluation._abs(c["docs"]))
    res = pipeline.process_submission(conn, sid, llm)
    conn.close()
    return res, t


def test_every_demo_case_gets_the_same_decision_or_a_human_when_the_ai_is_down():
    for c in CASES:
        ResilientLLM._down_until = 0.0
        want, t1 = _run(c["id"], MockLLM())
        got, t2 = _run(c["id"], ResilientLLM(Down()))
        held = got["substatus"] == "awaiting_reviewer" and any(f["code"] == "STAGE_ERROR" for f in got["findings"])
        assert (got["status"], got["substatus"]) == (want["status"], want["substatus"]) or held, c["id"]
        assert not (got["status"] == "approved" and want["status"] != "approved"), c["id"]
        t1.cleanup(); t2.cleanup()


def test_backup_is_declared_on_the_run_and_skips_the_dead_model_afterwards():
    res, t = _run("H1_happy_india", ResilientLLM(Down()))
    assert res["status"] == "approved" and res["ai_backup"]
    assert any(m.get("fallback_reason") for m in res["emails"])          # email came from the template
    first = Down.calls
    _run("H1_happy_india", ResilientLLM(Down()))[1].cleanup()
    assert Down.calls == first, "a model known to be out of quota is not asked again for a while"
    t.cleanup()


def test_a_scan_is_never_read_by_the_backup(tmp_path):
    img = tmp_path / "bank_proof.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0 not really a photo")
    Path(str(img) + ".mock.json").write_text(json.dumps({"text": "Account holder: X\nIBAN: DE89370400440532013000", "legible": True}))
    with pytest.raises(LLMError):
        ResilientLLM(Down()).extract_document("bank_proof", str(img))


def test_a_pdf_the_backup_cannot_fully_read_is_not_half_read(tmp_path):
    from reportlab.pdfgen import canvas
    p = tmp_path / "bank_proof.pdf"
    c = canvas.Canvas(str(p)); c.drawString(72, 720, "Bank confirmation letter"); c.drawString(72, 700, "We confirm the account of Alpha GmbH with us."); c.save()
    with pytest.raises(LLMError):
        ResilientLLM(Down()).extract_document("bank_proof", str(p))
