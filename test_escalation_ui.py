"""The desktop Escalate button: bot.letter_for_client builds an owner-approved
formal reminder for ONE party by client_id (no name ambiguity), and the preview
branch (send=False) never sends - it only returns what the owner will read."""
import asyncio

from app.services import bot


def _mock_bills(monkeypatch, entry, en=True):
    async def _agg(_business_id):
        return {"c1": entry}
    monkeypatch.setattr(bot, "_open_bills_by_client", _agg)
    monkeypatch.setattr(bot, "_biz_is_en", lambda _bid: en)


def test_preview_returns_letter_without_sending(monkeypatch):
    from decimal import Decimal
    entry = {
        "client": {"id": "c1", "name": "Ramesh Traders",
                   "whatsapp_number": "919812345678", "language": "hi"},
        "bills": [{"id": "b1", "due_date": "2000-01-01"}],
        "total": Decimal("50000"),
        "oldest_days": 40,
    }
    _mock_bills(monkeypatch, entry)
    # If it tried to send, this would blow up (no whatsapp mock) - proving send=False is inert.
    out = asyncio.run(bot.letter_for_client(
        {"id": "biz1", "business_name": "ACME TRADERS - (from 1-Apr-2024)", "plan": "starter"},
        "c1", send=False))
    assert out["ok"] is True
    assert out["name"] == "Ramesh Traders"
    assert out["has_number"] is True
    assert out["days_overdue"] >= 40
    # The clean shop name (no Tally FY tag) is in the letter; no legal threat.
    assert "ACME TRADERS" in out["text"] and "from 1-Apr" not in out["text"]
    for bad in ("legal", "court", "lawyer"):
        assert bad not in out["text"].lower()


def test_preview_flags_missing_number(monkeypatch):
    from decimal import Decimal
    entry = {
        "client": {"id": "c1", "name": "No Number Co", "whatsapp_number": None},
        "bills": [{"id": "b1", "due_date": "2020-01-01"}],
        "total": Decimal("1000"),
        "oldest_days": 10,
    }
    _mock_bills(monkeypatch, entry)
    out = asyncio.run(bot.letter_for_client(
        {"id": "biz1", "business_name": "Shop", "plan": "starter"}, "c1", send=False))
    assert out["ok"] is True and out["has_number"] is False


def test_unknown_client_is_a_clean_error(monkeypatch):
    async def _agg(_b):
        return {}
    monkeypatch.setattr(bot, "_open_bills_by_client", _agg)
    out = asyncio.run(bot.letter_for_client(
        {"id": "biz1", "business_name": "Shop", "plan": "starter"}, "nope", send=False))
    assert out["ok"] is False and "Nothing to escalate" in out["detail"]
