"""Reminder-batch payment resolution (B5): each batch picks which UPI/bank
account its reminders lead with, and bank details ride along when configured."""
from app.services import batches as B
from app.services.batches import BANK_SENTINEL


BIZ = {
    "upi_vpa": "shop@okhdfc",
    "upi_vpa_2": "shop2@okaxis",
    "upi_vpa_3": "",
    "bank_account_name": "RISHAB TRADING COMPANY",
    "bank_account_no": "123456789012",
    "bank_ifsc": "HDFC0000123",
    "bank_name": "HDFC Bank",
    "msg_language": "english",
}


def test_batch_vpa_default_is_shop_upi():
    assert B.batch_vpa(BIZ, {"upi": ""}) == "shop@okhdfc"


def test_batch_vpa_picks_selected_upi():
    assert B.batch_vpa(BIZ, {"upi": "shop2@okaxis"}) == "shop2@okaxis"


def test_batch_vpa_bank_sentinel_falls_back_to_default_upi():
    # A bank-collecting batch still offers UPI via the shop default (never the sentinel).
    assert B.batch_vpa(BIZ, {"upi": BANK_SENTINEL}) == "shop@okhdfc"


def test_bank_details_needs_account_number():
    assert B.bank_details({"bank_account_no": ""}) is None
    b = B.bank_details(BIZ)
    assert b and b["no"] == "123456789012" and b["ifsc"] == "HDFC0000123"


def test_payment_upi_primary_by_default():
    pay = B.batch_payment(BIZ, {"upi": ""})
    assert pay["primary"] == "upi"
    assert pay["vpa"] == "shop@okhdfc"
    assert pay["bank"]["no"] == "123456789012"   # bank still rides along


def test_payment_bank_primary_on_sentinel():
    pay = B.batch_payment(BIZ, {"upi": BANK_SENTINEL})
    assert pay["primary"] == "bank"
    assert pay["vpa"] == "shop@okhdfc"            # UPI still offered
    assert pay["bank"]["name"] == "RISHAB TRADING COMPANY"


def test_payment_no_bank_configured():
    biz = {"upi_vpa": "shop@okhdfc"}
    pay = B.batch_payment(biz, {"upi": ""})
    assert pay["primary"] == "upi" and pay["bank"] is None


def test_normalize_keeps_bank_sentinel():
    nb = B.normalize_batch({"name": "NEFT", "lang": "english", "upi": BANK_SENTINEL})
    assert nb["upi"] == BANK_SENTINEL
