"""The Command Center must surface a pairing code, because that is now the whole
onboarding flow: the operator reads a short code aloud and the shop types it.

Covers the Add-business result panel (code is the hero, token demoted to an
Advanced section) and the per-shop "Get code" re-pair button, which is how an
existing shop moves onto a fresh install without losing its DB-stored reminders.
"""
import asyncio
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.routers import ops


def _render(monkeypatch) -> str:
    monkeypatch.setattr(ops.settings, "admin_api_key", "testkey")
    resp = asyncio.run(ops.ops_page(key="testkey"))
    return resp.body.decode()


def test_add_business_result_leads_with_the_pairing_code(monkeypatch):
    html = _render(monkeypatch)
    assert 'id="r_code"' in html                 # code element exists
    assert "pairing_code_display" in html        # populated from the API response
    # the raw token is no longer the headline - it sits under Advanced
    assert "Advanced: manual setup" in html
    # the old token-gated download link is gone from the onboarding path
    assert "ASVA_shop.zip?token=" not in html


def test_repair_button_and_modal_exist(monkeypatch):
    html = _render(monkeypatch)
    assert "async function pairCode" in html
    assert "/license/mint-code" in html
    assert 'id="pairModal"' in html
    assert ">Get code<" in html
    assert "<th>Pair</th>" in html


def test_subscriptions_table_colspan_matches_column_count(monkeypatch):
    """The empty-state colspan must match the subscriptions header exactly, or the
    table renders ragged the first time an operator opens a fresh install. Tied to
    the ACTUAL subs column count so it can never go stale when a column is added."""
    html = _render(monkeypatch)
    # Isolate the subscriptions table header (between its tab and its tbody).
    subs = html.split('<tbody id="rows">')[0].split('<div id="tab_subs"')[-1]
    n_cols = subs.count("<th>") + subs.count('<th class="num">')
    assert n_cols >= 12          # Business..Delete
    # The empty-state row must span every subscriptions column.
    assert 'colspan="%d"' % n_cols in html


def test_pair_flow_is_admin_gated_end_to_end(monkeypatch):
    """The page is useless to anyone without the admin key - minting a code
    goes through /license/mint-code, which refuses a bad key."""
    from app.routers import license as lr
    from fastapi import HTTPException
    monkeypatch.setattr(lr.settings, "admin_api_key", "s3cret")
    try:
        asyncio.run(lr.mint_code(lr.MintCodePayload(admin_key="nope", business_id="b1")))
        assert False, "should refuse"
    except HTTPException as e:
        assert e.status_code == 401
