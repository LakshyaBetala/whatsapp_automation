"""Health center: the snapshot builder, the problem evaluator, and the alert
reconcile (open once / dedup / resolve).
"""
import datetime as _dt
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import alerts, monitoring


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ── fake read DB for build_health (ignores filters; paging-safe) ──────────
class _Q:
    def __init__(self, rows):
        self._rows, self._start = rows, 0

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, s, e): self._start = s; return self

    def execute(self):
        return type("R", (), {"data": self._rows if self._start == 0 else []})()


class _DB:
    def __init__(self, t): self.t = t
    def table(self, n): return _Q(list(self.t.get(n, [])))


def test_build_health_counts_and_jobs():
    today = _dt.date.today()
    now = _now_iso()
    active_exp = (today + _dt.timedelta(days=20)).isoformat()
    db = _DB({
        "businesses": [
            {"id": "a", "business_name": "Alpha", "plan": "pro",
             "plan_expires_on": active_exp, "last_seen": now,
             "wa_ready": True, "wa_checked_at": now},
            {"id": "b", "business_name": "Beta", "plan": "starter",
             "plan_expires_on": active_exp, "last_seen": now,
             "wa_ready": False, "wa_checked_at": now},
        ],
        "messages": [
            {"business_id": "a", "delivery_status": "sent", "created_at": now},
            {"business_id": "a", "delivery_status": "delivered", "created_at": now},
            {"business_id": "a", "delivery_status": "failed", "created_at": now},
            {"business_id": "b", "delivery_status": "limit_reached", "created_at": now},
            {"business_id": "b", "delivery_status": "queued", "created_at": now},
        ],
        "wa_outbox": [
            {"business_id": "b", "created_at": now},
            {"business_id": "b", "created_at": now},
        ],
        "job_heartbeats": [
            {"job_name": "reminder_sweep", "last_run_at": now, "ok": True},
            {"job_name": "monitor",
             "last_run_at": (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat(),
             "ok": True},
        ],
    })
    h = monitoring.build_health(db)
    t = h["totals"]
    assert t["businesses"] == 2 and t["online"] == 2
    assert t["sent_today"] == 2 and t["failed_today"] == 1
    assert t["blocked_today"] == 1 and t["queued_now"] == 2
    assert t["wa_down"] == 1                       # Beta wa_ready False, not suspended
    assert len(h["traffic"]) == 14
    jobs = {j["name"]: j for j in h["jobs"]}
    assert jobs["reminder_sweep"]["stale"] is False
    assert jobs["monitor"]["stale"] is True        # 2h > 20min budget
    beta = next(r for r in h["businesses"] if r["id"] == "b")
    assert beta["wa_ready"] is False and beta["queued"] == 2


def test_evaluate_flags_problems():
    health = {
        "system": {"bot_wa": {"ok": False}},
        "jobs": [{"name": "reminder_sweep", "mins_ago": 200, "stale": True}],
        "businesses": [
            {"id": "b", "name": "Beta", "status": "active", "online": False,
             "last_seen_min": 90, "wa_ready": False, "queued": 30,
             "queue_oldest_min": 40, "sent_today": 5, "failed_today": 6, "blocked_today": 0},
            {"id": "s", "name": "Susp", "status": "suspended", "online": False,
             "last_seen_min": 999, "wa_ready": False, "queued": 99,
             "queue_oldest_min": 0, "sent_today": 0, "failed_today": 0, "blocked_today": 0},
        ],
    }
    kinds = {p["kind"] for p in monitoring.evaluate(health)}
    assert "bot_wa_down" in kinds
    assert "job_stale:reminder_sweep" in kinds
    assert "shop_offline" in kinds and "wa_down" in kinds
    assert "outbox_stuck" in kinds and "high_failrate" in kinds
    # suspended shop raises NO alerts (expected state)
    assert not any(p.get("business_id") == "s" for p in monitoring.evaluate(health))


# ── stateful fake for alert reconcile ─────────────────────────────────────
class _AQ:
    def __init__(self, db):
        self.db = db; self._mode = "select"; self._payload = None
        self._id = None; self._openonly = False

    def select(self, *a, **k): self._mode = "select"; return self
    def insert(self, row): self._mode = "insert"; self._payload = row; return self
    def update(self, vals): self._mode = "update"; self._payload = vals; return self
    def is_(self, col, val): self._openonly = True; return self
    def eq(self, col, val):
        if col == "id": self._id = val
        return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        R = lambda data: type("R", (), {"data": data})()
        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", f"id{len(self.db.rows)}")
            row.setdefault("resolved_at", None)
            row.setdefault("created_at", _now_iso())
            self.db.rows.append(row)
            return R([row])
        if self._mode == "update":
            for r in self.db.rows:
                if r["id"] == self._id:
                    r.update(self._payload)
            return R([])
        rows = self.db.rows
        if self._openonly:
            rows = [r for r in rows if r.get("resolved_at") is None]
        return R(list(rows))


class _AlertDB:
    def __init__(self): self.rows = []
    def table(self, n): return _AQ(self)


def test_reconcile_opens_dedups_resolves():
    db = _AlertDB()
    p = [{"kind": "wa_down", "business_id": "b", "severity": "critical",
          "title": "Beta WhatsApp down", "body": "reconnect"}]
    assert alerts.reconcile(db, p)["opened"] == 1
    assert alerts.reconcile(db, p)["opened"] == 0          # dedup: still one open
    assert alerts.reconcile(db, [])["resolved"] == 1       # cleared -> resolved
    assert alerts.reconcile(db, p)["opened"] == 1          # recurs -> new alert
    assert len([r for r in db.rows if r["resolved_at"] is None]) == 1
