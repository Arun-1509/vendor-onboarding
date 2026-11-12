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
        # fresh: N22 (every document German, 'United Kingdom' picked in the dropdown) goes to a person here; the author would ask the vendor
        policy = {"H05", "H33"} if name == "holdout" else {"N22"} if name == "fresh" else set()
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


def test_files_that_cannot_be_opened_are_the_vendors_to_fix_not_an_ai_job(tmp_path):
    from app.pipeline import _unopenable
    from app.service import normalize_form
    for name, data in (("a.pdf", b""), ("b.pdf", b"%PDF-1.4 garbage garbage " * 5), ("c.pdf", b"not a pdf at all " * 8), ("d.jpg", b"hello " * 20)):
        p = tmp_path / name
        p.write_bytes(data)
        assert _unopenable(str(p)), name
    f = normalize_form({"legal_name": "A" * 20000, "country": 5, "tax_id": None, "postal_code": {"x": 1}, "address": "x\x00y\n" * 500})
    assert len(f["legal_name"]) == 160 and f["postal_code"] == "" and len(f["address"]) <= 300 and "\x00" not in f["address"]


def test_real_sample_documents_all_open():
    import json as _j
    from app import evaluation
    from app.pipeline import _unopenable
    for c in evaluation.load_cases():
        for p in evaluation._abs(c["docs"]).values():
            assert not _unopenable(p), p
    for name in bench.SETS:
        for c in bench.load(name)["cases"]:
            for p in c["docs"].values():
                assert not _unopenable(str(bench.ROOT / p) if hasattr(bench, "ROOT") else p), p


def test_fresh_unseen_set_gaps_are_closed_by_general_rules():
    """Rules added after the second unseen set; scripts/generalization_check.py probes each on new vendors."""
    from app.pipeline import EMAIL_RE, _lookalike_domain
    from app.validators import taxid
    assert names.compare_names("Mueller und Soehne Maschinenbau GmbH", "Müller & Söhne Maschinenbau GmbH")["verdict"] in ("exact", "equivalent")
    assert names.compare_names("Rheinhafen Spedition AG", "Rheinhafen Spedition Aktiengesellschaft")["verdict"] in ("exact", "equivalent")
    assert names.compare_names("3S Engineering Works LLP", "3 S ENGINEERING WORKS LLP")["verdict"] in ("exact", "equivalent")
    assert names.compare_names("Kelviex Cmpnts Limited", "Kelviex Components Limited")["verdict"] == "grey" and names.is_abbreviation_of("Kelviex Cmpnts", "Kelviex Components")
    assert names.replaced_word("VISTARA AGRO TRADING PRIVATE LIMITED", "Vistara Agro Foods Pvt Ltd")
    assert not names.replaced_word("Corrius Tech Industriebedarf GmbH", "Corrius Technik Industriebedarf GmbH")     # an abbreviation
    assert not names.replaced_word("Kestral Analytics Pvt Ltd", "Kestrel Analytics Pvt Ltd")                        # a typo
    assert not names.replaced_word("Orion Foods Ltd", "Zenith Trading Ltd")                                         # nothing in common
    assert _lookalike_domain("ar@carro11ton-fasteners.com", "Carrollton Industrial Fasteners Inc") == "carrollton"
    assert _lookalike_domain("ar@carrollton24.com", "Carrollton Industrial Fasteners Inc") is None
    assert _lookalike_domain("accounts@3sengineering.in", "3S Engineering Works LLP") is None
    assert not EMAIL_RE.match("finance@pennineupholstery,co.uk") and EMAIL_RE.match("a.b+c@mail.firma-müller.de")
    assert not taxid.validate_tax_id("US", "418-62-3307").ok and taxid.validate_tax_id("US", "41-8623307").ok
    assert taxid.pan_name_initial_ok("LZTFS4962Z", "3S Engineering Works LLP") is None
