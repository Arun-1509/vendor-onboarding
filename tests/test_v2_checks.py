"""Unit tests for the v2 checks (each one found by the synthetic validation)."""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import screening
from app.validators import bank, docintegrity, names, taxid
from synthetic import docs as sdocs


def test_pan_initial_matches_company_name():
    assert taxid.pan_name_initial_ok("AABCG4411K", "Globex Logistics Pvt Ltd") is True
    assert taxid.pan_name_initial_ok("AABCR4411K", "Deccan Cables Private Limited") is False
    assert taxid.pan_name_initial_ok("AABCD4411K", "The Deccan Cables Ltd") is True        # 'The' is skipped
    assert taxid.pan_name_initial_ok("ABCPR1234F", "Ramesh Traders") is None               # individual PAN: surname rule


def test_pin_to_state():
    assert "27" in taxid.pin_states(taxid.pin_from_address("Plot 4, MIDC, Mumbai 400001"))
    assert taxid.pin_states("560001") == {"29"}
    assert taxid.pin_from_address("no pin here") is None


def test_bank_code_directories():
    assert bank.bank_from_code("GB", {"sort_code": "40-11-22"})[0] == "HSBC UK"
    assert bank.bank_from_code("DE", {"iban": "DE89500700100000000000"})[0] == "Deutsche Bank"
    assert bank.bank_from_code("IN", {"ifsc": "ICIC0000001"})[0] == "ICICI Bank"
    assert bank.bank_names_match("Deutsche Bank AG", "Deutsche Bank")
    assert not bank.bank_names_match("Hamburger Sparkasse", "Sparkasse KoelnBonn")
    assert not bank.bank_names_match("Barclays Bank", "HSBC UK")


def test_gb_iban_must_contain_sort_code_and_account():
    iban = "GB29NWBK60161331926819"
    assert bank.gb_iban_parts_mismatch({"iban": iban, "sort_code": "60-16-13", "account_number": "31926819"}) == []
    assert bank.gb_iban_parts_mismatch({"iban": iban, "sort_code": "60-16-14", "account_number": "31926819"}) == ["sort_code"]


def test_padded_lookalike_names():
    assert names.padded_variant("Aravali Cables Services Pvt Ltd", "Aravali Cables Pvt Ltd") == "services"
    assert names.padded_variant("Solent Engineering Group", "Solent Engineering LLP") == "group"
    assert names.padded_variant("Aravali Steel Pvt Ltd", "Aravali Cables Pvt Ltd") is None


def test_sanctions_exact_vs_fuzzy():
    exact = screening.sanctions_best_match(["Crest Meridian Shipping Co"])           # re-ordered = same name
    assert exact["exact"]
    fuzzy = screening.sanctions_best_match(["Blackthorn Offshre Trading Ltd"])     # 1 letter missing: human decides
    assert not fuzzy["exact"] and fuzzy["score"] >= 0.84
    two_typos = screening.sanctions_best_match(["Orioo Vale yetals LLC"])
    assert two_typos["score"] >= 0.84                                              # caught by aligned matching
    assert screening.sanctions_best_match(["Meridian Dental Care Ltd"])["score"] < 0.84


def test_abbreviations_and_ocr_names():
    assert names.compare_names("Kaveri Engg Pvt Ltd", "Kaveri Engineering Private Limited")["verdict"] == "equivalent"
    assert names.compare_names("Bodensee Antr GmbH", "Bodensee Antriebstechnik GmbH")["verdict"] == "grey"   # AI judges
    assert names.ocr_fix_name("Prairie Supp1y Corp") == "Prairie Supply Corp"
    assert names.ocr_fix_name("Unit 12 Supply") == "Unit 12 Supply"                 # real numbers untouched


def test_tampered_pdf_is_detected(tmp_path: Path):
    orig = tmp_path / "o.pdf"
    c = canvas.Canvas(str(orig), pagesize=A4)
    c.drawString(50, A4[1] - 90, "Bank: HDFC Bank")
    c.drawString(50, A4[1] - 112, "Account Holder: Kestrel Analytics Pvt Ltd")
    c.drawString(50, A4[1] - 134, "Account Number: 111122223333")
    c.save()
    clean = docintegrity.analyze(orig)
    assert clean["conflicts"] == {} and clean["incremental_updates"] == 0
    edited = tmp_path / "e.pdf"
    sdocs.tamper_overlay(orig, edited, 2, "Account Number: 999988887777", 90)
    r = docintegrity.analyze(edited)
    assert r["incremental_updates"] == 1
    assert sorted(r["conflicts"]["account_number"]) == ["111122223333", "999988887777"]
