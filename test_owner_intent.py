"""Owners can ask in plain words; the bot answers read-only questions only,
and leaves personal chat alone (the bot number is also the founder's own)."""
import asyncio
import json
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import owner_intent as oi


def test_question_cues():
    for t in ("Ramesh ka kitna baki hai?", "how much does Ramesh owe", "aaj kisko reminder gaya",
              "list bhejo", "is mahine kitna wapas aaya", "kaun kaun pay karne wala hai"):
        assert oi.looks_like_question(t), t
    for t in ("hello sir", "kal milte hai", "thank you", "ok", "theek hai"):
        assert not oi.looks_like_question(t), t


def _llm(monkeypatch, answer):
    async def fake_generate(payload, **kw):
        if answer is None:
            return None
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}
    monkeypatch.setattr(oi.llm, "generate", fake_generate)


def test_route_maps_to_a_read_only_command(monkeypatch):
    _llm(monkeypatch, {"command": "CHECK", "party": "Ramesh", "confidence": 0.93})
    assert asyncio.run(oi.route("Ramesh ka kitna baki hai?")) == "CHECK Ramesh"
    _llm(monkeypatch, {"command": "SENT", "party": "", "confidence": 0.9})
    assert asyncio.run(oi.route("aaj kisko reminder gaya")) == "SENT"


def test_route_never_runs_an_action_command(monkeypatch):
    for cmd in ("PAID", "REMIND", "STOP", "BILL", "EXCLUDE", "LETTER"):
        _llm(monkeypatch, {"command": cmd, "party": "Ramesh", "confidence": 0.99})
        assert asyncio.run(oi.route("Ramesh ne paise de diye")) is None, cmd


def test_route_needs_confidence_and_degrades_quietly(monkeypatch):
    _llm(monkeypatch, {"command": "LIST", "party": "", "confidence": 0.5})
    assert asyncio.run(oi.route("list?")) is None
    _llm(monkeypatch, {"command": "NONE", "party": "", "confidence": 0.99})
    assert asyncio.run(oi.route("kya haal hai?")) is None
    _llm(monkeypatch, None)                     # AI down
    assert asyncio.run(oi.route("list?")) is None


def test_party_names_are_cleaned():
    assert oi._clean_party("  Ramesh Traders?? ") == "Ramesh Traders"
    assert oi._clean_party("x" * 200) == "x" * 60
