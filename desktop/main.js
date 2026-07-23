// ASVA Desktop - the control center.
// Supervises the backend + both WhatsApp services + the Tally watcher as
// child processes (replacing START.bat and its cmd windows), and shows the
// dashboard / QR setup / status in one window with a tray icon.
const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage } = require('electron');
const { spawn, spawnSync } = require('child_process');
const http = require('http');
const os = require('os');
const path = require('path');
const fs = require('fs');

const REPO = path.join(__dirname, '..'); // desktop/ lives inside the repo root
// Identifiable User-Agent so Cloudflare's bot check never blocks the app's own
// calls to the server (heartbeat, health, reload). Matches tally_agent.
const USER_AGENT = 'Mozilla/5.0 (compatible; ASVA-Desktop/1.6.0; +https://tryasva.com)';
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
// Where this shop's identity lives. Packaged: beside the bundled Tally reader
// (which resolves config.json next to its own exe). Dev: the repo's tally_agent.
// Older installs kept it in Asva/, so that path is still honoured.
const AGENT_DIR = app.isPackaged ? path.join(process.resourcesPath, 'agent')
                                 : path.join(REPO, 'tally_agent');
const CONFIG_PATH = path.join(AGENT_DIR, 'config.json');
const LEGACY_CONFIG_PATH = path.join(REPO, 'Asva', 'config.json');

function configPath() {
  if (fs.existsSync(CONFIG_PATH)) return CONFIG_PATH;
  if (fs.existsSync(LEGACY_CONFIG_PATH)) return LEGACY_CONFIG_PATH;
  return CONFIG_PATH;                       // where a fresh pairing will write
}
function readConfigFile() {
  try { return JSON.parse(fs.readFileSync(configPath(), 'utf8')); } catch (e) { return null; }
}
// Unpaired = no identity yet, so the app opens the setup wizard instead of the
// dashboard. This is what lets the installer ship with no secret inside.
function isPaired() {
  const c = readConfigFile();
  return !!(c && c.agent_token && c.business_id);
}

