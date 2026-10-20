"""Owners can ask the ASVA bot in plain words.

"Ramesh ka kitna baki hai?" -> CHECK Ramesh; "aaj kisko reminder gaya?" ->
SENT. Two guards keep this safe on a number that is also the founder's own
WhatsApp:
  1. Only a message that looks like a question to the bot (looks_like_question)
     reaches the AI; plain chat ("hello sir", "kal milte hai") never does and
     gets no reply.
  2. Only READ-ONLY commands are ever run from plain words. Recording a
     payment, reminding a customer, stopping reminders or adding a bill always
     need the owner's exact command; an AI guess never moves money or messages.
The AI goes through llm.generate (Vertex first, key fallback); None = silent.
"""
from __future__ import annotations

import json
import re

from app.services import llm

READ_ONLY = {"LIST", "CHECK", "SENT", "RECOVERED", "PROMISES", "DIGEST", "HELP"}
MIN_CONFIDENCE = 0.8

_CUE = re.compile(
    r"\?|\b(kitna|kitne|kitni|baki|baaki|bakaya|balance|owe|owes|outstanding|list|"
    r"kaun|kon|kisne|kisko|who|how much|status|hisab|hisaab|sent|bheja|gaya|recovered|"
    r"wapas|aaya|promise|promised|digest|summary|report|evlo|evvalavu|yaar)\b",
    re.IGNORECASE)

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "command": {"type": "STRING", "enum": sorted(READ_ONLY | {"NONE"})},
        "party": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
    },
    "required": ["command", "confidence"],
}

_PROMPT = (
    "A shop owner sent this WhatsApp message to ASVA, a payment-collection assistant "
    "for Indian wholesale shops. It may be in English, Hindi, Hinglish or Tamil. "
    "Map it to ONE read-only command, or NONE:\n"
    "  LIST = who owes money / the list of dues\n"
    "  CHECK = one party's balance (put the party's name in `party`)\n"
    "  SENT = who was reminded today\n"
    "  RECOVERED = how much came back this month\n"
    "  PROMISES = who promised to pay or said they paid\n"
    "  DIGEST = today's summary\n"
    "  HELP = what can you do / how to use ASVA\n"
    "  NONE = personal chat, greetings, thanks, or anything else, including any "
    "request to record a payment, send a reminder, stop reminders or add a bill.\n"
    "Return command, party (empty unless CHECK) and confidence 0 to 1. If unsure, NONE."
)


def looks_like_question(text: str) -> bool:
    return bool(_CUE.search(text or ""))


def _clean_party(name: str) -> str:
    return re.sub(r"[^\w .&'-]", "", (name or "")).strip()[:60]


async def route(text: str) -> str | None:
    """The command line to run for this owner message, or None (stay silent)."""
    payload = {
        "contents": [{"parts": [{"text": _PROMPT + "\n\nMessage:\n" + (text or "")[:500]}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": _SCHEMA,
                             "temperature": 0, "thinkingConfig": {"thinkingBudget": 0}},
    }
    data = await llm.generate(payload, timeout=20)
    raw = llm.text_of(data)
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        cmd = str(obj.get("command") or "NONE").upper().strip()
        conf = float(obj.get("confidence") or 0)
    except (ValueError, TypeError, AttributeError):
        return None
    if cmd not in READ_ONLY or conf < MIN_CONFIDENCE:
        return None
    if cmd == "CHECK":
        party = _clean_party(obj.get("party") or "")
        return f"CHECK {party}" if party else None
    return cmd
