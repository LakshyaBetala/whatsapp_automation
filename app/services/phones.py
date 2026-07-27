"""Indian phone-number handling in ONE place.

Every part of ASVA that matches an inbound WhatsApp reply to a party, or stores
a contact read from Tally, goes through here - so "is this the same person?" is
decided consistently and is fully testable.

Indian mobile facts we rely on:
- 10 significant digits, first digit 6/7/8/9.
- Country code 91. In the wild a number arrives as any of: 10-digit,
  0-prefixed (STD, 11 digits), 91-prefixed (12), 0091-prefixed (14), with
  spaces / dashes / brackets / '+' , or as a WhatsApp JID like
  '919812345678@s.whatsapp.net'. Landlines and junk must be rejected.
"""
from __future__ import annotations

import re

_NON_DIGIT = re.compile(r"\D+")


def _digits(raw) -> str:
    """Just the digits. A JID's '@s.whatsapp.net' contributes none, so it drops
    away naturally."""
    if raw is None:
        return ""
    return _NON_DIGIT.sub("", str(raw))


def last10(raw) -> str:
    """The last 10 digits - a format-agnostic match key. '' if fewer than 10."""
    d = _digits(raw)
    return d[-10:] if len(d) >= 10 else ""


def core10(raw) -> str | None:
    """The 10 significant mobile digits if `raw` looks like an Indian mobile,
    else None. Peels a leading 0 (STD), 91, or 0091 country code, then checks
    the 10-digit shape (first digit 6/7/8/9). Rejects landlines and junk."""
    d = _digits(raw)
    if len(d) == 14 and d.startswith("0091"):
        d = d[4:]
    elif len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10 and d[0] in "6789":
        return d
    return None


def normalize(raw) -> str | None:
    """Canonical '91XXXXXXXXXX' for a valid Indian mobile, else None."""
    c = core10(raw)
    return ("91" + c) if c else None


def same_number(a, b) -> bool:
    """Do two numbers refer to the same person? True when both have a valid
    10-digit mobile core and the cores match; otherwise a last-10-digit
    fallback so odd-but-consistent formats still match. Empty/None never match."""
    ca, cb = core10(a), core10(b)
    if ca is not None and cb is not None:
        return ca == cb
    # Fallback for an oddly-formatted-but-real number: match on the last 10
    # digits, but only when those 10 look like a mobile (start 6-9). That keeps
    # junk (all-zeros, over-long strings) from ever matching itself or anything.
    la, lb = last10(a), last10(b)
    return bool(la) and la == lb and la[0] in "6789"
