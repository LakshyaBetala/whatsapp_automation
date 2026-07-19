"""The installer must be publicly downloadable; the legacy zip must not be.

ASVA-Setup.exe carries no secret (no DB key, no token, no config) - it learns
which shop it is only when the owner types a pairing code. Gating it would just
break the website's Download button. ASVA_shop.zip still ships the service-role
key, so it stays token-gated.
"""
import sys
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

sys.modules.setdefault("weasyprint", MagicMock())

from app.routers import downloads


def test_installer_is_public_and_zip_is_gated():
    assert "ASVA-Setup.exe" in downloads.PUBLIC
    assert "ASVA_shop.zip" not in downloads.PUBLIC
    assert "ASVA-Setup.exe" in downloads.ALLOWED


def test_zip_without_token_is_refused(monkeypatch):
    monkeypatch.setattr(downloads, "_token_ok", lambda t: False)
    with pytest.raises(HTTPException) as e:
        downloads.download_file("ASVA_shop.zip", token="")
    assert e.value.status_code == 403


def test_installer_without_token_gets_past_auth(monkeypatch):
    """No token given, yet we must NOT get 403. With the file absent the honest
    answer is 404 (not published yet), which proves auth was not the blocker."""
    monkeypatch.setattr(downloads, "_token_ok", lambda t: False)
    monkeypatch.setattr(downloads.os.path, "exists", lambda p: False)
    with pytest.raises(HTTPException) as e:
        downloads.download_file("ASVA-Setup.exe", token="")
    assert e.value.status_code == 404


def test_unknown_download_is_404():
    with pytest.raises(HTTPException) as e:
        downloads.download_file("secrets.env", token="anything")
    assert e.value.status_code == 404
