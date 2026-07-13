// ASVA Desktop - the control center.
// Supervises the backend + both WhatsApp services + the Tally watcher as
// child processes (replacing START.bat and its cmd windows), and shows the
// dashboard / QR setup / status in one window with a tray icon.
const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const os = require('os');
const path = require('path');
const fs = require('fs');

const REPO = path.join(__dirname, '..'); // desktop/ lives inside the repo root
let mainWindow = null;
let tray = null;
app.isQuitting = false;

// ── Anti-blank-screen hardening ───────────────────────────────────────────
// Budget shop laptops have flaky GPU drivers; hardware-accelerated Electron
// often paints a WHITE/BLACK blank window there. Software rendering is a hair
// slower but rock-solid, which is what a till machine needs. This is the single
// biggest fix for "the app goes blank".
app.disableHardwareAcceleration();
// Don't let a crashed GPU process take the window down - keep painting.
app.commandLine.appendSwitch('disable-gpu-compositing');
// A renderer that crashes should be relaunched, not left blank.
process.on('uncaughtException', (e) => console.error('[main] uncaughtException:', (e && e.message) || e));
process.on('unhandledRejection', (e) => console.error('[main] unhandledRejection:', (e && e.message) || e));

// Any webview whose render process dies gets reloaded automatically (belt-and-
// suspenders alongside the per-webview handlers in the renderer).
app.on('web-contents-created', (e, contents) => {
  const kick = () => { try { if (!contents.isDestroyed()) contents.reload(); } catch (_) {} };
  contents.on('render-process-gone', kick);
  contents.on('unresponsive', kick);
});

// ── Config: token + backend URL come from Asva/config.json ────────────────
// Multi-company: the primary company is the top-level token; extra Tally
// companies (added via `agent --add-company`) live in config.companies[].
// Each has its OWN token = its own isolated data. The renderer shows a
// dropdown when there is more than one.
function pageUrls(backend, token) {
  const q = `?token=${encodeURIComponent(token)}`;
  return {
    dashboardUrl: `${backend}/admin${q}`,
    remindersUrl: `${backend}/admin/reminders${q}`,
    analyticsUrl: `${backend}/admin/analytics${q}`,
    accountsUrl: `${backend}/admin/accounts${q}`,
  };
}
function loadConfig() {
  let token = '';
  let backend = 'http://localhost:8000';
  let companies = [];
  try {
    const c = JSON.parse(fs.readFileSync(path.join(REPO, 'Asva', 'config.json'), 'utf8'));
    token = c.agent_token || '';
    if (c.backend_url) backend = c.backend_url.replace(/\/+$/, '');
    const primaryName = c.company_name || c.business_name || 'Company 1';
    if (token) companies.push({ name: primaryName, token });
    for (const extra of (c.companies || [])) {
      if (extra && extra.agent_token && extra.company_name
          && extra.company_name !== primaryName) {
        companies.push({ name: extra.company_name, token: extra.agent_token });
      }
    }
  } catch (e) {
    // config missing/invalid - dashboard tab will show a helpful message
  }
  const base = token ? pageUrls(backend, token) : {
    dashboardUrl: '', remindersUrl: '', analyticsUrl: '', accountsUrl: '',
  };
  return {
    token,
    backendUrl: backend,
    companies: companies.map((co) => ({ name: co.name, ...pageUrls(backend, co.token) })),
    ...base,
    waShopUrl: 'http://localhost:3001/qr',
  };
}
const CONFIG = loadConfig();

// ── Child-process supervision ─────────────────────────────────────────────
// Use the .venv Python that SETUP.bat built (3.11-3.13). A bare `python` on the
// machine may be 3.14, which has no pydantic-core wheel and fails to import.
const VENV_PY = path.join(REPO, '.venv', 'Scripts', 'python.exe');
const PY = fs.existsSync(VENV_PY) ? VENV_PY : 'python';
// ONE number, one wa_service instance:
//   whatsapp (3001) = the shop's own number  -> bills, reminders, customer replies
const SPECS = {
  backend: { cmd: PY, args: ['-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000'], cwd: REPO, env: {} },
  whatsapp: { cmd: 'node', args: ['index.js'], cwd: path.join(REPO, 'wa_service'), env: { PORT: '3001', SESSION_ID: 'default', WA_CHANNEL: 'shop' } },
  watcher: { cmd: PY, args: ['-u', 'tally_agent/agent.py', '--watch'], cwd: REPO, env: {} },
};
const services = {}; // name -> { proc, restarts }
const logs = {};     // name -> ring buffer of recent output lines

