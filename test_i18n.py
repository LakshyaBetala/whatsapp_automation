"""Owner-facing language (app/services/i18n.py) + the heartbeat/bot wiring.

The promise: the owner picks English or Hinglish once in the app, it is saved on
the business, and every owner-facing reply comes back in that language ("English
chosen -> pure English everywhere"). Unknown values never crash - they fall back
to English.
"""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import i18n


# ── norm_lang ────────────────────────────────────────────────────────────────
def test_norm_lang_aliases():
    for v in ("english", "English", "EN", "eng", "  english "):
        assert i18n.norm_lang(v) == "english"
    for v in ("hinglish", "hi", "hin", "hindi", "HINGLISH"):
        assert i18n.norm_lang(v) == "hinglish"


def test_norm_lang_defaults_and_junk():
    for v in ("", None, "klingon", "français", 123):
        assert i18n.norm_lang(v) == "english"       # safe default, never crashes
    assert i18n.is_english("en") is True
    assert i18n.is_english("hi") is False


# ── t() ──────────────────────────────────────────────────────────────────────
def test_t_returns_each_language():
    en = i18n.t("english", "help")
    hi = i18n.t("hinglish", "help")
    assert en != hi
    assert "SEE YOUR MONEY" in en                    # pure English
    assert "APNA PAISA DEKHEIN" in hi                # Hinglish
    # every command name stays literal in both (owner types the same word)
    for cmd in ("LIST", "PAID", "STOP", "CHASE", "REMIND"):
        assert cmd in en and cmd in hi


def test_t_formats_kwargs_in_both():
    en = i18n.t("english", "stopped", name="Ramesh")
    hi = i18n.t("hinglish", "stopped", name="Ramesh")
    assert "Ramesh" in en and "START Ramesh" in en
    assert "Ramesh" in hi and "START Ramesh" in hi


def test_t_unknown_key_and_lang_are_safe():
    assert i18n.t("english", "no_such_key") == "no_such_key"
    # unknown language -> English text, never a crash
    assert i18n.t("martian", "help") == i18n.t("english", "help")


def test_no_em_or_en_dashes_in_owner_copy():
    # House rule: no em/en dashes anywhere in ASVA owner-facing copy.
    for key in ("help", "unknown_prefix", "which_one", "no_match", "stopped", "started"):
        for lang in ("english", "hinglish"):
            s = i18n.t(lang, key)
            assert "—" not in s and "–" not in s, (key, lang)


# ── heartbeat carries the saved language ─────────────────────────────────────
def test_heartbeat_returns_owner_language(monkeypatch):
    import datetime as _dt
    from app.services import license as lic
    monkeypatch.setattr(lic, "active_debtor_count", lambda db, bid: 1)
    monkeypatch.setattr(lic, "messages_used_this_month", lambda db, bid, today=None: 0)
    monkeypatch.setattr(lic, "_latest_release", lambda db: ("1.8.4", False))
    monkeypatch.setattr(lic, "ensure_license_key", lambda db, biz: "ASVA-A")
    biz = {"id": "b1", "plan": "pro", "plan_expires_on": None, "owner_language": "hi"}
    hb = lic.build_heartbeat(db=None, biz=biz, today=_dt.date(2026, 7, 12))
    assert hb["owner_language"] == "hinglish"        # normalised from "hi"
    # missing -> English
    hb2 = lic.build_heartbeat(db=None, biz={"id": "b", "plan": "pro",
                              "plan_expires_on": None}, today=_dt.date(2026, 7, 12))
    assert hb2["owner_language"] == "english"


def test_set_language_endpoint_persists(monkeypatch):
    from app.routers import license as lr

    saved = {}

    class _Q:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def order(self, *a, **k): return self
        def limit(self, n): return self
        def update(self, u): saved.update(u); return self
        def execute(self):
            return type("R", (), {"data": [{"id": "b1", "agent_token": "tok"}]})()

    class _DB:
        def table(self, name): return _Q()

    monkeypatch.setattr(lr, "require_db", lambda: _DB())
    out = asyncio.run(lr.set_language(lr.SetLanguagePayload(agent_token="tok", language="hi")))
    assert out["owner_language"] == "hinglish"
    assert saved == {"owner_language": "hinglish"}    # normalised value written
