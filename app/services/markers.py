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


def settle(db, message_db_id, delivered: bool) -> None:
    if not message_db_id:
        return
    try:
        q = db.table("messages")
        q = q.update({"delivery_status": "sent"}) if delivered else q.delete()
        q.eq("parent_message_id", message_db_id).eq("template_name", MARKER).execute()
    except Exception:
        log.warning("marker settle failed for message %s", message_db_id, exc_info=True)
