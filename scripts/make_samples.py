"""Generate realistic sample documents + labelled test cases.

Run:  python scripts/make_samples.py
Writes samples/docs/**.pdf|png, samples/cases.json and data/seed_vendors.json.
Re-run before a demo: document dates are relative to today (bank letters expire after 90 days)."""
from __future__ import annotations

import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.validators.taxid import _de_vat_check_digit, gstin_check_char  # noqa: E402

DOCS = ROOT / "samples" / "docs"
TODAY = date.today()


def d(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


# ----------------------------------------------------------------- id makers
def gstin(state: str, pan: str, ent: str = "1") -> str:
    s = f"{state}{pan}{ent}Z"
    return s + gstin_check_char(s)


def bad_gstin(g: str) -> str:
    return g[:14] + ("A" if g[14] != "A" else "B")


def de_vat(n8: str) -> str:
    return "DE" + n8 + str(_de_vat_check_digit(n8))


def gb_vat(n7: str) -> str:
    w = [8, 7, 6, 5, 4, 3, 2]
    s = sum(int(x) * y for x, y in zip(n7, w))
    return f"GB{n7}{(-s) % 97:02d}"


def iban(cc: str, bban: str) -> str:
    num = "".join(str(int(c, 36)) for c in bban + cc + "00")
    return f"{cc}{98 - int(num) % 97:02d}{bban}"


def aba_ok(r: str) -> bool:
    n = [int(c) for c in r]
    return (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5] + n[8])) % 10 == 0


# ------------------------------------------------------------------ pdf maker
def make_pdf(path: Path, title: str, lines: list[str], footer: str = "") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 15)
    c.drawString(50, h - 70, title)
    c.setFont("Helvetica", 11)
    y = h - 110
    for ln in lines:
        c.drawString(50, y, ln)
        y -= 22
    if footer:
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(50, 60, footer)
    c.save()
    return str(path.relative_to(ROOT))


def make_blurry_png(path: Path, lines: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (900, 600), "white")
    dr = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    y = 40
    for ln in lines:
        dr.text((40, y), ln, fill="black", font=font)
        y += 38
    img = img.filter(ImageFilter.GaussianBlur(7)).resize((450, 300)).resize((900, 600))
    img.save(path)
    Path(str(path) + ".mock.json").write_text(json.dumps({"legible": False}))
    return str(path.relative_to(ROOT))


TAX_TITLE = {"IN": "GOODS AND SERVICES TAX - REGISTRATION CERTIFICATE", "DE": "VAT REGISTRATION CERTIFICATE (UStG)",
             "GB": "VAT REGISTRATION CERTIFICATE", "US": "FORM W-9 - REQUEST FOR TAXPAYER IDENTIFICATION NUMBER"}
TAX_LABEL = {"IN": "GSTIN", "DE": "VAT ID", "GB": "VAT Number", "US": "EIN"}


def tax_cert(case, country, legal, tid, addr, issue=None):
    return make_pdf(DOCS / case / "tax_cert.pdf", TAX_TITLE[country],
                    [f"Legal Name: {legal}", f"{TAX_LABEL[country]}: {tid}", f"Registered Address: {addr}",
                     f"Date of Issue: {issue or d(900)}"], "Synthetic sample document for testing.")


def bank_letter(case, bank, holder, ident: dict, issue=None):
    lines = [f"Bank: {bank}", f"Account Holder: {holder}"] + [f"{k}: {v}" for k, v in ident.items()] + [f"Date: {issue or d(12)}"]
    return make_pdf(DOCS / case / "bank_proof.pdf", "BANK ACCOUNT CONFIRMATION LETTER", lines, "Synthetic sample document for testing.")


# -------------------------------------------------------------- form builders
def form_in(legal, gst, acct, ifsc="HDFC0003321", bank="HDFC Bank", holder=None, trade="", etype="pvt_ltd",
            email=None, pin="400001", city="Mumbai", **kw):
    slug = "".join(c for c in legal.lower() if c.isalpha())[:20]
    f = dict(legal_name=legal, trade_name=trade, entity_type=etype, country="IN",
             address=f"Plot 14, MIDC Industrial Area, {city}", city=city, postal_code=pin, tax_id=gst,
             contact_name="Priya Nair", contact_email=email or f"accounts@{slug}.in", bank_name=bank,
             account_holder=holder or legal, account_number=acct, ifsc=ifsc, currency="INR")
    f.update(kw)
    return f


