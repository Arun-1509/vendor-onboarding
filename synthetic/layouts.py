"""Varied document renderer for the robustness benchmarks (bench/).

The main generator draws every document in one or two 'Label: value' shapes. Real documents
are messier, so this module renders the SAME facts in many shapes:

  PDF layouts : kv (label: value) | table (bordered grid) | prose (values inside sentences) |
                twocol (label above value, two columns) | multipage (fields on page 2) |
                stamped (a bank stamp and signature drawn across the text)
  languages   : English, German (German labels and dd.mm.yyyy dates)
  date styles : per country (09.11.2026, 9 November 2026, 11/09/2026, 09-Nov-2026, ISO)
  photos      : a phone photo of the page: perspective skew, rotation, uneven light, noise, JPEG
  kinds       : GST / VAT certificate, W-9, bank letter, cancelled cheque, bank statement,
                utility bill, invoice
  edits       : a genuine PDF with one value pasted over (incremental save; original stays inside)

render() returns the file path and the GROUND TRUTH of what is printed, so a benchmark can score
the AI's reading field by field, independently of the decision.
"""
from __future__ import annotations

import io
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

FOOT = "Synthetic document generated for testing. Not a real record."
PDF_LAYOUTS = ["kv", "table", "prose", "twocol", "multipage", "stamped"]

TAX_KINDS = {"gst_certificate", "vat_certificate", "w9"}
BANK_KINDS = {"bank_letter", "cancelled_cheque", "bank_statement"}
OTHER_KINDS = {"utility_bill", "invoice", "other"}

