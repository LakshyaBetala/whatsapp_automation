"""Cadence markers follow the real message they stand for.

The reminder sweep writes one marker per (bill, cadence day) so a point never
fires twice. When the real message is only QUEUED (it waits for a WhatsApp to
deliver it), the marker is written 'queued' with parent_message_id = the real
message. When the outbox row reaches a final state, settle() is called:
delivered -> markers become 'sent'; expired / failed / held -> markers are
deleted, so the cadence point is due again at the next sweep. Best-effort: a
failure here must never break a delivery or an ack.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MARKER = "cadence_marker"


def settle(db, message_db_id, delivered: bool, *, permanent: bool = False) -> None:
    """Close out the cadence markers a queued reminder was holding.

    Delivered -> the point is spent, mark it sent. Failed -> delete the marker
    so the point comes round again on the next sweep, which is right for a
    transient failure like WhatsApp being down.

    It is wrong for a PERMANENT one. A number that is not on WhatsApp will
    never be on WhatsApp, and deleting the marker put nine parties into an
    hourly retry that produced 120 failed sends, ate the daily cap and buried
    the real failures in noise. So a permanent failure keeps the marker and
    flags it failed: the point is spent, the party is not chased again on it,
    and the failure stays on the record where the owner can see it.
    """
    if not message_db_id:
        return
    try:
        q = db.table("messages")
        if delivered:
            q = q.update({"delivery_status": "sent"})
        elif permanent:
            q = q.update({"delivery_status": "failed"})
        else:
            q = q.delete()
        q.eq("parent_message_id", message_db_id).eq("template_name", MARKER).execute()
    except Exception:
        log.warning("marker settle failed for message %s", message_db_id, exc_info=True)
