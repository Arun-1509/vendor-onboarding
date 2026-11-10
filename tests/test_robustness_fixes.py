"""Regression tests for gaps found by the blind and diverse-documents benchmarks (bench/)."""
import random
from datetime import date

from app import bench, screening
from app.pipeline import _looks_like_typo
from app.validators import dates, docintegrity, names
from synthetic import layouts


def test_dates_are_grounded_as_dates_not_characters():
    assert dates.grounded_date("2026-11-09", "Datum: 09.11.2026")
    assert dates.grounded_date("2026-11-09", "Date: 9 November 2026")
    assert dates.grounded_date("2026-11-09", "Date: November 9, 2026")
    assert dates.grounded_date("2026-11-09", "Date: 09-Nov-2026")
    assert dates.grounded_date("2026-11-09", "Date: 11/09/2026")             # US style
    assert not dates.grounded_date("2026-11-09", "Date: 10.11.2026")


def test_value_only_paste_over_is_detected(tmp_path):
    bank = {"bank_name": "Commerzbank", "account_holder": "Alpen Technik GmbH", "iban": "DE89370400440532013000"}
    doc = {"kind": "bank_letter", "printed": bank, "issued_days_ago": 10, "layout": "table",
           "edited": {"field": "iban", "visible_value": "DE44500105175407324931", "original_value": "DE89370400440532013000"}}
    p, _ = layouts.render(tmp_path, "bank_proof", doc, random.Random(1), date(2026, 11, 9), "DE")
    assert docintegrity.analyze(p)["conflicts"]
    clean, _ = layouts.render(tmp_path / "c", "bank_proof", {**doc, "edited": None}, random.Random(1), date(2026, 11, 9), "DE")
    assert not docintegrity.analyze(clean)["conflicts"]


def test_typo_vs_different_account():
    assert _looks_like_typo("331600120237", "331600122037")              # neighbours swapped
    assert _looks_like_typo("12345678", "12345679")
    assert not _looks_like_typo("32561345946", "78015372157")             # a different account


def test_payee_and_affiliate_names_are_screened():
    assert screening.sanctions_best_match(["Blackthorn Offshore Trading Ltd"])["exact"]
    near = screening.sanctions_best_match(["Meridian Crest Logistics Ltd"])
    assert near["shares_distinctive_name"] == "meridian crest"
    assert "shares_distinctive_name" not in screening.sanctions_best_match(["Orion Valley Bakery LLC"])


def test_name_decorations():
    assert names.compare_names("Sharma & Sons", "M/S SHARMA AND SONS")["verdict"] in ("exact", "equivalent")
    assert names.compare_names("Cascade Signal Systems Inc DBA CascadeSig", "Cascade Signal Systems Inc")["verdict"] in ("exact", "equivalent")
    assert names.compare_names("Kleinwerk UG", "Kleinwerk UG (haftungsbeschränkt)")["verdict"] in ("exact", "equivalent")
    assert names.compare_names("Draliar Plastics LLP", "Draliar Plastics International LLP")["verdict"] == "grey"


def test_benchmarks_pass_with_a_perfect_reader():
    for name in bench.SETS:
        r = bench.run_set(name, bench.TruthLLM())
        assert r["metrics"]["errors"]["unsafe_approvals"] == 0, name
        assert r["metrics"]["errors"]["missed_rejects"] == 0, name
        # holdout: two deliberate policy disagreements with ChatGPT's labels (see VALIDATION.md)
        policy = {"H05", "H33"} if name == "holdout" else set()
        assert {f["id"] for f in r["failures"]} <= policy, [(f["id"], f["got"]) for f in r["failures"]]


def test_statement_is_caught_in_code_even_if_the_ai_calls_it_a_letter():
    import json
    from app.pipeline import _looks_like_statement
    c = next(x for x in json.load(open("bench/blind/cases.json"))["cases"] if x["id"] == "B27")
    assert _looks_like_statement(bench._abs(c["docs"]["bank_proof"]))
    ok = next(x for x in json.load(open("bench/blind/cases.json"))["cases"] if x["id"] == "B01")
    assert not _looks_like_statement(bench._abs(ok["docs"]["bank_proof"]))


def test_a_guessed_photo_reading_asks_for_a_clearer_copy_not_a_reviewer(tmp_path):
    """Two independent reads of a photo that disagree on the account number = it was not legible."""
    data = bench.load("diverse")
    case = next(c for c in data["cases"] if c["id"].startswith("D041"))
    case = {**case, "expect": {"substatus": "awaiting_vendor"}}

    class Guessy(bench.TruthLLM):
        calls = 0

        def extract_document(self, doc_type, path):
            out = super().extract_document(doc_type, path)
            if doc_type == "bank_proof":
                out = {**out, "legible": True}                       # the model claims it could read it
                Guessy.calls += 1
                f = dict(out.get("fields", {}))
                f["account_number"] = {"value": "6767928%d" % Guessy.calls, "evidence": "", "confidence": 0.95}
                out["fields"] = f
            return out
    llm = Guessy(); llm.register(case)
    mf = tmp_path / "m.json"; mf.write_text(__import__("json").dumps({"vendors": data["master"]}))
    r = bench.run_case(case, mf, llm, data["generated_for"])
    assert "DOC_UNREADABLE" in r["codes"] and r["got"] != "approved", r


def test_holdout_gaps_are_closed_by_general_rules():
    """Rules added after the ChatGPT holdout; scripts/generalization_check.py probes them on fresh vendors."""
    assert names.compare_names("O'Connell Surveying LLP", "OConnell Surveying LLP")["verdict"] == "exact"
    assert names.compare_names("Cedar Pantry, a trading name of Cedar Ridge Foods LLC", "Cedar Ridge Foods LLC")["verdict"] == "exact"
    assert names.compare_names("Cedar Pantry, a trade name of Granite Holdings LLC", "Cedar Ridge Foods LLC")["verdict"] != "exact"
    from app.pipeline import _street_numbers
    assert _street_numbers("Kanalweg 9, 04109 Leipzig") == ["9"] and _street_numbers("22 King St, Manchester M2 6AY") == ["22"]
    r = bench.run_set("holdout", bench.TruthLLM())
    assert r["metrics"]["errors"]["unsafe_approvals"] == 0 and r["metrics"]["errors"]["missed_rejects"] == 0
