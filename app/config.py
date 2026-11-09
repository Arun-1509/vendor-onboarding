"""Central configuration. Everything tunable lives here (or in env vars)
so that the rules a procurement lead cares about are easy to find and change."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR_DIR = Path(os.getenv("VAR_DIR", ROOT / "var"))
VAR_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("DB_PATH", VAR_DIR / "vendors.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", VAR_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = ROOT / "data"
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")   # used in links inside emails

# ---------------------------------------------------------------- LLM ----
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")          # free key: aistudio.google.com
# Which real model provider to use: anthropic | gemini | auto (anthropic if its key exists, else gemini)
_prov = os.getenv("LLM_PROVIDER", "auto").lower()
PROVIDER = _prov if _prov in ("anthropic", "gemini") else ("anthropic" if ANTHROPIC_API_KEY else "gemini" if GEMINI_API_KEY else "none")
_HAS_KEY = bool(ANTHROPIC_API_KEY if PROVIDER == "anthropic" else GEMINI_API_KEY if PROVIDER == "gemini" else "")
_mock = os.getenv("MOCK_LLM", "auto").lower()
# auto -> use a real API only when the chosen provider has a key
MOCK_LLM = (not _HAS_KEY) if _mock == "auto" else _mock in ("1", "true", "yes")
PROVIDER_LABEL = "offline mock" if MOCK_LLM else {"anthropic": "Claude", "gemini": "Gemini"}.get(PROVIDER, PROVIDER)

# Model per job. Extraction is on the live-demo critical path, so it uses the
# fast model; the rare ambiguous judgment uses the strongest one.
MODEL_EXTRACT = os.getenv("MODEL_EXTRACT", "claude-sonnet-5-5")
MODEL_JUDGE = os.getenv("MODEL_JUDGE", "claude-fable-5-1")
MODEL_WRITE = os.getenv("MODEL_WRITE", "claude-sonnet-5-5")
MODEL_FALLBACK = os.getenv("MODEL_FALLBACK", "claude-sonnet-5-5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")           # reads documents, writes emails
GEMINI_JUDGE_MODEL = os.getenv("GEMINI_JUDGE_MODEL", "gemini-flash-latest")
GEMINI_FALLBACK = os.getenv("GEMINI_FALLBACK", "gemini-flash-lite-latest")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "60"))

# --------------------------------------------------------------- rules ----
AUTO_APPROVE_MAX_RISK = 20        # risk score must be BELOW this to auto-approve
BANK_PROOF_MAX_AGE_DAYS = 90      # older bank letters are rejected as stale
NAME_EQUIVALENT = 0.93            # core-name similarity treated as the same name
NAME_GREY_LOW = 0.72              # below this the names are simply different
LOOKALIKE_THRESHOLD = 0.88        # new vendor vs existing vendor, different tax id
SANCTIONS_REJECT = 0.93           # at/above -> automatic rejection
SANCTIONS_REVIEW = 0.84           # at/above -> human review
LLM_MIN_CONFIDENCE = 0.80         # below this a name judgment counts as "unsure"
EXTRACTION_MIN_CONFIDENCE = 0.70  # below this a field is treated as unreadable
VENDOR_RESPONSE_DAYS = 7          # how long a vendor has to respond
MAX_REMINDERS = 2                 # reminders before the request is closed as stale

FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "yahoo.in", "outlook.com", "hotmail.com",
    "proton.me", "protonmail.com", "icloud.com", "rediffmail.com", "aol.com",
}

COUNTRIES = {
    "IN": {"name": "India", "currency": "INR"},
    "DE": {"name": "Germany", "currency": "EUR"},
    "GB": {"name": "United Kingdom", "currency": "GBP"},
    "US": {"name": "United States", "currency": "USD"},
}

# --------------------------------------------------------- lifecycle / ops ----
REMINDER_GAP_DAYS = 3                 # days between a reminder and the next deadline
ESTIMATED_MANUAL_MINUTES = 20         # ASSUMPTION used only for the dashboard's "time saved" figure
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev")
DEMO_INBOX = os.getenv("DEMO_INBOX", "")   # all demo emails are sent HERE, never to the vendor's address
ADMIN_KEY = os.getenv("ADMIN_KEY", "")     # if set, required (X-Admin-Key) for reset
