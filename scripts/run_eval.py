"""Run every labelled case through the pipeline and print a scorecard.
   python scripts/run_eval.py            # all cases
   python scripts/run_eval.py -v         # show findings for each case
   python scripts/run_eval.py E1 H2      # only cases whose id starts with these
Exit code is non-zero if any case fails (usable in CI / before a demo)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, evaluation  # noqa: E402
from app.llm import get_llm  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    llm = get_llm()
    cases = [c for c in evaluation.load_cases() if not args or any(c["id"].startswith(a) for a in args)]
    print(f"LLM mode: {llm.mode.upper()}  |  {len(cases)} case(s)\n")
    passed = 0
    for c in cases:
        r = evaluation.run_case(c, llm)
        passed += r["passed"]
        last = r["steps"][-1]
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['id']:<40} -> {last['status']}/{last['substatus']:<18} risk {last['risk']:>3}  ({r['seconds']}s)")
        if verbose or not r["passed"]:
            for i, s in enumerate(r["steps"]):
                tag = "initial" if i == 0 else "after resubmission"
                print(f"        {tag}: {', '.join(s['codes']) or 'no findings'}")
                for f in s["fails"]:
                    print(f"        !! {f}")
    print(f"\n{passed}/{len(cases)} passed")
    sys.exit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
