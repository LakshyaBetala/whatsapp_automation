"""One door to Gemini: Vertex AI first, the AI Studio key second, None last."""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import llm

OK = {"candidates": [{"content": {"parts": [{"text": '{"intent": "order"}'}]}}]}


class Resp:
    def __init__(self, status, data=None):
        self.status_code, self._data, self.text = status, data, str(data)
    def json(self): return self._data


def _wire(monkeypatch, responses, *, project="proj", key="k", token="tok"):
    calls = []
    async def fake_post(url, *, headers=None, params=None, json=None, timeout=30.0):
        calls.append((url, headers, params, json))
        return responses.pop(0)
    async def fake_token():
        return token
    monkeypatch.setattr(llm, "_post", fake_post)
    monkeypatch.setattr(llm, "_metadata_token", fake_token)
    monkeypatch.setattr(llm.settings, "vertex_project", project)
    monkeypatch.setattr(llm.settings, "gemini_api_key", key)
    async def no_sleep(*a): return None
    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)
    return calls


def _payload():
    return {"contents": [{"parts": [{"text": "hi"}]}], "generationConfig": {}}


def test_vertex_answers_first_with_the_user_role(monkeypatch):
    calls = _wire(monkeypatch, [Resp(200, OK)])
    assert asyncio.run(llm.generate(_payload())) == OK
    url, headers, params, body = calls[0]
    assert "aiplatform.googleapis.com" in url and "/projects/proj/" in url
    assert headers == {"Authorization": "Bearer tok"} and params is None
    assert body["contents"][0]["role"] == "user"


def test_vertex_rate_limited_falls_back_to_the_key(monkeypatch):
    calls = _wire(monkeypatch, [Resp(429), Resp(429), Resp(200, OK)])
    assert asyncio.run(llm.generate(_payload())) == OK
    assert "generativelanguage.googleapis.com" in calls[-1][0]
    assert calls[-1][2] == {"key": "k"}


def test_off_gcp_skips_vertex(monkeypatch):
    calls = _wire(monkeypatch, [Resp(200, OK)], token=None)
    assert asyncio.run(llm.generate(_payload())) == OK
    assert len(calls) == 1 and "generativelanguage" in calls[0][0]


def test_everything_failing_returns_none(monkeypatch):
    _wire(monkeypatch, [Resp(429), Resp(429), Resp(403)])
    assert asyncio.run(llm.generate(_payload())) is None


def test_nothing_configured(monkeypatch):
    _wire(monkeypatch, [], project="", key="")
    assert llm.is_configured() is False
    assert asyncio.run(llm.generate(_payload())) is None


def test_text_of_is_safe():
    assert llm.text_of(OK) == '{"intent": "order"}'
    assert llm.text_of({}) is None and llm.text_of(None) is None
