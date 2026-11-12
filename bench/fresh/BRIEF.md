You are a senior procurement / accounts-payable compliance analyst. Write a TEST SET of 40 vendor-onboarding
submissions, each with the outcome YOU think a careful company should reach. Use business judgment only.

## The process being tested
A company onboards new suppliers (vendors) before paying them. A vendor submits a form plus two documents:
a tax registration certificate and proof of bank account. Each submission must end in exactly ONE outcome:
- "approved": complete, consistent, credible. Safe to pay.
- "awaiting_vendor": an honest gap the VENDOR can fix (missing/blurry/old/wrong document, blank field, typo
  in a number, wrong dropdown). The vendor is emailed what to fix.
- "awaiting_reviewer": a fraud or compliance concern that a HUMAN on our team must look at (do NOT ask
  the vendor, since that would tip off a fraudster). If a case has both a fixable gap and a fraud concern, the fraud
  concern wins.
- "rejected": the vendor or the payee matches the sanctions list.

Countries supported: India (IN), Germany (DE), United Kingdom (GB), United States (US).
Bank proof should be a bank letter or cancelled cheque issued in the last 90 days.

Our EXISTING approved vendors (the vendor master):
- Globex Logistics Solutions Pvt Ltd | IN | GSTIN 27AABCG4411K1ZR | accounts@globexlogisticssolut.in | HDFC Bank a/c 50100234567890
- Northwind Components GmbH | DE | VAT DE136695976 | buchhaltung@northwindcomponentsg.de | Deutsche Bank IBAN DE89370400440532013000
- Brightpath Consulting Ltd | GB | VAT GB123456782 | finance@brightpathconsulting.co.uk | Barclays a/c 55779911
- Summit Office Supplies LLC | US | EIN 12-3456789 | ap@summitofficesupplies.com | JPMorgan Chase a/c 123456789012
- Acme Technologies Private Limited | IN | GSTIN 29AABCA7788M1ZV | accounts@acmetechnologiespriv.in | ICICI Bank a/c 60200011223344

Sanctions list: "Meridian Crest Shipping Co" (alias "MC Shipping"), "Blackthorn Offshore Trading Ltd",
"Orion Vale Metals LLC" (alias "Orion Vale Metallurgy").

## Mix (40 cases)
- 12 approved, including harmless variations a good system must NOT flag (legal-suffix wording, accents,
  abbreviations, a name that merely resembles a sanctioned one, a bank letter 80+ days old, etc.)
- 12 awaiting_vendor
- 12 awaiting_reviewer: realistic fraud/compliance patterns (think broadly; be creative, don't just list the obvious)
- 4 rejected
Cover all four countries. Make roughly a third of them genuinely tricky.

## IDs and account numbers: use these placeholders (I fill in real, checksum-valid numbers)
- {{TAX_ID}} = this vendor's own valid tax ID. {{TAX_ID_TYPO}} = same but one digit mistyped.
  {{TAX_ID_OTHER}} = a valid tax ID belonging to some other company.
- {{ACCOUNT}} = this vendor's own valid account number (or IBAN for DE). {{ACCOUNT_TYPO}} = one digit mistyped.
  {{ACCOUNT_OTHER}} = a valid account belonging to someone else.
- {{BANK_CODE}} = valid IFSC (IN) / sort code (GB) / routing number (US); leave blank for DE.
- To reuse an existing vendor's real number, write it literally from the list above.
Placeholders mean the same value everywhere they appear within one case.

## Output: ONLY a JSON object, no commentary, in exactly this shape
{"cases": [
 {"id": "H01",
  "title": "short title",
  "story": "1-2 sentences describing what happened",
  "form": {"legal_name": "", "trade_name": "", "entity_type": "pvt_ltd|ltd|llp|gmbh|ug|ag|llc|inc|corp|sole_prop|partnership",
           "country": "IN|DE|GB|US", "address": "", "city": "", "postal_code": "", "tax_id": "",
           "contact_name": "", "contact_email": "", "bank_name": "", "account_holder": "", "currency": "",
           "account_number": "", "ifsc": "", "iban": "", "sort_code": "", "routing_number": "", "swift": ""},
  "documents": {
    "tax_cert": {"kind": "gst_certificate|vat_certificate|ust_certificate|w9|utility_bill|other",
                 "printed": {"legal_name": "", "tax_id": "", "address": ""},
                 "issued_days_ago": 300, "language": "en|de", "format": "pdf|photo",
                 "quality": "clear|blurry", "edited": null},
    "bank_proof": {"kind": "bank_letter|cancelled_cheque|bank_statement|invoice|other",
                   "printed": {"bank_name": "", "account_holder": "", "account_number": "", "iban": "", "ifsc": "", "sort_code": "", "routing_number": ""},
                   "issued_days_ago": 20, "language": "en|de", "format": "pdf|photo",
                   "quality": "clear|blurry", "edited": null}
  },
  "expected": "approved|awaiting_vendor|awaiting_reviewer|rejected",
  "why": "one sentence of business reasoning",
  "difficulty": "easy|medium|hard"}
]}
Notes: a missing document = null. "edited" = null, or {"field": "account_number", "visible_value": "...",
"original_value": "..."} when someone pasted a new value over the original in the PDF.
