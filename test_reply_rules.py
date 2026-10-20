"""Preset rules settle common customer replies with no AI call."""
import pytest

from app.services.reply_rules import match


@pytest.mark.parametrize("text,intent", [
    ("Paid", "paid_claim"),
    ("payment done sir", "paid_claim"),
    ("paisa bhej diya", "paid_claim"),
    ("kattiten", "paid_claim"),
    ("All payment paid", "paid_claim"),
    ("Paid using Google Pay! ✅ Crores of Indians trust Google Pay", "paid_claim"),
    ("kitna baki hai bhai", "balance_query"),
    ("evlo balance", "balance_query"),
    ("statement bhejo", "statement_request"),
    ("send ledger", "statement_request"),
    ("bill bhejo", "bill_request"),
    ("invoice copy please", "bill_request"),
    ("UPI id do", "payment_info"),
    ("account number send", "payment_info"),
    ("Nill balance", "dispute"),
    ("not our pls check", "dispute"),
    ("bill galat hai", "dispute"),
    ("Okay", "chatter"),
    ("Okay sir", "chatter"),
    ("thank you", "chatter"),
    ("சரி", "chatter"),
    ("Welcome to *Shanti Group* 😊 We’re happy to assist you. How can we help?", "auto_greeting"),
    ("Hi Rupeshrtc! I've connected you with our team. They will reply soon.", "auto_greeting"),
    ("💐 Welcome to Naveen Electricals From Padappai", "auto_greeting"),
])
def test_rules(text, intent):
    v = match(text)
    assert v is not None, text
    assert v["intent"] == intent and v["source"] == "rule" and v["confidence"] == 1.0


@pytest.mark.parametrize("text", [
    "5 tareek ko dunga",                         # a date: the model resolves it
    "payment nahi hua abhi",                     # negation: not a paid claim
    "paid 20000 on monday but bill is wrong",    # mixed, with an amount
    "Photos",                                    # unknown: the model decides
    "",
])
def test_rules_defer_to_the_model(text):
    assert match(text) is None


def test_payment_app_text_carries_the_amount():
    v = match("Paid ₹12,500 using Google Pay")
    assert v["intent"] == "paid_claim" and v["amount"] == 12500.0
