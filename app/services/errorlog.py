"""Central error capture - swallowed exceptions still get seen.

The app has ~168 best-effort ``except Exception: log.exception(...)`` blocks that
keep it alive but bury the error in a local log nobody reads. This attaches a
logging handler that ALSO records ERROR+ log records into ``alert_log`` (kind
``app_error``), so they surface in the Command Center's recent-alerts view - the
same table the monitor/alerts pipeline already emails from.

It is built to be invisible to the thing it observes:
  * only ERROR and above (rare on a healthy box),
  * de-duped per (logger, message) for a few minutes so a hot loop can't flood,
  * re-entrancy guarded so a failure WHILE reporting never recurses,
  * fully best-effort - any error inside the handler is swallowed.

If ``SENTRY_DSN`` is set (and sentry-sdk is installed) Sentry is initialised too,
for proper long-term capture; without it, the alert_log path still works.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import traceback

_log = logging.getLogger(__name__)
_local = threading.local()
_seen: dict[str, float] = {}
_DEDUP_TTL = 300.0          # same error within 5 min -> recorded once
_MAXLEN = 4000
_SKIP_PREFIXES = ("app.services.errorlog", "urllib3", "httpx", "httpcore",
                  "hpack", "postgrest", "hpack.hpack")


class DBErrorLogHandler(logging.Handler):
    """Persist ERROR+ log records to alert_log. Never raises, never recurses."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        if getattr(_local, "in_emit", False):
            return
        if record.name.startswith(_SKIP_PREFIXES):
            return
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(getattr(record, "msg", ""))
        now = time.time()
        key = hashlib.md5((record.name + "|" + msg[:120]).encode("utf-8", "ignore")).hexdigest()
        last = _seen.get(key)
        if last is not None and now - last < _DEDUP_TTL:
            return
        _seen[key] = now
        if len(_seen) > 500:                     # prune stale keys occasionally
            for k, t in list(_seen.items()):
                if now - t > _DEDUP_TTL:
                    _seen.pop(k, None)

        _local.in_emit = True
        try:
            from app.db import get_client
            db = get_client()
            if db is None:
                return
            body = msg
            if record.exc_info:
                body = msg + "\n\n" + "".join(traceback.format_exception(*record.exc_info))
            db.table("alert_log").insert({
                "kind": "app_error",
                "severity": "critical" if record.levelno >= logging.CRITICAL else "warn",
                "title": f"{record.name}: {msg[:180]}",
                "body": body[:_MAXLEN],
                "emailed": False,
            }).execute()
        except Exception:
            pass                                 # observing must never break the observed
        finally:
            _local.in_emit = False


def install() -> None:
    """Attach the DB error handler once, and init Sentry if a DSN is configured.
    No-ops on any failure so startup is never blocked by observability."""
    try:
        root = logging.getLogger()
        if not any(isinstance(h, DBErrorLogHandler) for h in root.handlers):
            h = DBErrorLogHandler()
            h.setLevel(logging.ERROR)
            root.addHandler(h)
            _log.info("Error capture attached (alert_log).")
    except Exception:
        _log.warning("could not attach DB error handler", exc_info=True)

    try:
        from app.config import settings
        dsn = getattr(settings, "sentry_dsn", "") or ""
        if dsn:
            try:
                import sentry_sdk
                from sentry_sdk.integrations.logging import LoggingIntegration
                sentry_sdk.init(
                    dsn=dsn,
                    traces_sample_rate=0.0,
                    integrations=[LoggingIntegration(
                        level=logging.INFO, event_level=logging.ERROR)],
                )
                _log.info("Sentry initialised.")
            except ImportError:
                _log.info("SENTRY_DSN set but sentry-sdk is not installed "
                          "(pip install sentry-sdk to enable).")
    except Exception:
        pass
