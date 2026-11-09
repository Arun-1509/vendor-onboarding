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
    "pty": "pty", "lp": "lp", "haftungsbeschrankt": "ug",
}
# 'M/s Sharma & Sons' (Indian honorific for firms) and 'Acme Inc DBA Acme Direct' (US 'doing business as')
_HONORIFIC = re.compile(r"^\s*(m/s\.?|messrs\.?)\s+", re.I)
_DBA = re.compile(r"\s+(?:dba|d/b/a|d\.b\.a\.|t/a|trading as|doing business as)\s+", re.I)


def strip_decorations(name: str) -> str:
    """Remove a leading 'M/s' and anything after 'DBA' / 'trading as' (the trade-name part)."""
    s = _HONORIFIC.sub("", name or "")
    parts = _DBA.split(s, maxsplit=1)
    return parts[0] if parts[0].strip() else s
# groups of words that mean the same legal form
_FORM_CLASS = [
    ({"private", "limited"}, "private_limited"),
    ({"limited"}, "limited"),
    ({"llp"}, "llp"), ({"gmbh"}, "gmbh"), ({"ag"}, "ag"), ({"ug"}, "ug"),
    ({"llc"}, "llc"), ({"inc"}, "inc"), ({"corp"}, "inc"), ({"plc"}, "plc"),
    ({"co"}, "co"), ({"lp"}, "lp"),
]
_STOP = {"and", "the", "of"}
# Standard business abbreviations, expanded before comparing (deterministic; no AI needed).
ABBREVIATIONS = {
    "engg": "engineering", "eng": "engineering", "mfg": "manufacturing", "mfrs": "manufacturers", "intl": "international",
    "int'l": "international", "tech": "technologies", "techs": "technologies", "elec": "electricals", "inds": "industries",
    "ind": "industries", "svcs": "services", "svc": "services", "mgmt": "management", "natl": "national",
    "bros": "brothers", "assoc": "associates", "dist": "distributors", "pharma": "pharmaceuticals", "chem": "chemicals",
    "sys": "systems", "sol": "solutions", "solns": "solutions", "comm": "communications", "tex": "textiles",
}


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", strip_decorations(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_name(name: str, expand: bool = True) -> tuple[list[str], str]:
    """-> (core tokens, legal-form class or ''). `expand` applies the abbreviation dictionary."""
    toks = [ABBREVIATIONS.get(t, t) if expand else t for t in normalize(name).split()]
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
    contained = bool(extra_words(a, b))                 # 'X Plastics' vs 'X Plastics International'
    if sim >= config.NAME_GREY_LOW or contained or is_abbreviation_of(a, b) or is_abbreviation_of(b, a):
        why = (f"one name is the other plus '{' '.join(extra_words(a, b))}'" if contained and sim < config.NAME_GREY_LOW
               else "one name looks like an abbreviation of the other" if sim < config.NAME_GREY_LOW
               else f"core names are {sim:.0%} similar")
        return {**out, "verdict": "grey", "why": f"{why}, which needs a closer look"}
    return {**out, "verdict": "different", "why": f"core names are only {sim:.0%} similar"}


def is_abbreviation_of(short: str, long: str) -> bool:
    """Every token of `short` is a prefix of a distinct token of `long`
    ('delta fab engineering' ~ 'delta fabrication engineering')."""
    s, l = split_name(short, expand=False)[0], split_name(long, expand=False)[0]
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


def extra_words(a: str, b: str) -> list[str]:
    """Whole words one name has and the other lacks, when one core name CONTAINS the other
    ('Kestrel Analytics Technologies' vs 'Kestrel Analytics' -> ['technologies']). Abbreviations
    are expanded first, so 'Engg' vs 'Engineering' is not an extra word. An extra word can be a
    different registered company, so the AI is not allowed to wave it through on its own."""
    ca, cb = split_name(a)[0], split_name(b)[0]
    if not ca or not cb or len(ca) == len(cb):
        return []
    short, long_ = (ca, cb) if len(ca) < len(cb) else (cb, ca)
    rest = list(long_)
    for t in short:
        if t not in rest:
            return []
        rest.remove(t)
    return rest


# Words impostors add to a real vendor's name to make a 'new' company look plausible.
GENERIC_PADDING = {"india", "global", "group", "international", "intl", "services", "solutions", "holdings",
                   "trading", "enterprises", "worldwide", "consulting", "ventures", "industries", "co", "company",
                   "systems", "technologies", "tech", "corp", "corporation", "partners", "associates", "uk", "usa",
                   "europe", "deutschland", "new"}


def padded_variant(a: str, b: str) -> str | None:
    """'Aravali Cables Services Pvt Ltd' vs 'Aravali Cables Pvt Ltd': one core is the other plus
    only generic padding words. Returns the padding words, or None. Fuzzy ratios miss this
    because the extra word lowers the score, yet it is the most common impersonation trick."""
    ca, cb = split_name(a)[0], split_name(b)[0]
    if not ca or not cb or ca == cb:
        return None
    short, long_ = (ca, cb) if len(ca) < len(cb) else (cb, ca)
    if len(short) < 1 or len(" ".join(short)) < 4:
        return None
    rest = list(long_)
    for t in short:
        if t not in rest:
            return None
        rest.remove(t)
    return " ".join(rest) if rest and all(t in GENERIC_PADDING for t in rest) else None


def aligned_similarity(a: str, b: str) -> float:
    """Word-by-word similarity that does not depend on alphabetical order. Each word of the
    shorter name is paired with its best partner in the longer one; unpaired words count as 0.
    Used for SANCTIONS screening, where missing a misspelt listed name is the costliest error
    ('Orioo Vale yetals' vs 'Orion Vale Metals': sorted-token matching scores it low)."""
    ta, tb = split_name(a)[0], split_name(b)[0]
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    pool = list(long_)
    total, weight = 0.0, 0
    for t in short:
        best = max(pool, key=lambda x: fuzz.ratio(t, x))
        total += fuzz.ratio(t, best) / 100.0 * len(t)
        weight += len(t)
        pool.remove(best)
    weight += sum(len(x) for x in pool)          # unmatched words in the longer name
    return total / weight if weight else 0.0


_DIGIT_AS_LETTER = {"0": "o", "1": "l", "5": "s", "8": "b", "6": "g", "2": "z"}


def ocr_fix_name(name: str) -> str:
    """Undo typical scan misreads inside WORDS ('Supp1y' -> 'Supply', 'L0gistics' -> 'Logistics').
    Only touches words that mix letters and digits, so real numbers are left alone."""
    out = []
    for w in (name or "").split(" "):
        if any(c.isalpha() for c in w) and any(c.isdigit() for c in w):
            w = "".join(_DIGIT_AS_LETTER.get(c, c) for c in w)
        out.append(w)
    return " ".join(out)
