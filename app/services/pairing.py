"""Pairing codes - passwordless onboarding for the thin client.

The operator mints a short, single-use code (admin-gated, in the Command
Center) bound to a business. The shop's installer asks the owner for that code
and redeems it via ``POST /license/pair``, receiving the business's secret
agent_token - so no token is ever typed by hand or shipped inside the public
installer. A code can bind a fresh install to a NEW business or RE-PAIR one onto
an EXISTING business_id (the pilot owner moving off the old standalone keeps
every DB-stored reminder untouched, because reminders live under that id).

Security shape: the code is a bearer credential. It is high-entropy
(31^8 ~ 8.5e11), single use, and short-lived, so guessing an active code over
the public /pair endpoint is infeasible without an online brute force that the
short TTL + single-use window defeats. The code alphabet omits 0/O/1/I/L so it
survives being read aloud over a phone to an older shopkeeper.
"""
from __future__ import annotations

import datetime as _dt
import secrets

# No 0/O/1/I/L - unambiguous when read aloud or typed by an older user.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # 31 chars
_CODE_LEN = 8
DEFAULT_TTL_HOURS = 24


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def generate_code() -> str:
    """A fresh canonical code, e.g. 'K7P29M4T' (no separators)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))


def normalize_code(raw: str) -> str:
    """Strip spaces/dashes and uppercase - tolerant of how the owner types it."""
    return "".join(ch for ch in str(raw or "").upper() if ch in _ALPHABET)


def format_code(code: str) -> str:
    """Group for reading aloud: 'K7P29M4T' -> 'K7P2-9M4T'."""
    c = normalize_code(code)
    return f"{c[:4]}-{c[4:]}" if len(c) == _CODE_LEN else c


class PairingError(ValueError):
    """Redeeming failed (unknown / expired / already-used code)."""


def mint(db, business_id: str, ttl_hours: int = DEFAULT_TTL_HOURS,
         note: str | None = None) -> dict:
    """Create a single-use pairing code for ``business_id``. Retries on the rare
    code collision. Returns {code, code_display, expires_at}."""
    expires = _now() + _dt.timedelta(hours=max(1, int(ttl_hours)))
    last_err = None
    for _ in range(6):
        code = generate_code()
        row = {
            "code": code,
            "business_id": business_id,
            "note": note or None,
            "expires_at": expires.isoformat(),
            "used_at": None,
        }
        try:
            db.table("pairing_codes").insert(row).execute()
            return {"code": code, "code_display": format_code(code),
                    "expires_at": expires.isoformat()}
        except Exception as e:            # unique collision on code -> try again
            last_err = e
            continue
    raise RuntimeError(f"could not mint a pairing code ({last_err})")


def active_code(db, business_id: str) -> dict | None:
    """The newest still-valid (unused, unexpired) code for this business, or
    None. This is what makes 'Get code' idempotent: pressing it again returns
    the SAME code instead of piling up a fresh one each time - a code is a
    bearer credential, so we hand out the fewest that are live at once."""
    r = (db.table("pairing_codes").select("code, expires_at")
         .eq("business_id", business_id).is_("used_at", "null")
         .gt("expires_at", _now().isoformat())
         .order("created_at", desc=True).limit(1).execute()).data
    if not r:
        return None
    c = r[0]
    return {"code": c["code"], "code_display": format_code(c["code"]),
            "expires_at": c["expires_at"]}


def redeem(db, raw_code: str) -> dict:
    """Consume a code (single use) and return the bound business's identity,
    including its secret agent_token. Raises PairingError on any problem."""
    code = normalize_code(raw_code)
    if len(code) != _CODE_LEN:
        raise PairingError("That code doesn't look right. Please check and try again.")

    r = (db.table("pairing_codes").select("*")
         .eq("code", code).limit(1).execute()).data
    if not r:
        raise PairingError("This code is not valid.")
    pc = r[0]
    if pc.get("used_at"):
        raise PairingError("This code was already used. Ask for a fresh one.")
    exp = pc.get("expires_at")
    if exp and _dt.datetime.fromisoformat(str(exp)) <= _now():
        raise PairingError("This code has expired. Ask for a fresh one.")

    # Consume it. The is_ guard means a racing second redeem updates 0 rows.
    used = (db.table("pairing_codes").update({"used_at": _now().isoformat()})
            .eq("code", code).is_("used_at", "null").execute()).data
    if not used:
        raise PairingError("This code was already used. Ask for a fresh one.")

    biz = (db.table("businesses")
           .select("id, business_name, agent_token, license_key")
           .eq("id", pc["business_id"]).limit(1).execute()).data
    if not biz:
        raise PairingError("This code's business no longer exists.")
    b = biz[0]
    return {
        "business_id": b["id"],
        "business_name": b.get("business_name") or "",
        "agent_token": b["agent_token"],       # secret - handed to the paired install only
        "license_key": b.get("license_key") or "",
    }
