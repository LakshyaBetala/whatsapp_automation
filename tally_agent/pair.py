"""Pairing this install to its business - the whole of shop onboarding.

The operator mints a short code in the Command Center and reads it aloud. This
module redeems that code against the server, receives the business identity and
its agent_token, and writes config.json. The owner never types a token, and the
installer ships no secret: an unpaired install knows nothing and can do nothing.

Re-pairing is the safe cutover path. A code minted for an EXISTING business
binds this install to that same business_id, so every reminder and setting
(which lives server-side in the database) carries over untouched. write_config
also preserves any local settings already on the machine, so re-pairing an
existing install keeps its Tally company and export folder.

    python tally_agent/pair.py K7P2-9M4T
    python tally_agent/pair.py K7P2-9M4T --company "RISHAB TRADING COMPANY"
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BACKEND = "https://app.tryasva.com"
DEFAULT_TALLY_HOST = "localhost"
DEFAULT_TALLY_PORT = 9000


class PairError(Exception):
    """Pairing failed, with a message safe to show the shop owner as-is."""


def default_config_path() -> str:
    """config.json sits next to the .exe (PyInstaller) or this file, so it is
    found no matter which directory the app was launched from."""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "config.json")


def _detail_from_http_error(err: urllib.error.HTTPError) -> str:
    """FastAPI puts the human message in {"detail": ...}; fall back to a plain
    sentence rather than leaking a stack trace at a shopkeeper."""
    try:
        body = json.loads(err.read().decode("utf-8", "replace"))
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    except Exception:
        pass
    if err.code == 400:
        return "That code is not valid. Please check it and try again."
    return "The server could not complete setup. Please try again."


def redeem(code: str, backend_url: str = DEFAULT_BACKEND, timeout: int = 30) -> dict:
    """Exchange a one-time pairing code for this shop's identity + token."""
    cleaned = str(code or "").strip()
    if not cleaned:
        raise PairError("Please enter your setup code.")
    base = (backend_url or DEFAULT_BACKEND).rstrip("/")
    payload = json.dumps({"code": cleaned}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/license/pair", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise PairError(_detail_from_http_error(e))
    except Exception:
        raise PairError("Could not reach the ASVA server. Check the internet "
                        "connection and try again.")
    if not data.get("agent_token") or not data.get("business_id"):
        raise PairError("Setup did not complete. Please ask for a fresh code.")
    return data


def list_tally_companies(host: str = DEFAULT_TALLY_HOST, port: int = DEFAULT_TALLY_PORT,
                         timeout: int = 20) -> list:
    """Companies currently open in Tally, so the owner TAPS theirs instead of
    typing an exact name (a mistyped company name is the classic setup failure).
    Raises PairError with a plain-language message when Tally is not reachable."""
    try:                                     # works as a package or as a script
        from tally_agent import tally_xml
    except ImportError:
        import tally_xml                     # type: ignore

    body = tally_xml.build_company_list_query().encode("utf-8")
    req = urllib.request.Request(f"http://{host}:{port}", data=body,
                                 headers={"Content-Type": "text/xml"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:
        raise PairError(
            "Could not reach Tally. Open TallyPrime, then press Retry. "
            "(In Tally: F1 Help > Settings > Advanced Configuration, "
            "turn ON the HTTP server on port 9000.)")
    return tally_xml.parse_companies(tally_xml.sanitize_xml(raw))


def write_config(paired: dict, *, backend_url: str = DEFAULT_BACKEND,
                 company_name: str | None = None, path: str | None = None) -> dict:
    """Write config.json from a successful pairing, PRESERVING anything already
    configured on this machine (Tally company, export folder, host/port). That
    is what makes re-pairing an existing shop safe."""
    path = path or default_config_path()
    existing: dict = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f) or {}
        except Exception:
            existing = {}          # a corrupt config must never block setup

    cfg = dict(existing)
    cfg["backend_url"] = (backend_url or DEFAULT_BACKEND).rstrip("/")
    cfg["business_id"] = paired["business_id"]
    cfg["agent_token"] = paired["agent_token"]
    if paired.get("business_name"):
        cfg["business_name"] = paired["business_name"]
    if company_name:
        cfg["company_name"] = company_name
    cfg.setdefault("company_name", "")
    cfg.setdefault("tally_host", DEFAULT_TALLY_HOST)
    cfg.setdefault("tally_port", DEFAULT_TALLY_PORT)
    cfg.setdefault("watch_interval_seconds", 300)
    cfg.setdefault("folder_poll_seconds", 8)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)          # atomic: never leave a half-written config
    return cfg


def pair_and_write(code: str, *, backend_url: str = DEFAULT_BACKEND,
                   company_name: str | None = None, path: str | None = None) -> dict:
    """Redeem a code and persist the result. Returns the written config."""
    return write_config(redeem(code, backend_url), backend_url=backend_url,
                        company_name=company_name, path=path)


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: pair.py <SETUP-CODE> [--company NAME] [--backend URL]")
        return 0
    code = argv[0]
    company = backend = None
    for i, a in enumerate(argv):
        if a == "--company" and i + 1 < len(argv):
            company = argv[i + 1]
        if a == "--backend" and i + 1 < len(argv):
            backend = argv[i + 1]
    try:
        cfg = pair_and_write(code, backend_url=backend or DEFAULT_BACKEND,
                             company_name=company)
    except PairError as e:
        print(f"Setup failed: {e}")
        return 1
    print(f"Connected to {cfg.get('business_name') or cfg['business_id']}.")
    if not cfg.get("company_name"):
        print("Next: choose your Tally company.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