def form_de(legal, vat, ib, bank="Deutsche Bank", holder=None, **kw):
    slug = "".join(c for c in legal.lower() if c.isalpha())[:20]
    f = dict(legal_name=legal, trade_name="", entity_type="gmbh", country="DE", address="Industriestrasse 22, 60311 Frankfurt",
             city="Frankfurt", postal_code="60311", tax_id=vat, contact_name="Jonas Weber", contact_email=f"buchhaltung@{slug}.de",
             bank_name=bank, account_holder=holder or legal, iban=ib, currency="EUR")
    f.update(kw)
    return f


def form_gb(legal, vat, sort, acct, bank="Barclays Bank", holder=None, **kw):
    slug = "".join(c for c in legal.lower() if c.isalpha())[:20]
    f = dict(legal_name=legal, trade_name="", entity_type="ltd", country="GB", address="12 Quay Street, Bristol",
             city="Bristol", postal_code="BS1 4ST", tax_id=vat, contact_name="Hannah Cole", contact_email=f"finance@{slug}.co.uk",
             bank_name=bank, account_holder=holder or legal, sort_code=sort, account_number=acct, currency="GBP")
    f.update(kw)
    return f


def form_us(legal, ein, routing, acct, bank="Bank of America", holder=None, **kw):
    slug = "".join(c for c in legal.lower() if c.isalpha())[:20]
    f = dict(legal_name=legal, trade_name="", entity_type="llc", country="US", address="900 Main St, Omaha, NE",
             city="Omaha", postal_code="68102", tax_id=ein, contact_name="Marcus Hill", contact_email=f"ap@{slug}.com",
             bank_name=bank, account_holder=holder or legal, routing_number=routing, account_number=acct, currency="USD")
    f.update(kw)
    return f


def ident_in(acct, ifsc): return {"Account Number": acct, "IFSC": ifsc}
def ident_de(ib): return {"IBAN": ib}
def ident_gb(sort, acct): return {"Sort Code": sort, "Account Number": acct}
def ident_us(r, acct): return {"Routing Number": r, "Account Number": acct}


