"""Personal-data masking, applied at ingestion — before anything reaches the
clean tables, let alone the public dashboard.

What is masked in descriptions: email addresses, Indonesian phone numbers
(mobile 08…/+62 8…/62 8… and landlines with an area code), WhatsApp /
Telegram / LINE contact links, and the name that follows a contact cue
("hubungi Budi", "a.n. Sari Dewi", "CP: Rina").

Company names are not masked in text but are replaced by a keyed hash
(company_hmac) everywhere they are exported. HMAC rather than a plain hash:
company names are guessable, so sha256("PT ABC") could be reversed by
hashing a list of company names; without HMAC_SECRET that's impossible.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re

import core.env  # noqa: F401  (import for side effect: loads .env)
from core.text import normalise_company

MASK = "[disamarkan]"

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Mobile: 08xx / +62 8xx / 62 8xx, 9-13 digits total, with optional spaces,
# dots or dashes between groups. Landline: optional parens around a 0-led
# 2-4 digit area code, then 6-8 digits. Both require a leading 0 or 62, so
# salary figures like "5.000.000" or "Rp 4.500.000" never match.
PHONE = re.compile(
    r"(?<![\w.])(?:"
    r"(?:\+?62|0)[\s.-]?8\d{1,3}(?:[\s.-]?\d{2,4}){2,3}"
    r"|\(?0\d{1,3}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}"
    r")(?![\w])"
)

CONTACT_LINK = re.compile(
    r"(?:https?://)?(?:wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com|t\.me|line\.me)/\S+",
    re.IGNORECASE,
)

# A contact cue followed by 1-3 capitalised words = a person's name.
CONTACT_NAME = re.compile(
    r"(?P<cue>\b(?:hubungi|menghubungi|a\.n\.?|atas nama|cp|contact person|kontak|narahubung)\s*[:.]?\s*"
    r"(?:(?:bapak|ibu|bpk|bu|pak|mr|mrs|ms|sdr|sdri)\.?\s+)?)"
    r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
    re.IGNORECASE,
)


def mask_text(text: str | None) -> str:
    """Return `text` with every contact detail replaced by MASK."""
    if not text:
        return ""
    text = CONTACT_LINK.sub(MASK, text)
    text = EMAIL.sub(MASK, text)
    text = PHONE.sub(MASK, text)

    def _name(m: re.Match) -> str:
        name = m.group("name")
        # The IGNORECASE flag lets cues match "Hubungi"/"HUBUNGI", but a name
        # must really start uppercase — "hubungi kami" is not a person.
        if not name[0].isupper():
            return m.group(0)
        return m.group("cue") + MASK

    return CONTACT_NAME.sub(_name, text)


def find_pii(text: str | None) -> list[str]:
    """Kinds of contact detail still present in `text` (empty list = clean).
    Used by the quality gate and the deploy check as a last line of defence."""
    if not text:
        return []
    kinds = []
    if EMAIL.search(text):
        kinds.append("email")
    if PHONE.search(text):
        kinds.append("phone")
    if CONTACT_LINK.search(text):
        kinds.append("contact_link")
    return kinds


def _secret() -> bytes:
    secret = os.environ.get("HMAC_SECRET")
    if not secret:
        raise RuntimeError("HMAC_SECRET is not set (see .env.example); company names can't be pseudonymised")
    return secret.encode("utf-8")


def company_hmac(name: str | None) -> str | None:
    """Stable pseudonym for a company: first 12 hex chars of
    HMAC-SHA256(HMAC_SECRET, normalised name). The dashboard shows the first
    6 ('Perusahaan #a3f9c2'). None for an empty name."""
    key = normalise_company(name)
    if not key:
        return None
    return hmac.new(_secret(), key.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
