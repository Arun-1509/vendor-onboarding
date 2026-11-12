"""The example history must (a) give every kind of outcome and (b) never change a demo-pack result."""
import os
import shutil
import tempfile
from pathlib import Path

os.environ["MOCK_LLM"] = "1"

from app import db, evaluation, examples, pipeline, service  # noqa: E402
from app.llm import MockLLM  # noqa: E402


def test_examples_cover_all_outcomes_and_leave_the_demo_pack_unchanged():
    with tempfile.TemporaryDirectory() as t:
        base = Path(t) / "base.db"
        conn = db.connect(base)
        service.seed_vendor_master(conn)
        assert examples.seed(conn) == len(examples.SCENARIOS)
        assert examples.seed(conn) == 0                                   # only once
        subs = {r["substatus"] for r in conn.execute("SELECT substatus FROM runs")}
        assert {"approved", "awaiting_vendor", "awaiting_reviewer", "rejected_compliance"} <= subs
        assert db.verify_audit(conn)["ok"]
        conn.close()
        for i, c in enumerate(evaluation.load_cases()):
            p = Path(t) / f"c{i}.db"
            shutil.copy(base, p)
            conn = db.connect(p)
            _, sid = service.create_submission(conn, c["form"], evaluation._abs(c["docs"]))
            res = pipeline.process_submission(conn, sid, MockLLM())
            exp = c["expect"]
            assert res["status"] == exp["status"], c["id"]
            assert res["substatus"] == exp.get("substatus", res["substatus"]), c["id"]
            codes = {f["code"] for f in res["findings"]}
            assert set(exp.get("must_have", [])) <= codes and not (set(exp.get("must_not_have", [])) & codes), (c["id"], codes)
            conn.close()
