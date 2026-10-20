"""Store-forward-delete: once a bill is delivered we drop the stored PDF so no
customer invoice lingers in the bucket. delete_pdf targets the same path
upload_pdf_base64 wrote, and never raises (a failed cleanup can't hurt an
already-sent bill)."""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from app.services import pdf as pdf_service


class _Bucket:
    def __init__(self):
        self.removed = []

    def remove(self, paths):
        self.removed.extend(paths)
        return {"data": paths}


class _Storage:
    def __init__(self, bucket):
        self._bucket = bucket

    def from_(self, name):
        return self._bucket


class _DB:
    def __init__(self, bucket):
        self.storage = _Storage(bucket)


def test_delete_pdf_removes_the_stored_object(monkeypatch):
    bucket = _Bucket()
    monkeypatch.setattr(pdf_service, "require_db", lambda: _DB(bucket))
    pdf_service.delete_pdf("bill-123", "INV-9")
    assert bucket.removed == ["bill-123/INV-9.pdf"]


def test_delete_pdf_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("storage down")
    monkeypatch.setattr(pdf_service, "require_db", boom)
    # must swallow - a cleanup failure cannot break an already-delivered bill
    pdf_service.delete_pdf("bill-123", "INV-9")


def test_upload_returns_a_signed_url_never_a_public_one(monkeypatch):
    import asyncio
    import base64
    from app.services import pdf

    class Bucket:
        def upload(self, *a, **k): return None
        def create_signed_url(self, path, seconds):
            assert seconds == pdf.SIGNED_URL_SECONDS
            return {"signedURL": f"https://x/storage/v1/object/sign/invoices/{path}?token=t"}
        def get_public_url(self, path): raise AssertionError("public URL must not be used")

    class Storage:
        def from_(self, name): return Bucket()
        def get_bucket(self, name): return {}

    db = type("DB", (), {"storage": Storage()})()
    monkeypatch.setattr(pdf, "require_db", lambda: db)
    url = asyncio.run(pdf.upload_pdf_base64("b1", "INV1", base64.b64encode(b"%PDF").decode()))
    assert "/sign/" in url and "token=" in url
