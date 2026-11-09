"""Company-name comparison.

Three layers, cheapest first:
  1. normalise (case, accents, punctuation, '&' -> 'and')
  2. strip *legal-form suffixes* ('Pvt Ltd' == 'Private Limited') and compare the core
  3. only the grey zone is handed to the LLM (see pipeline.name_judgment)
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from .. import config

# legal-form words, mapped to a canonical class
_FORM_WORDS = {
    "pvt": "private", "private": "private", "p": "private",
    "ltd": "limited", "limited": "limited",
    "llp": "llp", "gmbh": "gmbh", "mbh": "gmbh", "ag": "ag", "ug": "ug", "kg": "kg",
    "llc": "llc", "inc": "inc", "incorporated": "inc", "corp": "corp", "corporation": "corp",
    "co": "co", "company": "co", "plc": "plc", "sarl": "sarl", "bv": "bv", "nv": "nv",
    "pty": "pty", "lp": "lp",
}
# groups of words that mean the same legal form
_FORM_CLASS = [
    ({"private", "limited"}, "private_limited"),
    ({"limited"}, "limited"),
    ({"llp"}, "llp"), ({"gmbh"}, "gmbh"), ({"ag"}, "ag"), ({"ug"}, "ug"),
    ({"llc"}, "llc"), ({"inc"}, "inc"), ({"corp"}, "inc"), ({"plc"}, "plc"),
    ({"co"}, "co"), ({"lp"}, "lp"),
]
_STOP = {"and", "the", "of"}


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_name(name: str) -> tuple[list[str], str]:
    """-> (core tokens, legal-form class or '')."""
    toks = normalize(name).split()
    core, forms = [], []
    # trailing legal-form words only: 'Limited Brands Pvt Ltd' keeps 'limited' in the core
    i = len(toks)
    while i > 0 and toks[i - 1] in _FORM_WORDS:
        forms.insert(0, _FORM_WORDS[toks[i - 1]])
        i -= 1
    core = [t for t in toks[:i] if t not in _STOP]
    fset = set(forms)
    cls = ""
    for words, label in _FORM_CLASS:
        if words <= fset:
            cls = label
            break
    return core, cls


def core_name(name: str) -> str:
    return " ".join(split_name(name)[0])


def core_similarity(a: str, b: str) -> float:
    ca, cb = core_name(a), core_name(b)
    if not ca or not cb:
        return 0.0
    return fuzz.token_sort_ratio(ca, cb) / 100.0


def compare_names(a: str, b: str) -> dict:
    """Deterministic verdict: exact | equivalent | form_mismatch | grey | different."""
    na, nb = normalize(a), normalize(b)
    (ca, fa), (cb, fb) = split_name(a), split_name(b)
    sim = core_similarity(a, b)
    out = {"a": a, "b": b, "core_a": " ".join(ca), "core_b": " ".join(cb),
           "form_a": fa, "form_b": fb, "core_similarity": round(sim, 3)}
    if not na or not nb:
        return {**out, "verdict": "different", "why": "one name is empty"}
    if na == nb:
        return {**out, "verdict": "exact", "why": "identical after normalisation"}
    if sorted(ca) == sorted(cb):
        if fa == fb or not fa or not fb:
            return {**out, "verdict": "equivalent",
                    "why": "same company name; only the legal-form wording or punctuation differs"}
        return {**out, "verdict": "form_mismatch",
                "why": f"same core name but different legal forms ({fa} vs {fb})"}
    if sim >= config.NAME_EQUIVALENT and (fa == fb or not fa or not fb):
        return {**out, "verdict": "equivalent", "why": f"core names are {sim:.0%} similar (minor spelling difference)"}
    if sim >= config.NAME_GREY_LOW:
        return {**out, "verdict": "grey", "why": f"core names are {sim:.0%} similar, which needs a closer look"}
    return {**out, "verdict": "different", "why": f"core names are only {sim:.0%} similar"}


def is_abbreviation_of(short: str, long: str) -> bool:
    """Every token of `short` is a prefix of a distinct token of `long`
    ('delta fab engineering' ~ 'delta fabrication engineering')."""
    s, l = split_name(short)[0], split_name(long)[0]
    # An abbreviation shortens WORDS, it never drops them: 'Kestrel Analytics' is NOT an
    # abbreviation of 'Kestrel Analytics Technologies' (that is a different company name).
    if not s or len(s) != len(l):
        return False
    pool = list(l)
    for t in s:
        hit = next((x for x in pool if x.startswith(t)), None)
        if hit is None:
            return False
        pool.remove(hit)
    return True
