"""Example history. The free host forgets its data on every restart, so a visitor could land on an
empty dashboard. After a restart (and after 'Reset demo data') a handful of freshly generated vendors
are run through the REAL pipeline, so the dashboard, review queue and outbox have something to show.

Honest by construction: these are real runs with real timestamps, read by the built-in offline reader
(no AI quota is spent), and every one is labelled 'example' wherever it appears. Their names are
generated so they cannot collide with the demo pack or the existing vendors."""
from __future__ import annotations

import json

from . import config, evaluation, pipeline, service
from .llm import MockLLM

SCENARIOS = ["clean", "clean", "variant_legal_form", "clean_scan", "missing_doc", "taxid_typo", "stale_bank_letter",
             "tampered_bank_letter", "holder_is_person", "sanctions_exact"]
SOURCE = "example"


def seed(conn, seed_no: int = 2026) -> int:
    """Run the example vendors once. Does nothing if the pipeline has already processed anything."""
    if conn.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"]:
        return 0
    from synthetic.generator import Generator
    g = Generator(seed_no, config.VAR_DIR / "examples")
    taken = [r["legal_name"] for r in conn.execute("SELECT legal_name FROM vendors")]
    taken += [c["form"].get("legal_name", "") for c in evaluation.load_cases()]
    g.used_names |= {n.lower() for n in taken if n}
    n = 0
    for i, sc in enumerate(SCENARIOS):
        try:
            res = getattr(g, f"s_{sc}")(f"ex-{i}")
            _, sid = service.create_submission(conn, res["form"], {k: str(v) for k, v in res.get("docs", {}).items()},
                                               source=f"{SOURCE}:{sc}")
            pipeline.process_submission(conn, sid, MockLLM())
            n += 1
        except Exception as e:                       # example data must never break the app
            print(f"example '{sc}' skipped: {e}")
    return n


def count(conn) -> int:
    return conn.execute("SELECT COUNT(*) n FROM submissions WHERE source LIKE ?", (SOURCE + ":%",)).fetchone()["n"]
