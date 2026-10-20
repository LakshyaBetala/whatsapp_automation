"""One door to Gemini for ASVA: Vertex AI first, the AI Studio key second,
None last.

Vertex AI is billed to the Google Cloud account (the trial credit while it
lasts) and needs no key file on the Google Cloud VM: the instance's attached
service account hands out short-lived tokens through the metadata server.
The AI Studio key is the fallback; its free tier allows only 20 requests a day
per model, fine as a backup and useless as the main path. Off Google Cloud
(for example after a move to Kamatera) the metadata server is absent, Vertex is
skipped, and the key takes over on its own.

generate() returns Gemini's raw generateContent JSON. None always means the
caller must not guess: it degrades (tells the owner, never acts).
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time

import httpx

from app.config import settings

log = logging.getLogger(__name__)

METADATA_TOKEN_URL = ("http://metadata.google.internal/computeMetadata/v1/"
                      "instance/service-accounts/default/token")
VERTEX_URL = ("https://aiplatform.googleapis.com/v1/projects/{project}/locations/"
              "{location}/publishers/google/models/{model}:generateContent")
STUDIO_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# 429 = rate limit, 5xx = a hiccup: worth one retry. Anything else will not fix
# itself (bad request, bad key, model retired) and moves to the next provider.
_RETRY = {429, 500, 502, 503, 504}
_token: dict = {"value": None, "expires": 0.0}


def is_configured() -> bool:
    return bool(settings.vertex_project or settings.gemini_api_key)


async def _post(url: str, *, headers=None, params=None, json=None,
                timeout: float = 30.0) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as http:
        return await http.post(url, headers=headers, params=params, json=json)


async def _metadata_token() -> str | None:
    """A Google access token for the VM's service account, cached until a
    minute before it expires. None when not running on Google Cloud."""
    now = time.time()
    if _token["value"] and now < _token["expires"]:
        return _token["value"]
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            r = await http.get(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
        if r.status_code != 200:
            return None
        data = r.json()
        _token["value"] = data["access_token"]
        _token["expires"] = now + max(60, int(data.get("expires_in", 300)) - 60)
        return _token["value"]
    except Exception:
        return None


def _with_user_role(payload: dict) -> dict:
    """Vertex requires role=user on each turn; the AI Studio API accepts it."""
    body = copy.deepcopy(payload)
    for turn in body.get("contents", []):
        turn.setdefault("role", "user")
    return body


async def _call(url: str, *, label: str, timeout: float, attempts: int = 2,
                **kw) -> dict | None:
    last = ""
    for i in range(attempts):
        try:
            r = await _post(url, timeout=timeout, **kw)
            if r.status_code == 200:
                return r.json()
            if r.status_code not in _RETRY:
                log.warning("llm %s HTTP %s: %s", label, r.status_code,
                            str(getattr(r, "text", ""))[:200])
                return None
            last = f"HTTP {r.status_code}"
        except Exception as e:                       # timeout / network
            last = repr(e)
        if i < attempts - 1:
            await asyncio.sleep(0.6 * (i + 1))
    log.warning("llm %s failed after %d attempts (%s)", label, attempts, last)
    return None


async def generate(payload: dict, *, vertex_model: str | None = None,
                   studio_model: str | None = None, timeout: float = 30.0) -> dict | None:
    body = _with_user_role(payload)
    if settings.vertex_project:
        tok = await _metadata_token()
        if tok:
            url = VERTEX_URL.format(project=settings.vertex_project,
                                    location=settings.vertex_location,
                                    model=vertex_model or settings.vertex_model)
            data = await _call(url, label="vertex", timeout=timeout,
                               headers={"Authorization": f"Bearer {tok}"}, json=body)
            if data is not None:
                return data
    if settings.gemini_api_key:
        url = STUDIO_URL.format(model=studio_model or settings.gemini_intent_model)
        data = await _call(url, label="ai-studio", timeout=timeout,
                           params={"key": settings.gemini_api_key}, json=body)
        if data is not None:
            return data
    return None


def text_of(data) -> str | None:
    """The first text part of a generateContent response, or None."""
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None