# field -> label choices, per language
LABELS = {
    "en": {
        "legal_name": ["Legal Name", "Registered Name", "Legal Name of Business", "Name of Business"],
        "trade_name": ["Trade Name", "Trading Name"],
        "address": ["Registered Address", "Address of Principal Place of Business", "Business Address"],
        "issue_date": ["Date of Issue", "Date", "Issue Date"],
        "bank_name": ["Bank", "Bank Name", "Name of Bank"],
        "account_holder": ["Account Holder", "Account Name", "Name of Account Holder", "Beneficiary Name"],
        "account_number": ["Account Number", "Account No.", "A/c No."],
        "iban": ["IBAN"], "ifsc": ["IFSC", "IFSC Code"], "sort_code": ["Sort Code"],
        "routing_number": ["Routing Number", "ABA Routing No."], "swift": ["SWIFT/BIC", "BIC"],
    },
    "de": {
        "legal_name": ["Firma", "Name / Firma", "Unternehmen"], "trade_name": ["Handelsname"],
        "address": ["Anschrift", "Sitz der Gesellschaft"], "issue_date": ["Datum", "Ausstellungsdatum"],
        "bank_name": ["Kreditinstitut", "Bank"], "account_holder": ["Kontoinhaber"],
        "account_number": ["Kontonummer"], "iban": ["IBAN"], "ifsc": ["IFSC"], "sort_code": ["Sort Code"],
        "routing_number": ["Routing Number"], "swift": ["BIC"],
    },
}
TAX_ID_LABEL = {
    ("IN", "en"): ["GSTIN", "Registration Number (GSTIN)"], ("DE", "en"): ["VAT ID", "VAT Identification Number"],
    ("DE", "de"): ["USt-IdNr.", "Umsatzsteuer-Identifikationsnummer"], ("GB", "en"): ["VAT Registration Number", "VAT Number"],
    ("US", "en"): ["Employer Identification Number (EIN)", "EIN"],
}
DATE_STYLES = {
    ("DE", "de"): ["%d.%m.%Y"], ("DE", "en"): ["%d.%m.%Y", "%d %B %Y", "%Y-%m-%d"],
    ("GB", "en"): ["%d %B %Y", "%d/%m/%Y"], ("US", "en"): ["%m/%d/%Y", "%B %d, %Y"],
    ("IN", "en"): ["%d/%m/%Y", "%d-%b-%Y", "%d.%m.%Y"],
}
TITLES = {
    ("gst_certificate", "en"): [("Government of India", "Form GST REG-06 - Registration Certificate"),
                                ("Goods and Services Tax", "Certificate of Registration")],
    ("vat_certificate", "en"): [("HM Revenue & Customs", "Certificate of Registration for Value Added Tax"),
                                ("Federal Central Tax Office", "Confirmation of VAT Identification Number")],
    ("vat_certificate", "de"): [("Bundeszentralamt fuer Steuern", "Bestaetigung ueber die Erteilung der Umsatzsteuer-Identifikationsnummer"),
                                ("Finanzamt", "Mitteilung ueber die Umsatzsteuer-Identifikationsnummer")],
    ("w9", "en"): [("Department of the Treasury - Internal Revenue Service", "Form W-9 Request for Taxpayer Identification Number and Certification")],
    ("bank_letter", "en"): [(None, "Bank Account Confirmation"), (None, "Confirmation of Account Details"), (None, "Account Verification Letter")],
    ("bank_letter", "de"): [(None, "Kontobestaetigung"), (None, "Bankbestaetigung")],
    ("cancelled_cheque", "en"): [(None, "CHEQUE")],
    ("bank_statement", "en"): [(None, "Statement of Account")], ("bank_statement", "de"): [(None, "Kontoauszug")],
    ("utility_bill", "en"): [("City Power Distribution Co.", "Electricity Bill")], ("utility_bill", "de"): [("Stadtwerke", "Stromrechnung")],
    ("invoice", "en"): [(None, "Tax Invoice")], ("invoice", "de"): [(None, "Rechnung")],
    ("other", "en"): [(None, "Company Profile")], ("other", "de"): [(None, "Unternehmensprofil")],
}
PROSE = {
    "en": ["To whom it may concern,", "We confirm that the account described below is held with us and is active",
           "as of the date of this letter. This letter is issued at the request of our customer."],
    "de": ["Sehr geehrte Damen und Herren,", "hiermit bestaetigen wir, dass das unten genannte Konto bei uns gefuehrt wird",
           "und zum Datum dieses Schreibens aktiv ist."],
}
BANK_ORDER = ["bank_name", "account_holder", "account_number", "iban", "ifsc", "sort_code", "routing_number", "swift", "issue_date"]
TAX_ORDER = ["legal_name", "trade_name", "tax_id", "address", "issue_date"]


def doc_type_of(kind: str) -> str:
    if kind == "bank_statement":
        return "bank_statement"
    return "tax_cert" if kind in TAX_KINDS else "bank_proof" if kind in BANK_KINDS else "other"


def _ascii_de(s: str) -> str:
    """Helvetica in reportlab covers Latin-1, but keep German titles plain-ASCII for portability."""
    return s


def fmt_date(d: date, country: str, lang: str, rng: random.Random) -> str:
    style = rng.choice(DATE_STYLES.get((country, lang), ["%Y-%m-%d"]))
    s = d.strftime(style)
    if "%d" in style and style.startswith("%d ") and s.startswith("0"):
        s = s[1:]                                           # '9 November 2026', not '09 November 2026'
    return s


def _rows(doc: dict, rng: random.Random, lang: str, country: str) -> list[tuple[str, str, str]]:
    """-> [(field, label, value)] in reading order, for the fields this document prints."""
    printed = doc["printed"]
    kind = doc["kind"]
    order = TAX_ORDER if kind in TAX_KINDS else BANK_ORDER
    out = []
    for f in order:
        v = printed.get(f)
        if not v:
            continue
        if f == "tax_id":
            lab = rng.choice(TAX_ID_LABEL.get((country, lang)) or TAX_ID_LABEL.get((country, "en")) or ["Tax ID"])
        else:
            lab = rng.choice(LABELS[lang].get(f) or LABELS["en"][f])
        out.append((f, lab, str(v)))
    for f, v in printed.items():                            # anything else the author printed
        if f not in order and v and f in LABELS["en"]:
            out.append((f, rng.choice(LABELS[lang].get(f) or LABELS["en"][f]), str(v)))
    return out


