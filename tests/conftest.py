"""Every test session gets its own throw-away database/uploads and offline mock AI."""
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="vo-tests-")
os.environ["MOCK_LLM"] = "1"
os.environ["STAGE_PACE_MS"] = "0"
os.environ["VAR_DIR"] = _tmp
os.environ["DB_PATH"] = str(Path(_tmp) / "vendors.db")
os.environ["UPLOAD_DIR"] = str(Path(_tmp) / "uploads")
for k in ("RESEND_API_KEY", "DEMO_INBOX", "ADMIN_KEY"):
    os.environ.pop(k, None)
