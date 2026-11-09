"""Render synthetic vendor documents with varied layouts.

Layouts vary on purpose (plain certificate, bank letterhead with prose, cancelled cheque,
phone scan) so the pipeline is tested against more than one 'shape' of document.
Labels stay within the vocabulary a document reader is expected to understand."""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

FOOT = "Synthetic document generated for testing. Not a real record."
TAX_TITLE = {
    "IN": ["GOODS AND SERVICES TAX - REGISTRATION CERTIFICATE", "Form GST REG-06 - Registration Certificate"],
    "DE": ["VAT REGISTRATION CERTIFICATE (UStG)", "Bestaetigung - Certificate of Registration for VAT"],
    "GB": ["VAT REGISTRATION CERTIFICATE", "HM Revenue & Customs - Certificate of Registration"],
    "US": ["FORM W-9 - REQUEST FOR TAXPAYER IDENTIFICATION NUMBER", "Form W-9 (Rev. 2024) Request for Taxpayer Identification Number"],
}
TAX_LABEL = {"IN": "GSTIN", "DE": "VAT ID", "GB": "VAT Number", "US": "EIN"}
NAME_LABEL = {"IN": ["Legal Name", "Legal Name"], "DE": ["Registered Name", "Legal Name"],
              "GB": ["Registered Name", "Business Name"], "US": ["Name of Taxpayer", "Business Name"]}


def _pdf(path: Path, title: str, lines: list[str], header: list[str] | None = None, prose: list[str] | None = None,
         sign: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 60
    if header:
        c.setFont("Helvetica-Bold", 18)
        c.drawString(50, y, header[0])
        c.setFont("Helvetica", 9)
        for i, ln in enumerate(header[1:]):
            c.drawString(50, y - 16 - 12 * i, ln)
        y -= 30 + 12 * len(header[1:])
        c.line(50, y + 8, w - 50, y + 8)
        y -= 14
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, title)
    y -= 30
    c.setFont("Helvetica", 11)
    for ln in prose or []:
        c.drawString(50, y, ln)
        y -= 18
    if prose:
        y -= 8
    for ln in lines:
        c.drawString(50, y, ln)
        y -= 22
    if sign:
        c.setFont("Helvetica-Oblique", 10)
        c.drawString(50, y - 30, sign)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, 50, FOOT)
    c.save()


def tax_cert(path: Path, rng, ident: dict, *, legal_name=None, tax_id=None, address=None, issue=None, extra=None) -> None:
    cc = ident["country"]
    v = rng.randrange(2)
    lines = [f"{NAME_LABEL[cc][v]}: {legal_name or ident['legal_name']}"]
    if cc == "IN" and ident.get("trade_name"):
        lines.append(f"Trade Name: {ident['trade_name']}")
    lines += [f"{TAX_LABEL[cc]}: {tax_id or ident['tax_id']}",
              f"Registered Address: {address or ident['full_address']}",
              f"Date of Issue: {issue}"]
    if cc == "IN":
        lines.insert(rng.randint(1, len(lines) - 1), "Type of Registration: Regular")
    lines += extra or []
    _pdf(path, TAX_TITLE[cc][v], lines, header=["Tax Registration Authority"] if v else None)


def bank_lines(ident: dict, acct: dict, holder: str, bank: str, issue: str) -> list[str]:
    out = [f"Bank: {bank}", f"Account Holder: {holder}"]
    for k, label in (("account_number", "Account Number"), ("iban", "IBAN"), ("ifsc", "IFSC"),
                     ("sort_code", "Sort Code"), ("routing_number", "Routing Number"), ("swift", "SWIFT")):
        if acct.get(k):
            out.append(f"{label}: {acct[k]}")
    out.append(f"Date: {issue}")
    return out


def bank_proof(path: Path, rng, ident: dict, *, holder=None, acct=None, bank=None, issue=None, layout=None) -> str:
    holder = holder or ident["account_holder"]
    bank = bank or ident["bank_name"]
    acct = acct or {k: ident.get(k, "") for k in ("account_number", "iban", "ifsc", "sort_code", "routing_number")}
    layout = layout or rng.choice(["plain", "letterhead", "letterhead"] + (["cheque"] if ident["country"] == "IN" else []))
    lines = bank_lines(ident, acct, holder, bank, issue)
    if layout == "plain":
        _pdf(path, "BANK ACCOUNT CONFIRMATION LETTER", lines)
    elif layout == "letterhead":
        _pdf(path, "Account confirmation letter", lines, header=[bank, f"{ident['city']} branch", "Customer Service Centre"],
             prose=["To whom it may concern,", "We confirm that the account described below is held with this bank",
                    "and is active as of the date of this letter."], sign="Authorised signatory, Branch Operations")
    else:  # cancelled cheque
        _pdf(path, "CANCELLED CHEQUE", lines + ["CANCELLED"], header=[bank])
    return layout


def scan(path: Path, lines: list[str], legible: bool = True, title: str = "BANK ACCOUNT CONFIRMATION LETTER") -> None:
    """A phone photo of a document. The `.mock.json` sidecar stands in for OCR output
    in offline mode; with a real model the image itself is read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [title, ""] + list(lines)
    img = Image.new("RGB", (900, 120 + 40 * len(lines)), (246, 244, 238))
    dr = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    y = 40
    for ln in lines:
        dr.text((40, y), ln, fill="black", font=font)
        y += 38
    img = img.rotate(1.2, fillcolor=(230, 230, 225))
    if not legible:
        img = img.filter(ImageFilter.GaussianBlur(6)).resize((300, 160)).resize(img.size)
    img.save(path)
    Path(str(path) + ".mock.json").write_text(json.dumps({"legible": legible, "text": "\n".join(lines) if legible else ""}))


def utility_bill(path: Path, ident: dict, issue: str) -> None:
    _pdf(path, "ELECTRICITY BILL", [f"Consumer Name: {ident['legal_name']}", f"Consumer Number: 88{ident['postal_code'][:3]}0045",
                                    f"Supply Address: {ident['full_address']}", f"Bill Date: {issue}", "Amount Due: 18,420.00"],
         header=["City Power Distribution Co."])


def invoice(path: Path, ident: dict, issue: str) -> None:
    _pdf(path, "TAX INVOICE", [f"Supplier: {ident['legal_name']}", "Invoice No: INV-2291", f"Invoice Date: {issue}",
                               "Description: Consulting services", "Total: 42,000.00"])


def tamper_overlay(src: Path, dst: Path, line_y_index: int, new_line: str, title_offset: int) -> None:
    """Simulate a fraudster editing a genuine PDF: a white box over one line and new text
    on top, saved as an incremental update. The original value stays in the text layer."""
    buf = io.BytesIO()
    o = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    y = h - title_offset - 22 * line_y_index
    o.setFillColorRGB(1, 1, 1)
    o.rect(46, y - 4, 360, 17, fill=1, stroke=0)
    o.setFillColorRGB(0, 0, 0)
    o.setFont("Helvetica", 11)
    o.drawString(50, y, new_line)
    o.save()
    buf.seek(0)
    w = PdfWriter(str(src), incremental=True)
    w.pages[0].merge_page(PdfReader(buf).pages[0])
    w.write(str(dst))