# ================================================================== PDF ====
class _Pdf:
    """Small drawing helper that remembers where each field VALUE was drawn (for edits)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.c = canvas.Canvas(str(path), pagesize=A4)
        self.w, self.h = A4
        self.pos: dict[str, tuple[float, float, int, int]] = {}  # field -> (x, y, size, page)
        self.page = 0

    def text(self, x, y, s, size=11, bold=False, field=None, italic=False):
        font = "Helvetica-Bold" if bold else "Helvetica-Oblique" if italic else "Helvetica"
        self.c.setFont(font, size)
        self.c.drawString(x, y, s)
        if field:
            self.pos[field] = (x, y, size, self.page)

    def new_page(self):
        self.c.setFont("Helvetica-Oblique", 8)
        self.c.drawString(50, 40, FOOT)
        self.c.showPage()
        self.page += 1

    def save(self):
        self.c.setFont("Helvetica-Oblique", 8)
        self.c.drawString(50, 40, FOOT)
        self.c.save()


def _header(p: _Pdf, doc: dict, rng, lang) -> float:
    kind = doc["kind"]
    issuer, title = rng.choice(TITLES.get((kind, lang)) or TITLES[(kind, "en")])
    bank = doc["printed"].get("bank_name")
    top = issuer or (bank if kind in BANK_KINDS and bank else None)
    y = p.h - 60
    if top:
        p.text(50, y, top, 16, bold=True)
        if kind in BANK_KINDS and doc.get("city"):
            p.text(50, y - 16, f"{doc['city']} branch", 9)
        y -= 34
        p.c.line(50, y + 10, p.w - 50, y + 10)
        y -= 14
    p.text(50, y, title, 14, bold=True)
    return y - 30


def _kv(p, rows, y, x=50, gap=22, size=11):
    for f, lab, v in rows:
        p.text(x, y, f"{lab}: ", size)
        lw = p.c.stringWidth(f"{lab}: ", "Helvetica", size)
        p.text(x + lw, y, v, size, field=f)
        y -= gap
    return y


def _table(p, rows, y):
    x0, x1, x2 = 50, 230, p.w - 50
    rh = 22
    p.c.rect(x0, y - rh * len(rows) + 6, x2 - x0, rh * len(rows), stroke=1, fill=0)
    p.c.line(x1, y + 6, x1, y - rh * len(rows) + 6)
    for i, (f, lab, v) in enumerate(rows):
        yy = y - i * rh - 9
        if i:
            p.c.line(x0, y - i * rh + 6, x2, y - i * rh + 6)
        p.text(x0 + 6, yy, lab, 10, bold=True)
        p.text(x1 + 6, yy, v, 10, field=f)
    return y - rh * len(rows) - 10


def _twocol(p, rows, y):
    half = (len(rows) + 1) // 2
    for col, chunk in enumerate((rows[:half], rows[half:])):
        yy = y
        x = 50 + col * 260
        for f, lab, v in chunk:
            p.text(x, yy, lab.upper(), 8)
            p.text(x, yy - 13, v, 11, field=f)
            yy -= 36
    return y - 36 * half


def _prose_bank(p, rows, y, lang):
    """Values woven into sentences, as many real bank letters are written."""
    d = {f: v for f, _, v in rows}
    lines = []
    if lang == "de":
        if d.get("account_holder"):
            lines.append(("account_holder", "Kontoinhaber ist ", d["account_holder"], "."))
        for f, pre in (("iban", "Das Konto mit der IBAN "), ("account_number", "Die Kontonummer lautet ")):
            if d.get(f):
                lines.append((f, pre, d[f], " wird bei uns gefuehrt."))
        if d.get("swift"):
            lines.append(("swift", "BIC: ", d["swift"], ""))
    else:
        if d.get("account_holder"):
            lines.append(("account_holder", "This is to certify that ", d["account_holder"], " maintains an account with us."))
        for f, pre in (("account_number", "The account number is "), ("iban", "The IBAN of the account is "),
                       ("ifsc", "The IFSC of our branch is "), ("sort_code", "Our sort code is "),
                       ("routing_number", "Our ABA routing number is "), ("swift", "SWIFT/BIC: ")):
            if d.get(f):
                lines.append((f, pre, d[f], "." if f != "swift" else ""))
    if d.get("bank_name"):
        lines.append(("bank_name", "Issued by " if lang == "en" else "Ausgestellt von ", d["bank_name"], ""))
    for f, pre, v, post in lines:
        p.text(50, y, pre, 11)
        w = p.c.stringWidth(pre, "Helvetica", 11)
        p.text(50 + w, y, v, 11, field=f)
        if post:
            p.text(50 + w + p.c.stringWidth(v, "Helvetica", 11), y, post, 11)
        y -= 20
    if d.get("issue_date"):
        lab = "Date: " if lang == "en" else "Datum: "
        p.text(50, y - 6, lab, 11)
        p.text(50 + p.c.stringWidth(lab, "Helvetica", 11), y - 6, d["issue_date"], 11, field="issue_date")
        y -= 26
    return y


def _stamp(p, x, y, bank, rng):
    c = p.c
    c.saveState()
    c.translate(x, y)
    c.rotate(rng.uniform(-25, 25))
    c.setStrokeColorRGB(0.15, 0.25, 0.65)
    c.setFillColorRGB(0.15, 0.25, 0.65)
    c.setLineWidth(2)
    c.circle(0, 0, 46, stroke=1, fill=0)
    c.circle(0, 0, 38, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(0, 6, (bank or "BANK")[:22].upper())
    c.drawCentredString(0, -6, "AUTHORISED")
    c.restoreState()
    c.saveState()
    c.setStrokeColorRGB(0.1, 0.1, 0.35)
    c.setLineWidth(1.2)
    pth = c.beginPath()
    sx, sy = x + 60, y - 20
    pth.moveTo(sx, sy)
    for i in range(1, 9):
        pth.curveTo(sx + i * 9 - 5, sy + rng.uniform(-12, 14), sx + i * 9 - 2, sy + rng.uniform(-12, 14), sx + i * 9, sy)
    c.drawPath(pth, stroke=1, fill=0)
    c.restoreState()


def _statement_tail(p, y, rng, lang):
    head = ["Date", "Description", "Amount", "Balance"] if lang == "en" else ["Datum", "Buchungstext", "Betrag", "Saldo"]
    xs = [50, 140, 380, 470]
    for x, h in zip(xs, head):
        p.text(x, y, h, 9, bold=True)
    bal = rng.randint(20000, 900000) / 100
    for i in range(7):
        y -= 16
        amt = rng.choice([-1, 1]) * rng.randint(1000, 90000) / 100
        bal += amt
        desc = rng.choice(["NEFT TRANSFER", "CARD PAYMENT", "SUPPLIER PAYMENT", "SALARY BATCH", "UTILITY DIRECT DEBIT"]) \
            if lang == "en" else rng.choice(["UEBERWEISUNG", "LASTSCHRIFT", "GUTSCHRIFT", "KARTENZAHLUNG"])
        for x, s in zip(xs, [f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}", desc, f"{amt:,.2f}", f"{bal:,.2f}"]):
            p.text(x, y, s, 9)
    return y - 20


def _cheque(p, rows, rng):
    d = {f: v for f, _, v in rows}
    c = p.c
    top = p.h - 80
    c.setFillColorRGB(0.92, 0.95, 0.99)
    c.rect(40, top - 210, p.w - 80, 230, stroke=1, fill=1)
    c.setFillColorRGB(0, 0, 0)
    p.text(55, top, d.get("bank_name", "BANK"), 15, bold=True, field="bank_name" if d.get("bank_name") else None)
    if d.get("ifsc"):
        p.text(55, top - 16, "IFSC: ", 9)
        p.text(55 + c.stringWidth("IFSC: ", "Helvetica", 9), top - 16, d["ifsc"], 9, field="ifsc")
    p.text(p.w - 200, top, "Date: ", 10)
    if d.get("issue_date"):
        p.text(p.w - 200 + c.stringWidth("Date: ", "Helvetica", 10), top, d["issue_date"], 10, field="issue_date")
    p.text(55, top - 50, "Pay ____________________________________________ or Bearer", 11)
    p.text(55, top - 75, "Rupees __________________________________________", 11)
    p.text(55, top - 110, "A/c No. ", 11)
    if d.get("account_number"):
        p.text(55 + c.stringWidth("A/c No. ", "Helvetica", 11), top - 110, d["account_number"], 11, field="account_number")
    if d.get("account_holder"):
        p.text(p.w - 300, top - 160, "For ", 10)
        p.text(p.w - 300 + c.stringWidth("For ", "Helvetica", 10), top - 160, d["account_holder"], 10, field="account_holder")
        p.text(p.w - 300, top - 190, "Authorised Signatory", 8, italic=True)
    c.saveState()
    c.setFillColorRGB(0.8, 0.1, 0.1)
    c.translate(p.w / 2, top - 100)
    c.rotate(18)
    c.setFont("Helvetica-Bold", 48)
    c.drawCentredString(0, 0, "CANCELLED")
    c.restoreState()


def render_pdf(path: Path, doc: dict, rng: random.Random, layout: str, lang: str, country: str) -> dict:
    rows = _rows(doc, rng, lang, country)
    p = _Pdf(path)
    kind = doc["kind"]
    if kind == "cancelled_cheque":
        _cheque(p, rows, rng)
        p.save()
        return p.pos
    if layout == "multipage":
        y = _header(p, doc, rng, lang)
        intro = ["Page 1 of 2", "", "This document is issued for information purposes. Please retain it for your records.",
                 "Details are set out on the following page."] if lang == "en" else \
                ["Seite 1 von 2", "", "Dieses Dokument dient zur Information. Bitte bewahren Sie es auf.",
                 "Die Angaben finden Sie auf der folgenden Seite."]
        for ln in intro:
            p.text(50, y, ln, 10)
            y -= 16
        p.new_page()
        y = p.h - 80
        p.text(50, y, "Page 2 of 2" if lang == "en" else "Seite 2 von 2", 9)
        _kv(p, rows, y - 30)
        p.save()
        return p.pos
    y = _header(p, doc, rng, lang)
    if kind in OTHER_KINDS:
        y = _kv(p, rows, y)
        extra = [("Consumer Number", f"88{rng.randint(1000, 9999)}045"), ("Amount Due", f"{rng.randint(900, 40000):,}.00")] \
            if kind == "utility_bill" else [("Invoice No", f"INV-{rng.randint(1000, 9999)}"), ("Total", f"{rng.randint(5000, 90000):,}.00")]
        for lab, v in extra:
            p.text(50, y, f"{lab}: {v}", 11)
            y -= 22
        p.save()
        return p.pos
    if kind == "bank_statement":
        y = _kv(p, rows, y, gap=18, size=10)
        _statement_tail(p, y - 10, rng, lang)
        p.save()
        return p.pos
    if kind == "bank_letter" and layout in ("prose", "stamped"):
        for ln in PROSE[lang]:
            p.text(50, y, ln, 11)
            y -= 18
        y -= 8
    if layout == "table":
        y = _table(p, rows, y)
    elif layout == "twocol":
        y = _twocol(p, rows, y)
    elif layout == "prose" and kind == "bank_letter":
        y = _prose_bank(p, rows, y, lang)
    else:
        y = _kv(p, rows, y)
    if layout == "stamped" or (kind == "bank_letter" and rng.random() < 0.4):
        # the stamp lands on top of the last lines of text, as real stamps do
        _stamp(p, rng.randint(300, 420), y + rng.randint(20, 60), doc["printed"].get("bank_name"), rng)
        p.text(50, y - 40, "Authorised signatory" if lang == "en" else "Unterschrift", 9, italic=True)
    p.save()
    return p.pos


def apply_edit(src: Path, dst: Path, pos: tuple, new_value: str) -> None:
    """A fraudster edits a genuine PDF: white box over the value, new value on top, saved as an
    incremental update. The original value stays in the file underneath."""
    x, y, size, page = pos
    buf = io.BytesIO()
    o = canvas.Canvas(buf, pagesize=A4)
    o.setFillColorRGB(1, 1, 1)
    o.rect(x - 2, y - 3, max(150, size * 0.62 * (len(new_value) + 4)), size + 5, fill=1, stroke=0)
    o.setFillColorRGB(0, 0, 0)
    o.setFont("Helvetica", size)
    o.drawString(x, y, new_value)
    o.save()
    buf.seek(0)
    w = PdfWriter(str(src), incremental=True)
    w.pages[page].merge_page(PdfReader(buf).pages[0])
    w.write(str(dst))


# ================================================================ photo ====
def _font(size: int, bold=False):
    for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        for base in ("C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/", ""):
            try:
                return ImageFont.truetype(base + name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render_photo(path: Path, doc: dict, rng: random.Random, lang: str, country: str, blurry: bool) -> list[str]:
    """A phone photo of a printed page."""
    rows = _rows(doc, rng, lang, country)
    issuer, title = rng.choice(TITLES.get((doc["kind"], lang)) or TITLES[(doc["kind"], "en")])
    W, H = 1240, 1754
    page = Image.new("RGB", (W, H), (250, 249, 245))
    d = ImageDraw.Draw(page)
    y = 110
    lines = []
    top = issuer or (doc["printed"].get("bank_name") if doc["kind"] in BANK_KINDS else None)
    if top:
        d.text((100, y), top, fill=(20, 20, 20), font=_font(48, True))
        lines.append(top)
        y += 90
        d.line((100, y, W - 100, y), fill=(60, 60, 60), width=3)
        y += 40
    d.text((100, y), title, fill=(10, 10, 10), font=_font(40, True))
    lines.append(title)
    y += 100
    table = rng.random() < 0.4
    for f, lab, v in rows:
        if table:
            d.rectangle((100, y - 12, W - 100, y + 52), outline=(90, 90, 90), width=2)
            d.line((470, y - 12, 470, y + 52), fill=(90, 90, 90), width=2)
            d.text((115, y), lab, fill=(30, 30, 30), font=_font(30, True))
            d.text((485, y), v, fill=(10, 10, 10), font=_font(32))
            y += 64
        else:
            d.text((100, y), f"{lab}: {v}", fill=(10, 10, 10), font=_font(34))
            y += 62
        lines.append(f"{lab}: {v}")
    d.text((100, H - 90), FOOT, fill=(90, 90, 90), font=_font(22))
    # --- camera: perspective + rotation onto a desk, uneven light, noise, JPEG ---
    desk = Image.new("RGB", (W + 360, H + 360), tuple(rng.randint(70, 140) for _ in range(3)))
    dx = rng.randint(-40, 40)
    coeffs = _perspective((W, H), [(0 + dx, 0), (W, rng.randint(0, 40)), (W - rng.randint(0, 50), H), (rng.randint(0, 50), H - rng.randint(0, 30))])
    warped = page.transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(0, 0, 0))
    mask = Image.new("L", (W, H), 255).transform((W, H), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=0)
    desk.paste(warped, (180, 180), mask)
    img = desk.rotate(rng.uniform(-4, 4), resample=Image.BICUBIC, fillcolor=(80, 80, 80))
    shade = Image.new("L", img.size)
    sd = ImageDraw.Draw(shade)
    cx, cy = rng.randint(0, img.width), rng.randint(0, img.height)
    for r in range(0, max(img.size), 40):
        sd.ellipse((cx - r, cy - r, cx + r, cy + r), outline=int(min(90, r / max(img.size) * 140)), width=40)
    img = Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, shade.filter(ImageFilter.GaussianBlur(60)).point(lambda v: v // 2))
    noise = Image.effect_noise(img.size, 18).convert("RGB")
    img = Image.blend(img, noise, 0.06)
    if blurry:
        img = img.filter(ImageFilter.GaussianBlur(9)).resize((img.width // 5, img.height // 5)).resize(img.size)
    img = img.resize((img.width * 2 // 3, img.height * 2 // 3))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=62)
    Path(str(path) + ".mock.json").write_text(json.dumps({"legible": not blurry, "text": "\n".join(lines) if not blurry else ""}))
    return lines


def _perspective(size, quad):
    """Coefficients that map the output rectangle onto `quad` (PIL's PERSPECTIVE wants the inverse)."""
    W, H = size
    src = [(0, 0), (W, 0), (W, H), (0, H)]
    A = []
    for (x, y), (u, v) in zip(src, quad):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, v])
    n = 8                                                   # Gauss-Jordan with partial pivoting
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(A[r][i]))
        A[i], A[piv] = A[piv], A[i]
        for r in range(n):
            if r != i:
                f = A[r][i] / A[i][i]
                A[r] = [a - f * b for a, b in zip(A[r], A[i])]
    return [A[i][n] / A[i][i] for i in range(n)]