# ====================================================================== data
def main():
    if DOCS.exists():
        shutil.rmtree(DOCS)
    cases = []

    def case(cid, group, title, form, docs, expect, follow_up=None):
        cases.append({"id": cid, "group": group, "title": title, "form": form, "docs": docs,
                      "expect": expect, **({"follow_up": follow_up} if follow_up else {})})

    # ---- seed vendors (already approved) -----------------------------------------
    G_GST = gstin("27", "AABCG4411K")
    seed = [
        dict(vendor_id="V-0001", status="approved", form=form_in("Globex Logistics Solutions Pvt Ltd", G_GST, "50100234567890", "HDFC0000123")),
        dict(vendor_id="V-0002", status="approved", form=form_de("Northwind Components GmbH", "DE136695976", "DE89370400440532013000")),
        dict(vendor_id="V-0003", status="approved", form=form_gb("Brightpath Consulting Ltd", gb_vat("1234567"), "20-00-00", "55779911")),
        dict(vendor_id="V-0004", status="approved", form=form_us("Summit Office Supplies LLC", "12-3456789", "021000021", "123456789012", bank="JPMorgan Chase")),
        dict(vendor_id="V-0005", status="approved", form=form_in("Acme Technologies Private Limited", gstin("29", "AABCA7788M"), "60200011223344", "ICIC0000456", "ICICI Bank")),
    ]
    (ROOT / "data" / "seed_vendors.json").write_text(json.dumps({"_note": "Already-approved vendors that screening compares against.", "vendors": seed}, indent=2))

    # ================= HAPPY PATHS ==================================================
    cid = "H1_happy_india"
    g = gstin("07", "AAFCK2231L")
    f = form_in("Kestrel Analytics Private Limited", g, "50200098765432")
    case(cid, "happy", "Clean Indian vendor (everything consistent)", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("50200098765432", "HDFC0003321"))},
         {"status": "approved", "substatus": "approved", "max_risk": 0, "must_not_have": ["NAME_VARIANT", "LOOKALIKE_VENDOR"]})

    cid = "H2_happy_germany"
    ib = iban("DE", "50010517" + "5407324931")
    f = form_de("Rheinwerk Maschinenbau GmbH", de_vat("81234567"), ib)
    case(cid, "happy", "Clean German vendor (VAT ID + IBAN valid)", f,
         {"tax_cert": tax_cert(cid, "DE", f["legal_name"], f["tax_id"], f["address"]),
          "bank_proof": bank_letter(cid, "Deutsche Bank", f["legal_name"], ident_de(ib))},
         {"status": "approved", "max_risk": 0})

    cid = "H3_happy_uk"
    f = form_gb("Harbourline Marine Ltd", gb_vat("5551234"), "40-47-84", "70872490")
    case(cid, "happy", "Clean UK vendor", f,
         {"tax_cert": tax_cert(cid, "GB", f["legal_name"], f["tax_id"], f["address"]),
          "bank_proof": bank_letter(cid, "Barclays Bank", f["legal_name"], ident_gb("40-47-84", "70872490"))},
         {"status": "approved", "max_risk": 0})

    cid = "H4_happy_us"
    routing = next(r for r in ("011401533", "111000025", "026009593", "322271627") if aba_ok(r))
    f = form_us("Prairie Wind Energy LLC", "47-1234567", routing, "004455667788")
    case(cid, "happy", "Clean US vendor (W-9 + routing number valid)", f,
         {"tax_cert": tax_cert(cid, "US", f["legal_name"], f["tax_id"], f["address"]),
          "bank_proof": bank_letter(cid, "Bank of America", f["legal_name"], ident_us(routing, "004455667788"))},
         {"status": "approved", "max_risk": 0})

    cid = "H5_happy_india_gmail"
    g = gstin("06", "AAGCG8812C")
    f = form_in("Gamma Fabrics Private Limited", g, "30112233445566", "UTIB0001987", "Axis Bank", email="gamma.fabrics@gmail.com", city="Gurugram", pin="122001")
    case(cid, "happy", "Clean vendor, but uses a free email address (small flag, still approved)", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "Axis Bank", f["legal_name"], ident_in("30112233445566", "UTIB0001987"))},
         {"status": "approved", "must_have": ["FREE_EMAIL_DOMAIN"], "max_risk": 10})

    # ================= EDGE CASE 1: looks fine, isn't ===============================
    cid = "E1a_lookalike_new_bank"
    g = gstin("24", "AABCG9981P")
    f = form_in("Globex Logistic Solutions Pvt Ltd", g, "91400056781234", "SBIN0004512", "State Bank of India", city="Ahmedabad", pin="380001")
    case(cid, "edge1", "Lookalike of approved vendor 'Globex Logistics Solutions' with a different tax ID and new bank", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "State Bank of India", f["legal_name"], ident_in("91400056781234", "SBIN0004512"))},
         {"status": "pending", "substatus": "awaiting_reviewer", "must_have": ["LOOKALIKE_VENDOR"]})

    cid = "E1b_bank_change_request"
    new_ib = iban("DE", "10090000" + "2111223344")
    f = form_de("Northwind Components GmbH", "DE136695976", new_ib, bank="Berliner Volksbank")
    case(cid, "edge1", "Approved vendor 'Northwind' suddenly sends NEW bank details (classic payment-fraud route)", f,
         {"tax_cert": tax_cert(cid, "DE", f["legal_name"], f["tax_id"], f["address"]),
          "bank_proof": bank_letter(cid, "Berliner Volksbank", f["legal_name"], ident_de(new_ib))},
         {"status": "pending", "substatus": "awaiting_reviewer", "must_have": ["BANK_CHANGE_REQUEST"]})

    cid = "E1c_shared_bank_account"
    f = form_gb("Willowbrook Textiles Ltd", gb_vat("4400123"), "20-00-00", "55779911")
    case(cid, "edge1", "New vendor whose bank account already belongs to 'Brightpath Consulting'", f,
         {"tax_cert": tax_cert(cid, "GB", f["legal_name"], f["tax_id"], f["address"]),
          "bank_proof": bank_letter(cid, "Barclays Bank", f["legal_name"], ident_gb("20-00-00", "55779911"))},
         {"status": "pending", "substatus": "awaiting_reviewer", "must_have": ["BANK_ACCOUNT_SHARED"]})

    # ================= EDGE CASE 2: looks wrong, isn't =============================
    cid = "E2a_variant_pvt_ltd"
    g = gstin("33", "AAACA9911B")
    f = form_in("Ardent Tools Private Limited", g, "11220033445566", "ICIC0000771", "ICICI Bank", city="Chennai", pin="600001")
    case(cid, "edge2", "'Private Limited' vs 'PVT. LTD.' vs 'Pvt Ltd' across form, certificate and bank letter", f,
         {"tax_cert": tax_cert(cid, "IN", "ARDENT TOOLS PVT. LTD.", g, f["address"]),
          "bank_proof": bank_letter(cid, "ICICI Bank", "Ardent Tools Pvt Ltd", ident_in("11220033445566", "ICIC0000771"))},
         {"status": "approved", "must_have": ["NAME_VARIANT"], "must_not_have": ["ACCOUNT_HOLDER_MISMATCH", "NAME_MISMATCH_TAXCERT"]})

    cid = "E2b_variant_trade_name"
    g = gstin("29", "AAEFN3345Q")
    f = form_in("Nimbus Cloud Services LLP", g, "77001122334455", "KKBK0002231", "Kotak Mahindra Bank", trade="NimbusCloud",
                holder="NimbusCloud", etype="llp", city="Bengaluru", pin="560001")
    case(cid, "edge2", "Bank account is in the declared trade name, not the legal name", f,
         {"tax_cert": tax_cert(cid, "IN", "Nimbus Cloud Services LLP", g, f["address"]),
          "bank_proof": bank_letter(cid, "Kotak Mahindra Bank", "NimbusCloud", ident_in("77001122334455", "KKBK0002231"))},
         {"status": "approved", "must_have": ["NAME_TRADE_NAME"], "must_not_have": ["ACCOUNT_HOLDER_MISMATCH"]})

    cid = "E2c_variant_abbreviation_ai"
    g = gstin("24", "AADCD5521P")
    f = form_in("Delta Fabrication Engineering Private Limited", g, "88112233445566", "IDFB0040101", "IDFC First Bank", city="Vadodara", pin="390001")
    case(cid, "edge2", "Grey-zone name: 'Fabrication' vs 'Fab' (needs the AI judge, then a recorded explanation)", f,
         {"tax_cert": tax_cert(cid, "IN", "Delta Fab Engineering Pvt Ltd", g, f["address"]),
          "bank_proof": bank_letter(cid, "IDFC First Bank", "Delta Fab Engineering Pvt Ltd", ident_in("88112233445566", "IDFB0040101"))},
         {"status": "approved", "must_have": ["NAME_VARIANT_AI"], "max_risk": 19})

    cid = "E2d_extra_word_is_a_different_company"
    g = gstin("29", "AAFCK2231M")
    f = form_in("Kestrel Analytics Technologies Pvt Ltd", g, "50200011112222", "HDFC0003321", city="Bengaluru", pin="560002")
    case(cid, "edge2", "NEGATIVE: form name has an extra word vs the certificate; this must NOT be waved through as a 'variant'", f,
         {"tax_cert": tax_cert(cid, "IN", "Kestrel Analytics Private Limited", g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", "Kestrel Analytics Private Limited", ident_in("50200011112222", "HDFC0003321"))},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["NAME_UNRESOLVED"], "must_not_have": ["NAME_VARIANT_AI"]})

    # ================= EDGE CASE 3: wait and resume ================================
    cid = "E3a_missing_bank_letter_then_resubmit"
    g = gstin("27", "AAHCO6677R")
    f = form_in("Orchid Packaging Private Limited", g, "44556677889900", "HDFC0002290", city="Pune", pin="411001")
    case(cid, "edge3", "Bank letter missing -> email -> vendor uploads it -> only the failed check clears -> approved", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"])},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["MISSING_DOC"]},
         follow_up={"add_docs": {"bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("44556677889900", "HDFC0002290"))},
                    "expect": {"status": "approved", "resolved_includes": ["MISSING_DOC:bank_proof"]}})

    cid = "E3b_missing_fields"
    g = gstin("19", "AAJCS3344T")
    f = form_in("Sundarban Spices Private Limited", g, "66778899001122", "HDFC0006655", city="Kolkata", pin="")
    f["contact_email"] = ""
    case(cid, "edge3", "Form incomplete (no PIN code, no contact email)", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("66778899001122", "HDFC0006655"))},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["MISSING_FIELD"]})

    # ================= EDGE CASE 4: subtle contradictions ==========================
    cid = "E4a_pan_is_individual"
    g = gstin("27", "ABCPR1234F")
    f = form_in("Sapphire Interiors Private Limited", g, "12312312312312", "ICIC0000771", "HDFC Bank", city="Mumbai", pin="400050")
    case(cid, "edge4", "Claims to be a Pvt Ltd, but its PAN is an INDIVIDUAL's; IFSC bank != named bank", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("12312312312312", "ICIC0000771"))},
         {"status": "pending", "substatus": "awaiting_reviewer", "must_have": ["ENTITY_TYPE_MISMATCH_PAN", "BANK_NAME_IFSC_MISMATCH"]})

    cid = "E4b_country_contradiction"
    g = gstin("29", "AAKCT4455U")
    f = form_in("Teutonic Precision GmbH", g, "99887766554433", "HDFC0001010", city="Berlin", pin="10115", country="DE", etype="gmbh", currency="EUR")
    case(cid, "edge4", "Form says Germany, but the tax ID, IFSC bank code and account are all Indian", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("99887766554433", "HDFC0001010"))},
         {"status": "pending", "substatus": "awaiting_reviewer", "must_have": ["COUNTRY_CONTRADICTION"]})

    # ================= vendor-fixable problems ======================================
    cid = "V1_bad_gstin_checksum"
    g = bad_gstin(gstin("19", "AALCP9988V"))
    f = form_in("Sunrise Print Works Private Limited", g, "55443322110099", "HDFC0004455", city="Kolkata", pin="700001")
    case(cid, "fixable", "GSTIN has a wrong check character (typo)", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("55443322110099", "HDFC0004455"))},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["TAXID_CHECKSUM"]})

    cid = "V2_bad_iban_checksum"
    good = iban("DE", "70070010" + "0123456789")
    bad = good[:-1] + ("0" if good[-1] != "0" else "1")
    f = form_de("Elbtal Werkzeuge GmbH", de_vat("29845612"), bad)
    case(cid, "fixable", "IBAN check digits fail", f,
         {"tax_cert": tax_cert(cid, "DE", f["legal_name"], f["tax_id"], f["address"]),
          "bank_proof": bank_letter(cid, "Deutsche Bank", f["legal_name"], ident_de(bad))},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["BANK_CHECKSUM"]})

    cid = "V3_account_in_other_name"
    g = gstin("08", "AAMCC1122W")
    f = form_in("Crestline Hardware Private Limited", g, "22334455667788", "SBIN0001234", "State Bank of India", holder="Rajesh Kumar Enterprises", city="Jaipur", pin="302001")
    case(cid, "fixable", "Bank account is in a different party's name", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "State Bank of India", "Rajesh Kumar Enterprises", ident_in("22334455667788", "SBIN0001234"))},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["ACCOUNT_HOLDER_MISMATCH"]})

    cid = "V4_taxid_form_vs_certificate"
    g1, g2 = gstin("10", "AANCB5566X"), gstin("10", "AANCB5577Y")
    f = form_in("Bihar Brothers Traders Private Limited", g1, "33445566778899", "PUNB0123400", "Punjab National Bank", city="Patna", pin="800001")
    case(cid, "fixable", "Tax ID typed on the form differs from the one on the certificate", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g2, f["address"]),
          "bank_proof": bank_letter(cid, "Punjab National Bank", f["legal_name"], ident_in("33445566778899", "PUNB0123400"))},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["TAXID_MISMATCH_DOC"]})

    cid = "V5_wrong_document"
    g = gstin("32", "AAPCQ2211Z")
    f = form_in("Quilon Coir Private Limited", g, "11002200330044", "HDFC0007788", city="Kochi", pin="682001")
    wrong = make_pdf(DOCS / cid / "tax_cert.pdf", "ELECTRICITY BILL - OCTOBER", [f"Customer Name: {f['legal_name']}", "Units Consumed: 1,240 kWh", "Amount Due: INR 18,420", f"Date: {d(10)}"])
    case(cid, "fixable", "Vendor attached an electricity bill instead of the tax certificate", f,
         {"tax_cert": wrong, "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("11002200330044", "HDFC0007788"))},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["WRONG_DOCUMENT"]})

    cid = "V6_stale_bank_letter"
    g = gstin("36", "AAQCR3300A")
    f = form_in("Deccan Cables Private Limited", g, "55660077889911", "HDFC0009900", city="Hyderabad", pin="500001")
    case(cid, "fixable", "Bank letter is ~200 days old", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("55660077889911", "HDFC0009900"), issue=d(200))},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["DOC_STALE"]})

    cid = "V7_blurry_scan"
    g = gstin("03", "AARCS7766B")
    f = form_in("Punjab Agro Foods Private Limited", g, "88990011223344", "PUNB0555500", "Punjab National Bank", city="Ludhiana", pin="141001")
    blurry = make_blurry_png(DOCS / cid / "bank_proof.png", ["BANK ACCOUNT CONFIRMATION LETTER", "Bank: Punjab National Bank", f"Account Holder: {f['legal_name']}", "Account Number: 88990011223344", "IFSC: PUNB0555500"])
    case(cid, "fixable", "Bank letter is a blurry phone photo", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]), "bank_proof": blurry},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["DOC_UNREADABLE"]})

    cid = "V8_certificate_for_other_company"
    g = gstin("27", "AASCZ9090D")
    f = form_in("Mosaic Healthcare Private Limited", g, "21436587092143", "HDFC0003344", city="Nagpur", pin="440001")
    case(cid, "fixable", "Tax certificate belongs to a different company", f,
         {"tax_cert": tax_cert(cid, "IN", "Zenith Retail Ventures LLP", g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("21436587092143", "HDFC0003344"))},
         {"status": "pending", "substatus": "awaiting_vendor", "must_have": ["NAME_MISMATCH_TAXCERT"]})

    # ================= compliance =====================================================
    cid = "R1_sanctions_match"
    g = gstin("27", "AATCM1212E")
    f = form_in("Meridian Crest Shipping Company Pvt Ltd", g, "13572468091357", "HDFC0001122", city="Mumbai", pin="400001")
    case(cid, "reject", "Name matches an entry on the (fictional) sanctions list", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("13572468091357", "HDFC0001122"))},
         {"status": "rejected", "substatus": "rejected_compliance", "must_have": ["SANCTIONS_HIT"]})

    cid = "R2_sanctions_near_match"
    g = gstin("27", "AAUCM3434F")
    f = form_in("Meridian Crest Shipments Pvt Ltd", g, "24681357902468", "HDFC0001133", city="Mumbai", pin="400002")
    case(cid, "reject", "Name is CLOSE to a sanctions entry: a human must rule out a false positive", f,
         {"tax_cert": tax_cert(cid, "IN", f["legal_name"], g, f["address"]),
          "bank_proof": bank_letter(cid, "HDFC Bank", f["legal_name"], ident_in("24681357902468", "HDFC0001133"))},
         {"status": "pending", "substatus": "awaiting_reviewer", "must_have": ["SANCTIONS_NEAR"]})

    (ROOT / "samples" / "cases.json").write_text(json.dumps({"_note": "Labelled test cases. 'expect' is what a correct pipeline must produce.", "cases": cases}, indent=2))
    print(f"Wrote {len(cases)} cases, {sum(1 for _ in DOCS.rglob('*') if _.is_file() and not _.name.endswith('.json'))} documents, {len(seed)} seed vendors.")


if __name__ == "__main__":
    main()
