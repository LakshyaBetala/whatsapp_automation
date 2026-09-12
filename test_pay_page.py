"""The hosted pay page: every pending bill, part payment presets, honest copy.

A part payment must never read as "settled", the customer must be able to say
WHICH bill the money is for (Contract Act s.59, carried in the UPI note), and
the page is a reminder, never a legal notice.
"""
import re
import shutil
import subprocess
from decimal import Decimal

import pytest

from app.routers import pay
from app.services import upi

BIZ = "94e2e0a7-2cc9-4bd4-b5a2-eca6ce924d17"
CID = "5a1c2d3e-4f50-4617-8899-aabbccddeeff"

BUSINESS = {
    "business_name": "Demo Traders", "upi_vpa": "demo@okhdfcbank",
    "bank_account_name": "Demo Traders", "bank_account_no": "1234567890",
    "bank_ifsc": "HDFC0000123", "bank_name": "HDFC Bank",
}
BILLS = [
    {"invoice_number": "2526RTC0071", "outstanding": 9475.0,
     "invoice_date": "2026-07-10", "due_date": "2026-08-09"},
    {"invoice_number": "2526RTC0088", "outstanding": 1500.5,
     "invoice_date": "2026-08-30", "due_date": "2099-01-01"},
]


class _Resp:
    def __init__(self, data): self.data = data


class _Tbl:
    def __init__(self, name, sink): self.name, self.sink, self._ins = name, sink, None

    def insert(self, row): self._ins = row; return self

    def __getattr__(self, _): return lambda *a, **k: self

    def execute(self):
        if self._ins is not None:
            self.sink.append((self.name, self._ins)); return _Resp([self._ins])
        if self.name == "businesses": return _Resp([dict(BUSINESS)])
        if self.name == "bills": return _Resp([dict(b) for b in BILLS])
        return _Resp([])


class FakeDB:
    def __init__(self): self.writes = []
    def table(self, name): return _Tbl(name, self.writes)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(pay, "get_client", lambda: fake)
    return fake


def _html(resp):
    assert resp.status_code == 200, resp.status_code
    return resp.body.decode("utf-8")


def _page(db, amount="10975.50", client=CID, ref="2 bills"):
    tok = upi.pay_token(BIZ, Decimal(amount), client)
    return tok, _html(pay.pay_page(tok, ref=ref))


def test_every_pending_bill_is_listed_and_tickable(db):
    _, html = _page(db)
    assert html.count('class="row bill"') == 2
    assert 'data-p="947500" data-n="2526RTC0071"' in html
    assert 'data-p="150050"' in html          # paise, so nothing rounds away
    assert "overdue" in html                    # factual status per bill
    assert "Untick a bill" in html


def test_part_payment_presets_and_custom_box(db):
    _, html = _page(db)
    for p in ("10%", "25%", "50%", "Full"):
        assert ">" + p + "<" in html
    assert 'id="cust"' in html and 'inputmode="numeric"' in html
    assert 'id="bal"' in html


def test_copy_is_honest_and_not_a_notice(db):
    _, html = _page(db)
    assert "not a legal notice" in html
    assert "never collects or holds your money" in html
    assert "stays open" in html                 # part payment wording lives in the JS
    for banned in ("settled", "cleared", "interest", "penalty", "legal action"):
        assert banned not in html.lower(), banned
    assert "—" not in html and "–" not in html   # no em/en dashes


def test_upi_note_names_the_bill_so_the_customer_can_appropriate(db):
    _, html = _page(db)
    assert "'INV ' + t[0].getAttribute('data-n')" in html
    assert "' part'" in html


def test_pay_page_script_is_valid_javascript(db, tmp_path):
    _, html = _page(db)
    js = "\n;\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    assert js.strip()
    assert not re.findall(r"=\s*(True|False|None)\s*[;,)]", js)
    assert '"softLimit": 100000' in js
    node = shutil.which("node")
    if not node:
        return
    f = tmp_path / "pay.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:800]


def test_link_without_customer_still_offers_presets(db):
    tok = upi.pay_token(BIZ, Decimal("500"))
    html = _html(pay.pay_page(tok, ref="INV 12"))
    assert 'class="row bill"' not in html
    assert ">Full<" in html and '"total": 500.0' in html


def test_tap_records_the_amount_the_customer_chose(db):
    tok = upi.pay_token(BIZ, Decimal("10975.50"), CID)
    assert pay.pay_tapped(tok, a="2500.00") == {"ok": True}
    kinds = [(t, r["kind"], r["amount"]) for t, r in db.writes]
    assert ("pay_link_events", "tapped", 2500.0) in kinds


def test_tap_with_junk_amount_falls_back_to_the_reminder_amount(db):
    tok = upi.pay_token(BIZ, Decimal("10975.50"), CID)
    pay.pay_tapped(tok, a="-5")
    pay.pay_tapped(tok, a="abc")
    amounts = [r["amount"] for _, r in db.writes if r["kind"] == "tapped"]
    assert amounts == [10975.5, 10975.5]


def test_tap_on_a_forged_token_records_nothing(db):
    assert pay.pay_tapped("xxxx", a="1") == {"ok": False}
    assert db.writes == []


def test_qr_endpoint_serves_a_png_for_the_chosen_amount(db):
    pytest.importorskip("qrcode")
    tok = upi.pay_token(BIZ, Decimal("10975.50"), CID)
    r = pay.pay_qr(tok, a="2500", n="INV 2526RTC0071 part")
    assert r.status_code == 200 and r.media_type == "image/png"
    assert r.body[:8] == b"\x89PNG\r\n\x1a\n"


def test_qr_endpoint_rejects_forged_tokens(db):
    assert pay.pay_qr("nope", a="1").status_code == 404


def test_bill_status_is_factual():
    from datetime import date
    today = date(2026, 9, 12)
    text, late = pay._bill_status({"due_date": "2026-08-09"}, today)
    assert text == "Due 09 Aug, 34 days overdue" and late
    text, late = pay._bill_status({"due_date": "2026-09-12"}, today)
    assert "due today" in text and not late
    text, late = pay._bill_status({"invoice_date": "2026-09-01"}, today)
    assert text == "Billed 01 Sep" and not late