# =============================================================== public ====
def render(folder: Path, slot: str, doc: dict, rng: random.Random, today: date, country: str) -> tuple[Path, dict]:
    """Render one document. Returns (path, truth). truth = what a perfect reader would report."""
    lang = doc.get("language", "en") if (country == "DE" or doc.get("language") == "en") else "en"
    issued = today - timedelta(days=int(doc.get("issued_days_ago", 30)))
    printed = {k: v for k, v in (doc.get("printed") or {}).items() if v}
    if doc.get("issued_days_ago") is not None and not doc.get("undated"):
        printed["issue_date"] = fmt_date(issued, country, lang, rng)
    d = {**doc, "printed": printed}
    kind = doc["kind"]
    if doc.get("format") == "photo":
        path = folder / f"{slot}.jpg"
        render_photo(path, d, rng, lang, country, doc.get("quality") == "blurry")
        layout = "photo"
    else:
        path = folder / f"{slot}.pdf"
        edited = doc.get("edited")
        layout = doc.get("layout") or rng.choice(PDF_LAYOUTS)
        if edited and layout in ("prose", "twocol", "multipage"):
            layout = rng.choice(["kv", "table", "stamped"])
        if edited:
            f = edited["field"]
            orig = {**d, "printed": {**printed, f: edited["original_value"]}}
            tmp = folder / f"_orig_{slot}.pdf"
            pos = render_pdf(tmp, orig, rng, layout, lang, country)
            if f in pos:
                apply_edit(tmp, path, pos[f], edited["visible_value"])
                tmp.unlink()
                printed[f] = edited["visible_value"]
            else:                                           # field not on the page: nothing to edit
                tmp.replace(path)
        else:
            render_pdf(path, d, rng, layout, lang, country)
    truth = {"doc_type": doc_type_of(kind), "kind": kind, "layout": layout, "language": lang,
             "legible": doc.get("quality") != "blurry", "printed": printed, "issued_iso": issued.isoformat() if "issue_date" in printed else None}
    return path, truth