function sendToWindow(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function startService(name) {
  const spec = SPECS[name];
  if (!spec) return;
  if (!fs.existsSync(spec.cwd)) {
    sendToWindow('log', { name, line: `[${name}] folder missing: ${spec.cwd}\n` });
    return;
  }
  let proc;
  try {
    proc = spawn(spec.cmd, spec.args, {
      cwd: spec.cwd,
      env: { ...process.env, ...spec.env },
      windowsHide: true,
    });
  } catch (e) {
    sendToWindow('log', { name, line: `[${name}] failed to start: ${e.message}\n` });
    return;
  }
  services[name] = services[name] || { restarts: 0 };
  services[name].proc = proc;
  logs[name] = logs[name] || [];

  const push = (buf) => {
    const s = buf.toString();
    logs[name].push(s);
    if (logs[name].length > 250) logs[name].shift();
    sendToWindow('log', { name, line: s });
  };
  proc.stdout.on('data', push);
  proc.stderr.on('data', push);
  proc.on('error', (e) => push(Buffer.from(`[${name}] ${e.message}\n`)));
  services[name].startedAt = Date.now();
  if (name === 'watcher') watcherSyncSeenAt = Date.now(); // give a fresh window
  proc.on('exit', (code) => {
    push(Buffer.from(`\n[${name} stopped, code ${code}]\n`));
    services[name].proc = null;
    if (!app.isQuitting) {
      services[name].restarts = (services[name].restarts || 0) + 1;
      // Quick-crash backoff: a process that dies within 15s of starting is
      // looping on a real error - back off up to 30s so we don't spin at 4s
      // forever (and the log stays readable). A healthy long run resets it.
      const ranMs = Date.now() - (services[name].startedAt || 0);
      const quick = ranMs < 15000;
      const delay = quick ? Math.min(4000 * services[name].restarts, 30000) : 4000;
      if (!quick) services[name].restarts = 0;
      setTimeout(() => startService(name), delay);
    }
  });
}

// ── Watcher stall watchdog ────────────────────────────────────────────────
// The Tally watcher posts a heartbeat every ~60s (even with no new data), so
// the backend's last_synced_at advances whenever the watcher is alive AND
// reaching Tally. If it STOPS advancing while the backend is up and the
// watcher process is still running, the watcher has hung (the "synced 15h ago
// while Tally was on" bug) - kill it so it respawns fresh. Bounded to at most
// one restart per cooldown so a legitimately-off Tally doesn't cause churn.
let watcherSyncValue = null;      // last last_synced_at we saw
let watcherSyncSeenAt = Date.now(); // wall time it last CHANGED
let watcherLastKick = 0;          // wall time we last restarted it
const SYNC_STALL_MS = 7 * 60 * 1000;
const WATCHER_KICK_COOLDOWN_MS = 10 * 60 * 1000;

function noteSyncValue(lastSyncedAt) {
  if (lastSyncedAt && lastSyncedAt !== watcherSyncValue) {
    watcherSyncValue = lastSyncedAt;
    watcherSyncSeenAt = Date.now();
  }
}

function maybeKickStalledWatcher(backendOk) {
  if (!backendOk || !CONFIG.token) return;   // backend down / no company: not the watcher
  const proc = services.watcher && services.watcher.proc;
  if (!proc) return;                          // already (re)starting
  const now = Date.now();
  if (now - watcherSyncSeenAt < SYNC_STALL_MS) return;        // sync is fresh
  if (now - watcherLastKick < WATCHER_KICK_COOLDOWN_MS) return; // one kick per cooldown
  watcherLastKick = now;
  watcherSyncSeenAt = now;   // give the fresh watcher a full window before judging again
  sendToWindow('log', { name: 'watcher',
    line: '\n[watchdog] Tally sync stalled 7+ min - restarting the Tally watcher.\n' });
  try { proc.kill(); } catch (e) { /* exit handler respawns it */ }
}

function startAll() { Object.keys(SPECS).forEach(startService); }

function stopAll() {
  app.isQuitting = true;
  for (const name of Object.keys(services)) {
    const p = services[name] && services[name].proc;
    if (p) { try { p.kill(); } catch (e) { /* ignore */ } }
  }
}

// ── Health polling (done in main to avoid renderer CORS) ──────────────────
function ping(url, cb) {
  let req;
  try {
    req = http.get(url, { timeout: 2500 }, (res) => {
      let body = '';
      res.on('data', (d) => (body += d));
      res.on('end', () => cb(res.statusCode === 200, body));
    });
  } catch (e) { return cb(false, ''); }
  req.on('error', () => cb(false, ''));
  req.on('timeout', () => { req.destroy(); cb(false, ''); });
}

// POST JSON to the backend (used by the top-bar Reload button -> /admin/reload).
function httpPostJson(url, bodyObj, cb) {
  let data;
  try { data = Buffer.from(JSON.stringify(bodyObj)); } catch (e) { return cb(false, {}); }
  let req;
  try {
    const u = new URL(url);
    req = http.request({
      hostname: u.hostname, port: u.port || 80, path: u.pathname + u.search,
      method: 'POST', timeout: 8000,
      headers: { 'Content-Type': 'application/json', 'Content-Length': data.length },
    }, (res) => {
      let body = '';
      res.on('data', (d) => (body += d));
      res.on('end', () => { let j = {}; try { j = JSON.parse(body); } catch (e) {} cb(res.statusCode === 200, j); });
    });
  } catch (e) { return cb(false, {}); }
  req.on('error', () => cb(false, {}));
  req.on('timeout', () => { req.destroy(); cb(false, {}); });
  req.write(data); req.end();
}

function parseWa(ok, body) {
  if (!ok) return { reachable: false, ready: false, qr: null };
  try {
    const d = JSON.parse(body);
    return { reachable: true, ready: !!d.ready, qr: d.qr || null };
  } catch (e) {
    return { reachable: true, ready: false, qr: null };
  }
}

// ── License heartbeat ─────────────────────────────────────────────────────
// Report this shop as alive to the central Command Center: machine + build
// version (so the operator sees who's on an old version) and pull back the
// subscription state. Liveness is ALSO covered by /tally/sync every ~60s; this
// adds the version/machine and keeps last_seen fresh even if Tally is closed.
let SERVER_VERSION = '';
function sendHeartbeat() {
  if (!CONFIG.token) return;
  try {
    httpPostJson(`${CONFIG.backendUrl}/license/heartbeat`, {
      agent_token: CONFIG.token,
      machine_id: os.hostname(),
      agent_version: SERVER_VERSION || undefined,
    }, () => { /* fire and forget */ });
  } catch (e) { /* never let a heartbeat crash the app */ }
}

function pollStatus() {
  const out = {};
  let pending = 3;
  const done = () => { if (--pending === 0) sendToWindow('status', out); };
  ping(`${CONFIG.backendUrl}/health`, (ok, b) => {
    out.backend = ok;
    if (ok) { try { const v = JSON.parse(b).version; if (v) SERVER_VERSION = v; } catch (e) {} }
    done();
  });
  ping('http://localhost:3001/api/wa/status', (ok, b) => {
    const w = parseWa(ok, b);
    out.whatsapp = w.ready; out.whatsappReachable = w.reachable; out.whatsappQr = w.qr;
    done();
  });
  // Tally sync freshness for the top bar (label + dot colour + last-sync ISO).
  const su = CONFIG.token
    ? `${CONFIG.backendUrl}/admin/sync-status?token=${encodeURIComponent(CONFIG.token)}`
    : `${CONFIG.backendUrl}/health`;
  ping(su, (ok, b) => {
    if (ok) { try { const d = JSON.parse(b);
      out.tallyLabel = d.tally_label; out.tallyColor = d.tally_color;
      out.lastSyncedLabel = d.last_synced_label; out.lastSyncedAt = d.last_synced_at;
      noteSyncValue(d.last_synced_at);
    } catch (e) {} }
    // ok here means the backend answered sync-status = backend is up.
    maybeKickStalledWatcher(ok);
    done();
  });
}

// ── Window + tray ─────────────────────────────────────────────────────────
function trayIcon() {
  const p = path.join(__dirname, 'icon.png');
  if (fs.existsSync(p)) return nativeImage.createFromPath(p);
  return nativeImage.createEmpty();
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 800,
    title: 'ASVA',
    icon: trayIcon(),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      webviewTag: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.setMenuBarVisibility(false);
  const loadUI = () => mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  loadUI();
  // If the whole UI process dies or hangs (the "everything goes blank" case),
  // rebuild it automatically instead of leaving a blank window.
  mainWindow.webContents.on('render-process-gone', (e, details) => {
    console.error('[main] renderer gone:', details && details.reason, '- reloading UI');
    setTimeout(() => { try { if (!mainWindow.isDestroyed()) loadUI(); } catch (_) {} }, 800);
  });
  mainWindow.webContents.on('unresponsive', () => {
    console.error('[main] renderer unresponsive - reloading UI');
    try { mainWindow.webContents.forcefullyCrashRenderer(); } catch (_) {}
  });
  // A failed initial load (rare) retries rather than sitting blank.
  mainWindow.webContents.on('did-fail-load', (e, code) => {
    if (code === -3) return; // aborted (normal on fast reloads)
    setTimeout(() => { try { if (!mainWindow.isDestroyed()) loadUI(); } catch (_) {} }, 1500);
  });
  mainWindow.on('close', (e) => {
    if (!app.isQuitting) { e.preventDefault(); mainWindow.hide(); } // hide to tray
  });
}

function createTray() {
  try {
    tray = new Tray(trayIcon());
  } catch (e) { return; }
  tray.setToolTip('ASVA - running');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open ASVA', click: () => mainWindow && mainWindow.show() },
    {
      label: 'Reload window (if blank)',
      click: () => { try { mainWindow && mainWindow.reload(); mainWindow && mainWindow.show(); } catch (e) {} },
    },
    {
      label: 'Restart all services',
      click: () => Object.keys(SPECS).forEach((n) => {
        const p = services[n] && services[n].proc;
        if (p) p.kill(); // exit handler auto-restarts it
      }),
    },
    { type: 'separator' },
    { label: 'Quit ASVA (stops reminders)', click: () => { stopAll(); app.quit(); } },
  ]));
  tray.on('click', () => mainWindow && mainWindow.show());
}

