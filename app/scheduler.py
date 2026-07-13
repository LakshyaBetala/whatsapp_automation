"""APScheduler setup - runs in-process inside the FastAPI app.

Three recurring jobs (all in the configured timezone):
  - EOD 9pm digest      -> jobs.eod_digest.run
  - Reminder sweep      -> jobs.reminder_sweep.run   (mid-morning)
  - Supabase keep-alive -> jobs.keepalive.ping       (free tier pauses after 7d idle)
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db import get_client
from app.jobs import (eod_digest, keepalive, monitor, outbox_sweep,
                      reminder_sweep, subscription_check)
from app.services import monitoring

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _tracked(name: str, fn):
    """Wrap a scheduler job so every run stamps job_heartbeats (ok/fail). Lets
    the health center show 'reminder sweep ran 4 min ago' and alert if a job
    goes quiet. The job's own error handling is unchanged; we just record."""
    async def _wrapped():
        ok = True
        try:
            await fn()
        except Exception:
            ok = False
            log.exception("Scheduled job %s failed", name)
        finally:
            db = get_client()
            if db is not None:
                monitoring.stamp_job(db, name, ok=ok)
    return _wrapped


def start() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    sched = AsyncIOScheduler(timezone=settings.timezone)

    # Runs hourly: each business sends once its own digest_hour is reached
    # (owner sets it from the bot with "DIGEST 9PM"); per-day dedup inside.
    if settings.enable_eod_digest:
        sched.add_job(
            _tracked("eod_digest", eod_digest.run),
            CronTrigger(minute=settings.eod_digest_minute),
            id="eod_digest",
            replace_existing=True,
            misfire_grace_time=3600,
        )
    else:
        log.info("EOD digest DISABLED (ENABLE_EOD_DIGEST=false)")

    # Runs every hour (at :minute). Each business only sends once its own
    # reminder_hour is reached; per-bill dedup keeps every reminder to one
    # send/day. Hourly (not daily) so a laptop that was off at the send hour
    # still catches up the next hour it is on. See jobs/reminder_sweep.py.
    if settings.enable_reminder_sweep:
        sched.add_job(
            _tracked("reminder_sweep", reminder_sweep.run),
            CronTrigger(minute=settings.reminder_sweep_minute),
            id="reminder_sweep",
            replace_existing=True,
            misfire_grace_time=3600,
        )
    else:
        log.info("Reminder sweep DISABLED (ENABLE_REMINDER_SWEEP=false)")

    # Subscription lifecycle: warn before expiry, flip to grace/suspended.
    if settings.enable_subscription_check:
        sched.add_job(
            _tracked("subscription_check", subscription_check.run),
            CronTrigger(hour=9, minute=0),
            id="subscription_check",
            replace_existing=True,
            misfire_grace_time=3600,
        )
    else:
        log.info("Subscription check DISABLED (ENABLE_SUBSCRIPTION_CHECK=false)")

    # Outbox: deliver bot-queued customer sends from the SHOP number. Every
    # minute; each run paces sends 12-40s apart and stops early if the shop
    # WhatsApp is offline (the queue simply waits).
    if settings.enable_outbox_send:
        sched.add_job(
            _tracked("outbox_sweep", outbox_sweep.run),
            IntervalTrigger(seconds=60),
            id="outbox_sweep",
            replace_existing=True,
            max_instances=1,       # never overlap two sweeps (double-send risk)
            coalesce=True,
            misfire_grace_time=55,
        )
    else:
        log.info("Outbox send DISABLED (ENABLE_OUTBOX_SEND=false)")

    # Health watchdog: build the snapshot, check the bot WhatsApp, and email the
    # operator about anything that needs attention (server/bot/shop WhatsApp
    # down, stuck queues, high failure rate). Runs on the HOST only.
    if settings.enable_monitor:
        sched.add_job(
            monitor.run,
            IntervalTrigger(minutes=max(1, settings.monitor_interval_min)),
            id="monitor",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )
    else:
        log.info("Monitor watchdog DISABLED (ENABLE_MONITOR=false)")

    # Ping ourselves so the Supabase free project never idles into a pause.
    sched.add_job(
        keepalive.ping,
        CronTrigger(hour="*/6"),  # every 6 hours
        id="keepalive",
        replace_existing=True,
    )

    sched.start()
    _scheduler = sched
    log.info(
        "Scheduler started: EOD %02d:%02d, reminders hourly at :%02d "
        "(each business sends at its own reminder_hour) (%s)",
        settings.eod_digest_hour,
        settings.eod_digest_minute,
        settings.reminder_sweep_minute,
        settings.timezone,
    )
    return sched


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
