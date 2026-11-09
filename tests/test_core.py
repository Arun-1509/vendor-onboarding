"""Unit tests for the parts that must never be wrong: checksums, name logic, decision rules,
the audit chain. Run: pytest -q"""
import tempfile
from pathlib import Path

import pytest

from app import db, rules
from app.validators import bank, names, taxid


# ----------------------------------------------------------- checksums ----
@pytest.mark.parametrize("fn,good,bad", [
    (taxid.validate_gstin, "27AAPFU0939F1ZV", "27AAPFU0939F1ZA"),
    (taxid.validate_de_vat, "DE136695976", "DE136695977"),
    (taxid.validate_gb_vat, "GB980780684", "GB980780685"),
    (bank.validate_iban, "DE89370400440532013000", "DE89370400440532013001"),
    (bank.validate_aba, "021000021", "021000022"),
])
def test_checksums_accept_good_reject_bad(fn, good, bad):
    assert fn(good).ok
    r = fn(bad)
    assert not r.ok and r.kind == "checksum"


def test_formats_and_country_detection():
    assert not taxid.validate_us_ein("07-1234567").ok          # 07 is not an IRS prefix
    assert not bank.validate_ifsc("HDFC1001234").ok            # 5th char must be 0
    assert taxid.detect_tax_id_country("27AAPFU0939F1ZV") == "IN"
    assert taxid.detect_tax_id_country("DE136695976") == "DE"
    assert bank.ifsc_bank_matches_name("HDFC0001234", "HDFC Bank Ltd") is True
    assert bank.ifsc_bank_matches_name("ICIC0000001", "HDFC Bank") is False


def test_pan_entity_type_consistency():
    assert taxid.pan_matches_declared_entity("pvt_ltd", "C") is True
    assert taxid.pan_matches_declared_entity("pvt_ltd", "P") is False   # individual PAN, company claimed
    assert taxid.pan_matches_declared_entity("llp", "F") is True


# --------------------------------------------------------------- names ----
@pytest.mark.parametrize("a,b,verdict", [
    ("Acme Tech Pvt Ltd", "ACME TECH PRIVATE LIMITED", "equivalent"),
    ("Ardent Tools Private Limited", "ARDENT TOOLS PVT. LTD.", "equivalent"),
    ("Smith & Sons Ltd", "Smith and Sons Limited", "equivalent"),
    ("Acme Ltd", "Acme LLP", "form_mismatch"),                       # different legal forms
    ("Delta Fabrication Engineering Pvt Ltd", "Delta Fab Engineering Pvt Ltd", "grey"),
    ("Kestrel Analytics Pvt Ltd", "Zenith Retail Ventures LLP", "different"),
])
def test_name_verdicts(a, b, verdict):
    assert names.compare_names(a, b)["verdict"] == verdict


def test_abbreviation_never_means_dropped_word():
    assert names.is_abbreviation_of("Delta Fab Engineering", "Delta Fabrication Engineering")
    # regression: this slipped through once and approved a different company name
    assert not names.is_abbreviation_of("Kestrel Analytics", "Kestrel Analytics Technologies")


# --------------------------------------------------------------- rules ----
def F(code, **kw):
    return rules.finding(code, "x", **kw)


def test_precedence_reject_over_reviewer_over_vendor():
    d = rules.decide([F("MISSING_DOC"), F("LOOKALIKE_VENDOR"), F("SANCTIONS_HIT")])
    assert (d["status"], d["substatus"]) == ("rejected", "rejected_compliance")
    d = rules.decide([F("MISSING_DOC"), F("LOOKALIKE_VENDOR")])
    assert (d["status"], d["substatus"]) == ("pending", "awaiting_reviewer")
    d = rules.decide([F("MISSING_DOC")])
    assert (d["status"], d["substatus"]) == ("pending", "awaiting_vendor")


def test_info_findings_do_not_block_but_risk_adds_up():
    d = rules.decide([F("NAME_VARIANT"), F("FREE_EMAIL_DOMAIN")])
    assert d["status"] == "approved" and d["risk_score"] == 5
    many = [F("CURRENCY_UNUSUAL"), F("BANK_COUNTRY_DIFFERS"), F("FREE_EMAIL_DOMAIN")]       # 5+15+5 = 25
    d = rules.decide(many)
    assert d["risk_score"] == 25 and d["substatus"] == "awaiting_reviewer"
    assert any(f["code"] == "RISK_OVER_LIMIT" for f in d["findings"])


def test_decision_is_deterministic_and_order_independent():
    fs = [F("MISSING_DOC", field="bank_proof"), F("TAXID_FORMAT", field="tax_id"), F("NAME_VARIANT")]
    a = rules.decide(fs)
    b = rules.decide(list(reversed(fs)))
    assert (a["status"], a["substatus"], a["risk_score"]) == (b["status"], b["substatus"], b["risk_score"])


def test_every_rule_has_a_plain_english_reason():
    for r in rules.RULEBOOK.values():
        assert r["title"] and r["why"] and r["action"] in ("none", "vendor", "reviewer", "reject")


# --------------------------------------------------------------- audit ----
def test_audit_chain_detects_tampering():
    with tempfile.TemporaryDirectory() as t:
        c = db.connect(Path(t) / "a.db")
        for i in range(5):
            db.audit(c, "evt", {"i": i}, "V-1")
        assert db.verify_audit(c)["ok"]
        c.execute("UPDATE audit SET payload='{\"i\":99}' WHERE seq=3")        # rewrite history
        r = db.verify_audit(c)
        assert not r["ok"] and r["broken_at_seq"] == 3
        c.close()


def test_audit_detects_deleted_row():
    with tempfile.TemporaryDirectory() as t:
        c = db.connect(Path(t) / "a.db")
        for i in range(4):
            db.audit(c, "evt", {"i": i})
        c.execute("DELETE FROM audit WHERE seq=2")
        assert not db.verify_audit(c)["ok"]
        c.close()
