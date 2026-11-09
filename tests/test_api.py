import json
import os
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ.update(MOCK_LLM="1", VAR_DIR=_tmp, DB_PATH=str(Path(_tmp) / "api.db"), UPLOAD_DIR=str(Path(_tmp) / "up"))

from fastapi.testclient import TestClient  # noqa: E402

from app import config, evaluation  # noqa: E402
from app.main import app  # noqa: E402

CASES = {c["id"]: c for c in evaluation.load_cases()}


def _wait_done(client, sid, timeout=10):
    events = []
    with client.stream("GET", f"/api/submissions/{sid}/events") as r:
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and ev:
                events.append((ev, json.loads(line.split(":", 1)[1])))
                if ev in ("end", "error"):
                    break
    return events


def test_upload_run_stream_and_dashboard():
    with TestClient(app) as client:
        client.post("/api/admin/reset")
        assert client.get("/api/health").json()["llm_mode"] == "mock"
        assert len(client.get("/api/vendors").json()) == 5            # seeded vendor master
        c = CASES["E1a_lookalike_new_bank"]
        files = {k: (Path(v).name, open(config.ROOT / v, "rb"), "application/pdf") for k, v in c["docs"].items()}
        r = client.post("/api/submissions", data={"form": json.dumps(c["form"])}, files=files)
        assert r.status_code == 200, r.text
        sid, vid = r.json()["submission_id"], r.json()["vendor_id"]

        events = _wait_done(client, sid)
        kinds = [e for e, _ in events]
        assert kinds[0] == "run_started" and kinds[-1] == "end"
        stages = [d["stage"] for e, d in events if e == "stage_end"]
        assert stages == ["intake", "read_documents", "completeness", "validate", "cross_check",
                          "name_judgment", "risk_screening", "decision", "communicate"]
        fin = next(d for e, d in events if e == "run_finished")
        assert (fin["status"], fin["substatus"]) == ("pending", "awaiting_reviewer")

        detail = client.get(f"/api/vendors/{vid}").json()
        assert detail["runs"] and detail["audit"] and "token" not in detail["vendor"]
        run = client.get(f"/api/runs/{detail['runs'][0]['run_id']}").json()
        assert len(run["steps"]) == 9

        o = client.post(f"/api/vendors/{vid}/override", json={"status": "rejected", "reason": "Phone check failed: vendor denied the request."})
        assert o.status_code == 200
        assert client.post(f"/api/vendors/{vid}/override", json={"status": "approved", "reason": "ok"}).status_code == 422
        assert client.get("/api/audit/verify").json()["ok"] is True
        s = client.get("/api/stats").json()
        assert s["vendors_total"] >= 6 and s["processed"] >= 1 and "awaiting_reviewer" in s and s["llm_mode"] == "mock"


def test_upload_guards():
    with TestClient(app) as client:
        form = {"form": json.dumps({"legal_name": "X"})}
        bad = client.post("/api/submissions", data=form, files={"tax_cert": ("evil.exe", b"MZ", "application/octet-stream")})
        assert bad.status_code == 400
        big = client.post("/api/submissions", data=form, files={"tax_cert": ("big.pdf", b"0" * (7 * 1024 * 1024), "application/pdf")})
        assert big.status_code == 413
        assert client.post("/api/submissions", data={"form": "not json"}).status_code == 400


def test_sample_endpoint_runs_end_to_end():
    with TestClient(app) as client:
        r = client.post("/api/samples/H1_happy_india/submit").json()
        events = _wait_done(client, r["submission_id"])
        fin = next(d for e, d in events if e == "run_finished")
        assert fin["status"] == "approved"