// ── IPC ───────────────────────────────────────────────────────────────────
ipcMain.handle('get-config', () => CONFIG);
ipcMain.handle('get-autostart', () => app.getLoginItemSettings().openAtLogin);
ipcMain.handle('set-autostart', (e, val) => {
  app.setLoginItemSettings({ openAtLogin: !!val });
  return app.getLoginItemSettings().openAtLogin;
});
ipcMain.handle('restart-service', (e, name) => {
  const p = services[name] && services[name].proc;
  if (p) p.kill();
  else startService(name);
  return true;
});
// Top-bar "Reload data" -> force an immediate Tally refresh (rate-limited to
// once/10min server-side). Returns {ok, cooldown, wait_min, detail}.
ipcMain.handle('tally-reload', () => new Promise((resolve) => {
  if (!CONFIG.token) return resolve({ ok: false, detail: 'no token' });
  httpPostJson(`${CONFIG.backendUrl}/admin/reload`, { token: CONFIG.token },
    (ok, j) => resolve(ok ? j : { ok: false, detail: 'backend down' }));
}));

// Single instance - double-clicking the launcher again just focuses the app
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
  app.whenReady().then(() => {
    startAll();
    createWindow();
    createTray();
    pollStatus();
    setInterval(pollStatus, 5000);
    // Heartbeat once the backend is up, then every 30 min.
    setTimeout(sendHeartbeat, 25000);
    setInterval(sendHeartbeat, 30 * 60 * 1000);
  });
  app.on('window-all-closed', () => { /* keep running in tray */ });
  app.on('before-quit', () => stopAll());
}
