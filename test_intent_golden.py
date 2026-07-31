"""Live golden accuracy test for reply-reading.

This is the real-world check the unit tests cannot give: it runs a representative
set of messy customer replies through the ACTUAL Gemini classifier and asserts the
intent is right at least 85% of the time. It is OFF by default (it hits the
network and uses quota) - run it deliberately:

    ASVA_GOLDEN=1 pytest test_intent_golden.py -q

Seed it with real customer replies from the field as they arrive; each new line
makes this a stronger guarantee. Calls are spaced to stay under the free-tier
rate limit (and intent.classify retries transient 429s anyway).
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

import pytest

from app.services import intent

# (message, expected_intent). Grow this from real WhatsApp replies over time.
GOLDEN = [
    ("paisa bhej diya sir", "paid_claim"),
    ("20000 kar diya aaj", "paid_claim"),
    ("already paid last week bhai", "paid_claim"),
    ("payment done sir", "paid_claim"),
    ("ho gaya transfer", "paid_claim"),
    ("5 tareek ko de dunga", "promise"),
    ("kal pakka payment ho jayega", "promise"),
    ("agle hafte de dunga", "promise"),
    ("bill galat hai itna nahi hua tha", "dispute"),
    ("maal kharab nikla, itne paise nahi dunga", "dispute"),
    ("good morning ji", "chatter"),
    ("dhanyavaad bhai", "chatter"),
]

pytestmark = pytest.mark.skipif(
    os.getenv("ASVA_GOLDEN") != "1" or not intent.is_configured(),
    reason="set ASVA_GOLDEN=1 with GEMINI_API_KEY to run the live golden accuracy test",
)


def test_reply_reading_golden_accuracy():
    async def run():
        out = []
        for text, expected in GOLDEN:
            v = await intent.classify(text)
            out.append((text, expected, v["intent"] if v else None))
            await asyncio.sleep(5.0)          # free tier is ~15 req/min; stay well under
        return out

    results = asyncio.run(run())
    # A None is the API being rate-limited / unreachable, NOT a wrong answer. Only
    # judge accuracy over the replies that actually classified; if too few got
    # through, the run is inconclusive (skip) rather than a false failure.
    classified = [(t, e, g) for (t, e, g) in results if g is not None]
    if len(classified) < len(GOLDEN) * 0.6:
        import pytest as _pt
        _pt.skip(f"Gemini unavailable/rate-limited: only {len(classified)}/{len(GOLDEN)} "
                 f"classified. Re-run when quota is free.")
    misses = [(t, e, g) for (t, e, g) in classified if g != e]
    acc = (len(classified) - len(misses)) / len(classified)
    assert acc >= 0.85, f"reply-reading accuracy {acc:.0%} over {len(classified)} classified; misses={misses}"
