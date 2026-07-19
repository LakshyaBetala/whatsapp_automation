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
