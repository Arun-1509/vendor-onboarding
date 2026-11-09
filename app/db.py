"""SQLite storage + the simulated clock + the hash-chained audit log.

Why a hash chain: the guide says the only audit trail today is 'whatever's in
someone's inbox'. Every event here stores sha256(prev_hash + event), so editing or
deleting any past row breaks every hash after it and `verify_audit()` shows where."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors(
  vendor_id TEXT PRIMARY KEY, legal_name TEXT, country TEXT, tax_id TEXT, account_key TEXT,
  status TEXT, substatus TEXT, risk_score INTEGER DEFAULT 0, token TEXT UNIQUE,
  created_at TEXT, updated_at TEXT, due_at TEXT, reminders_sent INTEGER DEFAULT 0,
  latest_run_id TEXT, version INTEGER DEFAULT 0, data TEXT);
CREATE TABLE IF NOT EXISTS submissions(
  submission_id TEXT PRIMARY KEY, vendor_id TEXT, version INTEGER, form TEXT, docs TEXT,
  created_at TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY, vendor_id TEXT, submission_id TEXT, status TEXT, substatus TEXT,
  risk_score INTEGER, llm_mode TEXT, started_at TEXT, finished_at TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS run_steps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, seq INTEGER, stage TEXT, status TEXT,
  headline TEXT, details TEXT, ms INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS emails(
  id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT, run_id TEXT, audience TEXT, kind TEXT,
  to_addr TEXT, subject TEXT, body TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS overrides(
  id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT, old_status TEXT, new_status TEXT,
  reason TEXT, actor TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS audit(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, vendor_id TEXT, run_id TEXT, event TEXT,
  payload TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

_lock = threading.RLock()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = str(path or config.DB_PATH)
    conn = sqlite3.connect(p, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- clock ----
def clock_offset_days(conn) -> int:
    r = conn.execute("SELECT value FROM settings WHERE key='clock_offset_days'").fetchone()
    return int(r["value"]) if r else 0


def now(conn) -> datetime:
    """Real time + a demo offset, so 'a week later' can be shown in a live demo."""
    return datetime.now(timezone.utc) + timedelta(days=clock_offset_days(conn))


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def advance_clock(conn, days: int) -> int:
    new = clock_offset_days(conn) + days
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('clock_offset_days',?)", (str(new),))
    return new


# ---------------------------------------------------------------- audit ----
def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def audit(conn, event: str, payload: dict, vendor_id: str | None = None, run_id: str | None = None) -> int:
    with _lock:
        last = conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last["hash"] if last else "GENESIS"
        at = iso(now(conn))
        body = _canon({"at": at, "vendor": vendor_id, "run": run_id, "event": event, "payload": payload})
        h = hashlib.sha256((prev + body).encode()).hexdigest()
        cur = conn.execute(
            "INSERT INTO audit(at,vendor_id,run_id,event,payload,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
            (at, vendor_id, run_id, event, _canon(payload), prev, h))
        return cur.lastrowid


def verify_audit(conn) -> dict:
    prev = "GENESIS"
    n = 0
    for r in conn.execute("SELECT * FROM audit ORDER BY seq"):
        body = _canon({"at": r["at"], "vendor": r["vendor_id"], "run": r["run_id"],
                       "event": r["event"], "payload": json.loads(r["payload"])})
        expect = hashlib.sha256((prev + body).encode()).hexdigest()
        if r["prev_hash"] != prev or r["hash"] != expect:
            return {"ok": False, "checked": n, "broken_at_seq": r["seq"]}
        prev = r["hash"]
        n += 1
    return {"ok": True, "checked": n, "head": prev[:16]}


# --------------------------------------------------------------- helpers ----
def next_id(conn, table: str, col: str, prefix: str, width: int = 4) -> str:
    r = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()
    n = r["c"] + 1
    while conn.execute(f"SELECT 1 FROM {table} WHERE {col}=?", (f"{prefix}{n:0{width}d}",)).fetchone():
        n += 1
    return f"{prefix}{n:0{width}d}"


def row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("data", "form", "docs", "result", "details", "payload"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d
