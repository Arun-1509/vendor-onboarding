"""Compile the BLIND benchmark: scenarios and expected outcomes written by an independent author
who never saw this codebase (bench/blind/spec.json), rendered into real documents with the
varied layouts in synthetic/layouts.py. Seeded vendor master = data/seed_vendors.json (what the
author was given).

    python scripts/bench.py build-blind
"""
from __future__ import annotations

import json
import random
import shutil
from datetime import date
from pathlib import Path

from . import layouts
from .generator import ROOT

STATUS = {"approved": ("approved", "approved"), "awaiting_vendor": ("pending", "awaiting_vendor"),
          "awaiting_reviewer": ("pending", "awaiting_reviewer"), "rejected": ("rejected", "rejected_compliance")}


def build(spec_path: Path | None = None, seed: int = 101, out: Path | None = None) -> Path:
    out = out or ROOT / "bench" / "blind"
    spec_path = spec_path or out / "spec.json"
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    docs_dir = out / "docs"
    if docs_dir.exists():
        shutil.rmtree(docs_dir)
    docs_dir.mkdir(parents=True)
    if Path(spec_path).resolve() != (out / "spec.json").resolve():
        shutil.copy(spec_path, out / "spec.json")
    rng, today = random.Random(seed), date.today()
    cases = []
    for c in spec["cases"]:
        form = {k: (v or "") for k, v in c["form"].items()}
        docs, truth = {}, {}
        for slot, d in (c.get("documents") or {}).items():
            if not d:
                continue
            p, t = layouts.render(docs_dir / c["id"], slot, d, rng, today, form.get("country", "US"))
            docs[slot] = str(p.resolve().relative_to(ROOT)).replace("\\", "/")
            truth[slot] = t
        st, sub = STATUS[c["expected"]]
        cases.append({"id": c["id"], "scenario": c["expected"], "group": c.get("difficulty", ""), "title": c["title"],
                      "story": c.get("story", ""), "why": c.get("why", ""), "difficulty": c.get("difficulty", ""),
                      "country": form.get("country"), "form": form, "docs": docs, "doc_truth": truth,
                      "expect": {"status": st, "substatus": sub}})
    master = json.loads((ROOT / "data" / "seed_vendors.json").read_text(encoding="utf-8"))["vendors"]
    meta = {"split": "blind", "seed": seed, "n": len(cases), "generated_for": today.isoformat(), "master": master,
            "author_note": spec.get("author_note", ""), "cases": cases}
    (out / "cases.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
