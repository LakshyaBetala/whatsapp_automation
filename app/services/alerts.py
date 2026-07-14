"""Operator alerts: record health problems and email them (once per incident).

The watchdog (jobs/monitor.py) calls ``reconcile`` with the set of problems it
currently sees. We open a NEW alert only for a problem that is not already open
(so you get one email when a shop's WhatsApp drops, not one every 5 minutes),
and we resolve alerts whose problem has cleared (with a short "recovered" note).

Email is best-effort and config-gated: with no SMTP set, alerts are still
recorded in alert_log and shown in the Command Center - they just are not
mailed. Gmail needs an APP PASSWORD (smtp_host=smtp.gmail.com, port 587).
"""
from __future__ import annotations

import datetime as _dt
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from app.config import settings

log = logging.getLogger(__name__)


def email_configured() -> bool:
    s = settings
    return bool(s.smtp_host and s.smtp_user and s.smtp_pass and s.alert_email_to)


def send_email(subject: str, body: str) -> bool:
    """Send one plain-text email to the operator. False if not configured or the
    send failed (never raises - alerting must not break the caller)."""
    if not email_configured():
        return False
    s = settings
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.alert_email_from or s.smtp_user
    msg["To"] = s.alert_email_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as srv:
            srv.starttls(context=ssl.create_default_context())
            srv.login(s.smtp_user, s.smtp_pass)
            srv.send_message(msg)
        return True
    except Exception:
        log.exception("Alert email failed (subject=%s)", subject)
        return False


def _open_key(row: dict) -> tuple:
    return (row.get("kind"), row.get("business_id"))


def list_open(db) -> list[dict]:
    r = (db.table("alert_log").select("*")
         .is_("resolved_at", "null").order("created_at", desc=True).execute())
    return r.data or []


def list_recent(db, limit: int = 40) -> list[dict]:
    r = (db.table("alert_log").select("*")
         .order("created_at", desc=True).limit(limit).execute())
    return r.data or []


def reconcile(db, problems: list[dict]) -> dict:
    """Open new alerts, resolve cleared ones. `problems` = the CURRENT issues,
    each {kind, business_id?, severity, title, body}. Returns counts + whether
    anything was emailed. One email per newly-opened problem; one 'recovered'
    email per critical that cleared.
    """
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    open_now = list_open(db)
    open_by_key = {_open_key(a): a for a in open_now}
    want_by_key = {(p["kind"], p.get("business_id")): p for p in problems}

    opened = resolved = emailed = 0

    # 1) Open the problems that are new (not already open).
    for key, p in want_by_key.items():
        if key in open_by_key:
            continue
        sev = p.get("severity", "warn")
        did_mail = False
        if sev in ("warn", "critical"):
            did_mail = send_email(f"[ASVA {sev.upper()}] {p['title']}",
                                  (p.get("body") or p["title"]) +
                                  f"\n\nTime: {now}\nOpen your Command Center to check.")
        try:
            db.table("alert_log").insert({
                "business_id": p.get("business_id"),
                "kind": p["kind"], "severity": sev,
                "title": p["title"], "body": p.get("body"),
                "emailed": did_mail,
            }).execute()
            opened += 1
            emailed += 1 if did_mail else 0
        except Exception:
            log.exception("Failed to record alert %s", p.get("kind"))

    # 2) Resolve the ones that have cleared.
    for key, a in open_by_key.items():
        if key in want_by_key:
            continue
        try:
            db.table("alert_log").update({"resolved_at": now}).eq("id", a["id"]).execute()
            resolved += 1
            if a.get("severity") == "critical":
                send_email(f"[ASVA recovered] {a['title']}",
                           f"This has recovered: {a['title']}\nTime: {now}")
        except Exception:
            log.exception("Failed to resolve alert %s", a.get("id"))

    return {"opened": opened, "resolved": resolved, "emailed": emailed,
            "open_total": len(want_by_key)}