function loadConfig() {
  let token = '';
  let backend = 'http://localhost:8000';
  let companies = [];
  try {
    const c = readConfigFile() || {};
    if (!c.agent_token) throw new Error('unpaired');
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

// ── Child-process supervision (thin client) ────────────────────────────────
// The packaged shop runs NO local backend and needs NO Python or system Node:
//   - wa_service runs on Electron's OWN Node runtime (ELECTRON_RUN_AS_NODE),
//   - the Tally reader + the outbox drainer are the bundled asva-agent.exe.
// In dev (unpackaged) we fall back to plain `node` and the repo's Python so the
// same file still runs from source.
const WA_DIR = app.isPackaged ? path.join(process.resourcesPath, 'wa_service')
                              : path.join(REPO, 'wa_service');
const AGENT_EXE = path.join(AGENT_DIR, 'asva-agent.exe');
const USE_AGENT_EXE = app.isPackaged && fs.existsSync(AGENT_EXE);
// Dev-only Python (the packaged app never touches Python).
const VENV_PY = path.join(REPO, '.venv', 'Scripts', 'python.exe');
const PY = fs.existsSync(VENV_PY) ? VENV_PY : 'python';

// wa_service on Electron's bundled Node in the packaged app; plain node in dev.
const NODE_CMD = app.isPackaged ? process.execPath : 'node';
const NODE_ENV = app.isPackaged ? { ELECTRON_RUN_AS_NODE: '1' } : {};

// The Tally reader / drainer: bundled exe when packaged, source in dev.
function agentService(extraArgs) {
  return USE_AGENT_EXE
    ? { cmd: AGENT_EXE, args: extraArgs, cwd: AGENT_DIR, env: {} }
    : { cmd: PY, args: ['-u', 'tally_agent/agent.py', ...extraArgs], cwd: REPO, env: {} };
}

// ONE number, one wa_service instance:
//   whatsapp (3001) = the shop's own number -> bills, reminders, customer replies
//   watcher         = reads Tally, pushes to the server (app.tryasva.com)
//   drainer         = delivers the server's queued sends from this shop's number
const SPECS = {
  whatsapp: { cmd: NODE_CMD, args: [path.join(WA_DIR, 'index.js')], cwd: WA_DIR,
              env: { ...NODE_ENV, PORT: '3001', SESSION_ID: 'default', WA_CHANNEL: 'shop' } },
  watcher: agentService(['--watch']),
  drainer: agentService(['--drain-outbox']),
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

// Self-update BEFORE starting services, then launch. Blocks up to ~90s on the
// updater (which no-ops when already current), so a new version is applied on
// the next open with zero action from the shop. Failure never blocks startup.
function startAll() {
  // An unpaired install has no business to serve - the services would just
  // crash-loop on a missing config. Setup starts them when it finishes.
  if (!isPaired()) {
    console.log('[main] not paired yet - waiting for setup');
    return;
  }
  // Dev only: the source-tree Python updater. The packaged app updates via its
  // own installer, not by overwriting files, so there is no Python to run.
  if (!app.isPackaged) {
    try {
      const upd = spawnSync(PY, ['updater.py'], { cwd: REPO, timeout: 90000, encoding: 'utf8' });
      if (upd && upd.stdout) console.log('[update]', upd.stdout.trim());
    } catch (e) { console.error('updater skipped:', (e && e.message) || e); }
  }
  Object.keys(SPECS).forEach(startService);
}

// ── Setup wizard plumbing ─────────────────────────────────────────────────
// The bundled Tally reader does the actual work (it owns the hard-won Tally
// XML handling); we just run it and read one JSON line. Every failure returns
// a message written for a shopkeeper - never a traceback.
function agentSpec(args) {
  return USE_AGENT_EXE
    ? { cmd: AGENT_EXE, args, cwd: AGENT_DIR }
    : { cmd: PY, args: ['tally_agent/agent.py', ...args], cwd: REPO };
}

function runAgent(args, timeoutMs = 90000) {
  return new Promise((resolve) => {
    const spec = agentSpec(args);
    let out = '';
    let proc;
    try {
      proc = spawn(spec.cmd, spec.args, { cwd: spec.cwd, windowsHide: true });
    } catch (e) {
      return resolve({ ok: false, error: 'Setup helper could not start.' });
    }
    const done = (v) => { clearTimeout(timer); resolve(v); };
    const timer = setTimeout(() => {
      try { proc.kill(); } catch (_) {}
      done({ ok: false, error: 'That took too long. Please try again.' });
    }, timeoutMs);
    proc.stdout.on('data', (d) => { out += d.toString(); });
    proc.on('error', () => done({ ok: false, error: 'Setup helper could not start.' }));
    proc.on('exit', () => {
      const line = out.trim().split(/\r?\n/).filter(Boolean).pop() || '';
      try { done(JSON.parse(line)); }
      catch (e) { done({ ok: false, error: 'Setup did not complete. Please try again.' }); }
    });
  });
}

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
    req = http.get(url, { timeout: 2500, headers: { 'User-Agent': USER_AGENT } }, (res) => {
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
      headers: { 'Content-Type': 'application/json', 'Content-Length': data.length, 'User-Agent': USER_AGENT },
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
let WA_READY = null;   // shop WhatsApp connected? (reported to the health center)
function sendHeartbeat() {
  if (!CONFIG.token) return;
  try {
    httpPostJson(`${CONFIG.backendUrl}/license/heartbeat`, {
      agent_token: CONFIG.token,
      machine_id: os.hostname(),
      agent_version: SERVER_VERSION || undefined,
      wa_ready: (typeof WA_READY === 'boolean') ? WA_READY : undefined,
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
    if (w.reachable) WA_READY = !!w.ready;   // only trust a real answer from :3001
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
  // A fresh install opens the setup wizard; a paired one opens the dashboard.
  const loadUI = () => mainWindow.loadFile(path.join(
    __dirname, 'renderer', isPaired() ? 'index.html' : 'setup.html'));
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

// ── Setup wizard IPC ──────────────────────────────────────────────────────
ipcMain.handle('pair-redeem', (e, code) => runAgent(['--pair', String(code || '').trim()]));
ipcMain.handle('pair-companies', () => runAgent(['--list-companies-json']));
// Connection doctor - runs the agent's --diagnose and returns its JSON verdict.
ipcMain.handle('run-diagnose', () => runAgent(['--diagnose'], 40000));
ipcMain.handle('pair-finish', async (e, company) => {
  if (company) {
    const r = await runAgent(['--set-company', String(company)]);
    if (!r.ok) return r;
  }
  Object.assign(CONFIG, loadConfig());   // pick up the identity we just wrote
  startAll();                            // WhatsApp + Tally reader come up now
  return { ok: true };
});
ipcMain.handle('open-dashboard', () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  }
  return true;
});
ipcMain.handle('wa-status', () => new Promise((resolve) => {
  ping('http://localhost:3001/api/wa/status', (ok, b) => resolve(parseWa(ok, b)));
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
