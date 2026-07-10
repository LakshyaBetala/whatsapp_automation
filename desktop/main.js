// ASVA Desktop - the control center.
// Supervises the backend + both WhatsApp services + the Tally watcher as
// child processes (replacing START.bat and its cmd windows), and shows the
// dashboard / QR setup / status in one window with a tray icon.
const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');

const REPO = path.join(__dirname, '..'); // desktop/ lives inside the repo root
let mainWindow = null;
let tray = null;
app.isQuitting = false;

// ── Config: token + backend URL come from Asva/config.json ────────────────
function loadConfig() {
  let token = '';
  let backend = 'http://localhost:8000';
  try {
    const c = JSON.parse(fs.readFileSync(path.join(REPO, 'Asva', 'config.json'), 'utf8'));
    token = c.agent_token || '';
    if (c.backend_url) backend = c.backend_url.replace(/\/+$/, '');
  } catch (e) {
    // config missing/invalid - dashboard tab will show a helpful message
  }
  const q = token ? `?token=${encodeURIComponent(token)}` : '';
  return {
    token,
    backendUrl: backend,
    dashboardUrl: token ? `${backend}/admin${q}` : '',
    remindersUrl: token ? `${backend}/admin/reminders${q}` : '',
    analyticsUrl: token ? `${backend}/admin/analytics${q}` : '',
    accountsUrl: token ? `${backend}/admin/accounts${q}` : '',
    waShopUrl: 'http://localhost:3001/qr',
  };
}
const CONFIG = loadConfig();

// ── Child-process supervision ─────────────────────────────────────────────
// ONE WhatsApp only (the shop's own number). The company/bot number lives on a
// separate account, so we do not run a second wa_service (3002) here.
const SPECS = {
  backend: { cmd: 'python', args: ['-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000'], cwd: REPO, env: {} },
  whatsapp: { cmd: 'node', args: ['index.js'], cwd: path.join(REPO, 'wa_service'), env: { PORT: '3001' } },
  watcher: { cmd: 'python', args: ['-u', 'tally_agent/agent.py', '--watch'], cwd: REPO, env: {} },
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
  proc.on('exit', (code) => {
    push(Buffer.from(`\n[${name} stopped, code ${code}]\n`));
    services[name].proc = null;
    if (!app.isQuitting) {
      services[name].restarts = (services[name].restarts || 0) + 1;
      setTimeout(() => startService(name), 4000); // auto-restart like START.bat
    }
  });
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

function pollStatus() {
  const out = {};
  let pending = 3;
  const done = () => { if (--pending === 0) sendToWindow('status', out); };
  ping(`${CONFIG.backendUrl}/health`, (ok) => { out.backend = ok; done(); });
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
    } catch (e) {} }
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
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
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
  });
  app.on('window-all-closed', () => { /* keep running in tray */ });
  app.on('before-quit', () => stopAll());
}
