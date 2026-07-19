"""Client-side pairing: the shop types a code, the install configures itself.

The critical property is that re-pairing an EXISTING install must not destroy
local settings (Tally company, export folder) - that is the cutover path for a
shop already running an older version.
"""
import io
import json
import os
import sys
import urllib.error
from unittest.mock import MagicMock

sys.modules.setdefault("weasyprint", MagicMock())

from tally_agent import pair


class _Resp(io.BytesIO):
    """Minimal stand-in for the urlopen context manager."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _ok_response(payload):
    return lambda req, timeout=None: _Resp(json.dumps(payload).encode())


PAIRED = {"ok": True, "business_id": "biz-1", "agent_token": "tok-secret",
          "business_name": "RISHAB TRADING COMPANY", "license_key": "ASVA-1-2-3"}


# ── redeem ────────────────────────────────────────────────────────────────
def test_redeem_returns_identity(monkeypatch):
    monkeypatch.setattr(pair.urllib.request, "urlopen", _ok_response(PAIRED))
    out = pair.redeem("K7P2-9M4T")
    assert out["agent_token"] == "tok-secret"
    assert out["business_id"] == "biz-1"


def test_redeem_blank_code_is_rejected_before_any_network():
    for bad in ("", "   ", None):
        try:
            pair.redeem(bad)
            assert False, "should refuse"
        except pair.PairError as e:
            assert "setup code" in str(e).lower()


def test_redeem_surfaces_server_message(monkeypatch):
    """A 400 from /license/pair carries a human message; show it verbatim."""
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            "u", 400, "Bad Request", {},
            io.BytesIO(json.dumps({"detail": "This code was already used. Ask for a fresh one."}).encode()))
    monkeypatch.setattr(pair.urllib.request, "urlopen", boom)
    try:
        pair.redeem("K7P29M4T")
        assert False
    except pair.PairError as e:
        assert str(e) == "This code was already used. Ask for a fresh one."


def test_redeem_network_failure_is_plain_language(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("dns go boom")
    monkeypatch.setattr(pair.urllib.request, "urlopen", boom)
    try:
        pair.redeem("K7P29M4T")
        assert False
    except pair.PairError as e:
        msg = str(e)
        assert "internet" in msg.lower()
        assert "boom" not in msg          # never leak the raw error at a shopkeeper


def test_redeem_rejects_incomplete_server_answer(monkeypatch):
    monkeypatch.setattr(pair.urllib.request, "urlopen", _ok_response({"ok": True}))
    try:
        pair.redeem("K7P29M4T")
        assert False
    except pair.PairError as e:
        assert "fresh code" in str(e).lower()


# ── write_config ──────────────────────────────────────────────────────────
def test_write_config_creates_a_usable_config(tmp_path):
    p = str(tmp_path / "config.json")
    cfg = pair.write_config(PAIRED, backend_url="https://app.tryasva.com/", path=p)
    assert cfg["business_id"] == "biz-1"
    assert cfg["agent_token"] == "tok-secret"
    assert cfg["backend_url"] == "https://app.tryasva.com"     # trailing slash trimmed
    assert cfg["tally_host"] == "localhost" and cfg["tally_port"] == 9000
    on_disk = json.load(open(p, encoding="utf-8"))
    assert on_disk == cfg


def test_repairing_preserves_local_settings(tmp_path):
    """THE cutover guarantee: re-pairing an existing shop keeps its Tally
    company and export folder, and only swaps the identity."""
    p = str(tmp_path / "config.json")
    json.dump({"company_name": "RISHAB TRADING COMPANY",
               "bill_pdf_dir": "C:\\ASVA\\bills",
               "tally_port": 9001,
               "business_id": "OLD", "agent_token": "OLD-TOKEN"},
              open(p, "w", encoding="utf-8"))

    cfg = pair.write_config(PAIRED, path=p)

    assert cfg["company_name"] == "RISHAB TRADING COMPANY"   # kept
    assert cfg["bill_pdf_dir"] == "C:\\ASVA\\bills"          # kept
    assert cfg["tally_port"] == 9001                         # kept, not defaulted
    assert cfg["business_id"] == "biz-1"                     # swapped
    assert cfg["agent_token"] == "tok-secret"                # swapped


def test_write_config_survives_a_corrupt_existing_file(tmp_path):
    p = str(tmp_path / "config.json")
    open(p, "w", encoding="utf-8").write("{not json at all")
    cfg = pair.write_config(PAIRED, path=p)
    assert cfg["business_id"] == "biz-1"      # corrupt config must not block setup


def test_write_config_is_atomic(tmp_path):
    p = str(tmp_path / "config.json")
    pair.write_config(PAIRED, path=p)
    assert not os.path.exists(p + ".tmp")     # no half-written leftovers


def test_company_name_can_be_set_at_pair_time(tmp_path):
    p = str(tmp_path / "config.json")
    cfg = pair.write_config(PAIRED, company_name="ACME EXPORTS", path=p)
    assert cfg["company_name"] == "ACME EXPORTS"


# ── Tally company listing (the picker) ────────────────────────────────────
def test_list_companies_gives_plain_advice_when_tally_is_closed(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(pair.urllib.request, "urlopen", boom)
    try:
        pair.list_tally_companies()
        assert False
    except pair.PairError as e:
        msg = str(e)
        assert "Tally" in msg and "9000" in msg      # actionable, not a stack trace


def test_list_companies_parses_tally_response(monkeypatch):
    xml = ("<ENVELOPE><BODY><DATA><COLLECTION>"
           "<COMPANY><NAME>RISHAB TRADING COMPANY</NAME></COMPANY>"
           "<COMPANY><NAME>ACME EXPORTS</NAME></COMPANY>"
           "</COLLECTION></DATA></BODY></ENVELOPE>")
    monkeypatch.setattr(pair.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(xml.encode()))
    names = pair.list_tally_companies()
    assert "RISHAB TRADING COMPANY" in names
    assert len(names) == 2


# ── CLI ───────────────────────────────────────────────────────────────────
def test_cli_reports_failure_without_traceback(monkeypatch, capsys):
    def boom(req, timeout=None):
        raise OSError("nope")
    monkeypatch.setattr(pair.urllib.request, "urlopen", boom)
    rc = pair.main(["K7P29M4T"])
    assert rc == 1
    assert "Setup failed" in capsys.readouterr().out
