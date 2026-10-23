"""Drive the real admin pages in a real browser, as part of the normal suite.

Everything else here tests Python, or parses the emitted JavaScript to prove it
is syntactically valid. Neither catches a page whose script runs and then does
the wrong thing: a row that moves to the wrong tab, a count that drifts, a
failed request that leaves the screen claiming success, or a click that
quietly goes back to reloading 1.5 MB to change one row.

So this renders the page the handler really produces, serves it, and drives it
in headless Chrome over the devtools protocol (no Selenium, no Playwright, no
npm: tests_e2e/run_e2e.js uses the WebSocket client built into Node). Skipped
cleanly when Chrome or Node is missing.
"""
from __future__ import annotations

import http.server
import shutil
import socket
import subprocess
import sys
import threading
import functools
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("weasyprint", MagicMock())

HERE = Path(__file__).parent
RUNNER = HERE / "tests_e2e" / "run_e2e.js"
NODE = shutil.which("node")
CHROME = next((p for p in (
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/usr/bin/google-chrome",
) if Path(p).exists()), None)

pytestmark = pytest.mark.skipif(
    not (NODE and CHROME and RUNNER.exists()),
    reason="needs Node and a local Chrome to drive the page",
)

# ── A small shop, spread across the three tabs ───────────────────────────
BIZ = {
    "id": "b1", "business_name": "Test Traders", "plan": "pro",
    "plan_expires_on": "2027-12-31", "msg_language": "english", "owner_language": "english",
    "reminder_style": "standard", "reminder_hour": 11, "discount_pct": 0,
    "upi_vpa": "shop@ybl", "whatsapp_number": "919000000000",
    "reminder_batches": None, "weekly_off_day": None, "blackout_dates": None,
    "reminder_cadence": None, "overdue_repeat_days": 7, "overdue_max_repeats": 3,
    "catchup_date": None, "catchup_action": None, "wa_ready": True,
    "wa_checked_at": None, "setup_problem": None, "wa_transport": "server",
    "reminder_custom_line": None, "upi_vpa_2": None, "upi_vpa_3": None,
    "bank_account_name": None, "bank_account_no": None, "bank_ifsc": None, "bank_name": None,
}
CLIENTS = [
    dict(id="c1", name="Alpha Traders", whatsapp_number="919111111111", reminders_enabled=True,
         excluded=False, tally_ledger_name="ALPHA", credit_days=30, reminder_batch=0,
         reminder_anchor=None, created_at="2026-01-01"),
    dict(id="c2", name="Beta Electricals", whatsapp_number="919222222222", reminders_enabled=True,
         excluded=False, tally_ledger_name="BETA", credit_days=45, reminder_batch=0,
         reminder_anchor=None, created_at="2026-01-01"),
    dict(id="c3", name="Gamma Manual", whatsapp_number="919333333333", reminders_enabled=True,
         excluded=False, tally_ledger_name="", credit_days=30, reminder_batch=0,
         reminder_anchor=None, created_at="2026-01-01"),
    dict(id="c4", name="Delta Paused", whatsapp_number="919444444444", reminders_enabled=False,
         excluded=True, tally_ledger_name="DELTA", credit_days=30, reminder_batch=0,
         reminder_anchor=None, created_at="2026-01-01"),
]
BILLS = [
    dict(client_id="c1", outstanding=18400, due_date="2026-09-01", invoice_date="2026-08-01"),
    dict(client_id="c2", outstanding=5200, due_date="2026-10-20", invoice_date="2026-09-20"),
    dict(client_id="c3", outstanding=900, due_date="2026-10-30", invoice_date="2026-10-01"),
]

# The browser must not reach a network or raise a modal: both would hang the run.
STUB = """
<script>
window.__E2E = {posts: [], reloaded: false};
window.confirm = function(){ return true; };
window.alert = function(m){ window.__E2E.lastAlert = m; };
window.fetch = function(url, opts){
  window.__E2E.posts.push({url: String(url), body: opts && opts.body});
  return Promise.resolve({ok: true, status: 200, json: () => Promise.resolve({ok: true})});
};
try { Object.defineProperty(window.location, 'reload',
      {configurable: true, value: function(){ window.__E2E.reloaded = true; }}); } catch(e) {}
</script>
"""


class _R:
    def __init__(self, data):
        self.data = data


class _T:
    def __init__(self, name):
        self.name = name

    def __getattr__(self, _):
        return lambda *a, **k: self

    def execute(self):
        return _R({"businesses": [dict(BIZ)],
                   "clients": [dict(c) for c in CLIENTS],
                   "bills": [dict(b) for b in BILLS]}.get(self.name, []))


class FakeDB:
    def table(self, name):
        return _T(name)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """Render the Dashboard the handler really produces and serve it."""
    from app.routers import admin
    admin.require_db = lambda: FakeDB()
    html = admin.admin_page(token="tok", lang="english").body.decode("utf-8")
    html = html.replace("</body>", STUB + "</body>") if "</body>" in html else html + STUB

    root = tmp_path_factory.mktemp("e2e")
    (root / "dashboard.html").write_text(html, encoding="utf-8")

    port = _free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/dashboard.html"
    httpd.shutdown()


def test_dashboard_clicks_behave_in_a_real_browser(served):
    spec = HERE / "tests_e2e" / "dashboard.spec.js"
    r = subprocess.run([NODE, str(RUNNER), served, str(spec)],
                       capture_output=True, text=True, timeout=240)
    print(r.stdout)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert "FAIL" not in r.stdout
    # A silent pass (no assertions ran) would be worse than a failure.
    assert r.stdout.count("PASS") >= 15, r.stdout

@pytest.fixture(scope="module")
def shell_served():
    """Serve the desktop shell itself (it needs a real origin: localStorage,
    which the fold preference uses, is blocked on file://)."""
    root = Path(__file__).parent / "desktop" / "renderer"
    port = _free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/index.html"
    httpd.shutdown()


def test_the_menu_folds_as_one_movement(shell_served):
    """The panel used to animate while everything inside it vanished on the
    first frame. This watches the width and the label opacity frame by frame."""
    spec = HERE / "tests_e2e" / "fold.spec.js"
    r = subprocess.run([NODE, str(RUNNER), shell_served, str(spec)],
                       capture_output=True, text=True, timeout=240)
    print(r.stdout)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert "FAIL" not in r.stdout
    assert r.stdout.count("PASS") >= 10, r.stdout
