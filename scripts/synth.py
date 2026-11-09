"""Generate a labelled synthetic dataset.
   python scripts/synth.py --split dev  --n 800 --seed 11
   python scripts/synth.py --split test --n 800 --seed 29
Output: var/synth/<split>/cases.json + rendered documents."""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synthetic.generator import generate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    out = generate(a.n, a.seed, a.split)
    import json
    d = json.loads((out / "cases.json").read_text(encoding="utf-8"))
    c = Counter(x["expect"]["substatus"] for x in d["cases"])
    print(f"{d['n']} cases -> {out}")
    print("  by expected outcome:", dict(c))
    print("  by country:", dict(Counter(x["country"] for x in d["cases"])))


if __name__ == "__main__":
    main()
