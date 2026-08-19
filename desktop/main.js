// ASVA Desktop - the control center.
// Supervises the backend + both WhatsApp services + the Tally watcher as
// child processes (replacing START.bat and its cmd windows), and shows the
// dashboard / QR setup / status in one window with a tray icon.
const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage, Notification } = require('electron');
const { spawn, spawnSync } = require('child_process');
const http = require('http');
const https = require('https');
// Pick the right client for the URL. The thin client's backend is
// https://app.tryasva.com, and Node's `http` module CANNOT talk HTTPS (it throws
// "Protocol https: not supported"). Using the wrong one is why the backend
// showed red and the dashboard/reminders/analytics stayed blank on real installs
// while a localhost (http) standalone worked. Always choose by protocol.
function httpMod(url) {
  try { return new URL(url).protocol === 'https:' ? https : http; }
  catch (e) { return http; }
}
const os = require('os');
const path = require('path');
const fs = require('fs');

const REPO = path.join(__dirname, '..'); // desktop/ lives inside the repo root
// Identifiable User-Agent so Cloudflare's bot check never blocks the app's own
// calls to the server (heartbeat, health, reload). Matches tally_agent.
const USER_AGENT = `Mozilla/5.0 (compatible; ASVA-Desktop/${app.getVersion()}; +https://tryasva.com)`;
let mainWindow = null;
let tray = null;
app.isQuitting = false;
let updateReady = false;      // an update is downloaded and waiting to install
let autoUpdater = null;       // set in setupAutoUpdate; module scope so close/quit can apply it

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
    todayUrl: `${backend}/admin/today${q}`,
    dashboardUrl: `${backend}/admin${q}`,
    remindersUrl: `${backend}/admin/reminders${q}`,
    paymentsUrl: `${backend}/admin/payments${q}`,
    analyticsUrl: `${backend}/admin/analytics${q}`,
    accountsUrl: `${backend}/admin/accounts${q}`,
  };
}
// ── Where this shop's identity + WhatsApp login live ──────────────────────
// The bundled Tally reader (asva-agent.exe) sits in resources/agent, but the
// MUTABLE data - the pairing (config.json) and the WhatsApp session - must NOT
// live inside the install folder: an installer or auto-update REPLACES that
// folder, which would silently un-pair the shop and log WhatsApp out on every
// update. So both live in Electron's userData (%APPDATA%\ASVA\...), which
// survives reinstalls, and the agent + wa_service are pointed at it via env
// vars (ASVA_CONFIG_PATH / WA_AUTH_DIR). This is what makes updates seamless.
const AGENT_DIR = app.isPackaged ? path.join(process.resourcesPath, 'agent')
                                 : path.join(REPO, 'tally_agent');
const USER_DATA = app.getPath('userData');
const DATA_CONFIG = path.join(USER_DATA, 'config.json');   // the live pairing
const WA_AUTH_ROOT = path.join(USER_DATA, 'wa');           // WhatsApp login root
// Every bundled agent child (watch, drain, --pair, --set-company, --diagnose)
// inherits this via process.env, so they all read/write the same config.json.
process.env.ASVA_CONFIG_PATH = DATA_CONFIG;

// Older locations config used to live in, kept only so the one-time migration
// below can carry an existing shop's pairing forward on the first upgrade.
const RES_CONFIG = path.join(AGENT_DIR, 'config.json');
const LEGACY_CONFIG_PATH = path.join(REPO, 'Asva', 'config.json');

// One-time migration. An install made before data moved to userData kept its
// config (and WhatsApp login) inside the install folder. If userData has none
// yet but an old copy still exists, carry it over so the upgrade keeps the
// pairing and stays linked. Best-effort: any failure just means one re-pair.
function migrateData() {
  try {
    // Where the install dir sits, so we can recover config/login that a
    // pre-userData build wrote next to the exe (this is what strands an upgrade
    // from a very old build - the pairing lived beside the program, not here).
    let exeDir = '';
    try { exeDir = path.dirname(app.getPath('exe')); } catch (_) {}

    if (!fs.existsSync(DATA_CONFIG)) {
      // Broadest-first list of every place an older build may have kept config.
      const oldConfigs = [
        RES_CONFIG, LEGACY_CONFIG_PATH,
        app.isPackaged ? path.join(process.resourcesPath, 'config.json') : '',
        exeDir ? path.join(exeDir, 'config.json') : '',
        exeDir ? path.join(exeDir, 'resources', 'config.json') : '',
      ];
      for (const old of oldConfigs) {
        if (old && fs.existsSync(old)) {
          fs.mkdirSync(USER_DATA, { recursive: true });
          fs.copyFileSync(old, DATA_CONFIG);
          break;
        }
      }
    }

    const newAuth = path.join(WA_AUTH_ROOT, '.baileys_auth');
    if (!fs.existsSync(newAuth)) {
      // Every place an older build may have kept the Baileys WhatsApp login.
      const oldAuths = [
        app.isPackaged ? path.join(process.resourcesPath, 'wa_service', '.baileys_auth')
                       : path.join(REPO, 'wa_service', '.baileys_auth'),
        exeDir ? path.join(exeDir, 'wa_service', '.baileys_auth') : '',
        exeDir ? path.join(exeDir, 'resources', 'wa_service', '.baileys_auth') : '',
      ];
      for (const oldAuth of oldAuths) {
        if (oldAuth && fs.existsSync(oldAuth)) {
          fs.mkdirSync(WA_AUTH_ROOT, { recursive: true });
          fs.cpSync(oldAuth, newAuth, { recursive: true });
          break;
        }
      }
    }
  } catch (e) { console.error('[main] data migration skipped:', (e && e.message) || e); }
}
migrateData();   // must run before loadConfig() reads the pairing below

function configPath() {
  // DEV ONLY: if ASVA_DEV_CONFIG points at a file, use it. This lets the real
  // app run against the local dev backend (for demos/videos) WITHOUT touching the
  // installed app's pairing in userData or the shop's tally_agent/config.json.
  // Production never sets this env var, so this is a no-op in the field.
  if (process.env.ASVA_DEV_CONFIG && fs.existsSync(process.env.ASVA_DEV_CONFIG)) {
    return process.env.ASVA_DEV_CONFIG;
  }
  if (fs.existsSync(DATA_CONFIG)) return DATA_CONFIG;
  if (fs.existsSync(RES_CONFIG)) return RES_CONFIG;
  if (fs.existsSync(LEGACY_CONFIG_PATH)) return LEGACY_CONFIG_PATH;
  return DATA_CONFIG;                       // where a fresh pairing will write
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
    todayUrl: '', dashboardUrl: '', remindersUrl: '', paymentsUrl: '', analyticsUrl: '', accountsUrl: '',
  };
  return {
    token,
    backendUrl: backend,
    appVersion: app.getVersion(),
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
  // PYTHONUNBUFFERED so the reader's log lines stream to the app's log panel
  // live (the packaged exe otherwise buffers stdout and the panel looks empty).
  return USE_AGENT_EXE
    ? { cmd: AGENT_EXE, args: extraArgs, cwd: AGENT_DIR, env: { PYTHONUNBUFFERED: '1' } }
    : { cmd: PY, args: ['-u', 'tally_agent/agent.py', ...extraArgs], cwd: REPO, env: { PYTHONUNBUFFERED: '1' } };
}

// ONE number, one wa_service instance:
//   whatsapp (3001) = the shop's own number -> bills, reminders, customer replies
//   watcher         = reads Tally, pushes to the server (app.tryasva.com)
//   drainer         = delivers the server's queued sends from this shop's number
const SPECS = {
  whatsapp: { cmd: NODE_CMD, args: [path.join(WA_DIR, 'index.js')], cwd: WA_DIR,
              // BACKEND_URL points wa_service at the REMOTE backend so inbound
              // customer replies (PAID, payment screenshots) actually reach ASVA.
              // Without it, wa_service defaults to localhost:8000 - which does not
              // exist on a thin-client shop laptop (ECONNREFUSED) - so paid/promise
              // detection never fires. Falls back to shop config's backend_url.
              env: { ...NODE_ENV, PORT: '3001', SESSION_ID: 'default', WA_CHANNEL: 'shop',
                     BACKEND_URL: CONFIG.backendUrl || 'http://localhost:8000',
                     WA_AUTH_DIR: WA_AUTH_ROOT } },
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
    const raw = buf.toString();
    // Pull out real sync-progress markers ("ASVA_PROGRESS done/total label")
    // and drive the % bar with them; keep them OUT of the technical log so it
    // stays readable. Anything not a marker is logged as normal.
    let mm; const rx = /ASVA_PROGRESS\s+(\d+)\/(\d+)\s*([^\r\n]*)/g;
    while ((mm = rx.exec(raw)) !== null) {
      sendToWindow('sync-progress', { done: +mm[1], total: +mm[2], label: (mm[3] || '').trim() });
    }
    const s = raw.replace(/ASVA_PROGRESS[^\r\n]*\r?\n?/g, '');
    if (!s.trim()) return;                 // the chunk was only marker(s)
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
  // DEV DEMO (pitch/video on mock data): ASVA_DEV_CONFIG is set. There is no
  // Tally, so skip the Tally reader (watcher), the outbox drainer, and the
  // source-tree updater - start ONLY wa_service so the WhatsApp Setup/QR demo
  // still works. The dashboard + Payments run off the mock backend. This keeps
  // the demo clean (no red "Tally watcher not running" noise) while the UI/UX
  // stays byte-for-byte the real app.
  const DEV_DEMO = !!process.env.ASVA_DEV_CONFIG;
  // Dev only: the source-tree Python updater. The packaged app updates via its
  // own installer, not by overwriting files, so there is no Python to run.
  if (!app.isPackaged && !DEV_DEMO) {
    try {
      const upd = spawnSync(PY, ['updater.py'], { cwd: REPO, timeout: 90000, encoding: 'utf8' });
      if (upd && upd.stdout) console.log('[update]', upd.stdout.trim());
    } catch (e) { console.error('updater skipped:', (e && e.message) || e); }
  }
  const toStart = DEV_DEMO ? ['whatsapp'] : Object.keys(SPECS);
  toStart.forEach(startService);
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

// Kill a child AND its whole tree. On Windows a plain proc.kill() often leaves
// grandchildren alive (the bundled exe, Electron's node child), which keeps
// port 3001 held and makes "ASVA still runs after I closed it" happen. taskkill
// /T /F takes down the entire tree so nothing lingers.
function killTree(proc) {
  if (!proc) return;
  try {
    if (process.platform === 'win32' && proc.pid) {
      spawnSync('taskkill', ['/pid', String(proc.pid), '/T', '/F'], { windowsHide: true });
    } else {
      proc.kill();
    }
  } catch (e) { try { proc.kill(); } catch (_) { /* ignore */ } }
}

function stopAll() {
  app.isQuitting = true;   // stops the exit-handler from respawning anything
  for (const name of Object.keys(services)) {
    const p = services[name] && services[name].proc;
    if (p) killTree(p);
    if (services[name]) services[name].proc = null;
  }
}

// Apply a downloaded update WITHOUT the "ASVA cannot be closed" installer prompt.
// Two things caused that prompt: (1) the window's close-confirm dialog blocked the
// installer's close request, and (2) wa_service runs as ASVA.exe (ELECTRON_RUN_AS_NODE),
// so a non-silent installer saw a lingering ASVA process. Fix: mark quitting (so the
// close-confirm is skipped), kill every child, give Windows a moment to reap them,
// then run the installer SILENTLY so it force-closes instead of prompting.
let _updateApplying = false;
function applyUpdateAndQuit() {
  if (_updateApplying) return;
  _updateApplying = true;
  app.isQuitting = true;
  try { stopAll(); } catch (e) {}
  setTimeout(() => {
    try {
      if (autoUpdater) autoUpdater.quitAndInstall(true, true);   // isSilent, isForceRunAfter
      else app.quit();
    } catch (e) { try { app.quit(); } catch (_) {} }
  }, 1500);
}

// ── Health polling (done in main to avoid renderer CORS) ──────────────────
function ping(url, cb) {
  let req;
  try {
    req = httpMod(url).get(url, { timeout: 2500, headers: { 'User-Agent': USER_AGENT } }, (res) => {
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
    req = httpMod(url).request({
      hostname: u.hostname,
      port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + u.search,
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
function httpGetJson(url, cb) {
  let req;
  try {
    const u = new URL(url);
    req = httpMod(url).request({
      hostname: u.hostname,
      port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + u.search,
      method: 'GET', timeout: 8000,
      headers: { 'User-Agent': USER_AGENT },
    }, (res) => {
      let body = '';
      res.on('data', (d) => (body += d));
      res.on('end', () => { let j = {}; try { j = JSON.parse(body); } catch (e) {} cb(res.statusCode === 200, j); });
    });
  } catch (e) { return cb(false, {}); }
  req.on('error', () => cb(false, {}));
  req.on('timeout', () => { req.destroy(); cb(false, {}); });
  req.end();
}

// ── Money-in ping ─────────────────────────────────────────────────────────
// A shop owner's strongest habit trigger is "you got paid". The backend flags a
// payment the moment a customer says they paid / sends a screenshot; we poll for
// those and fire a native notification. Clicking it opens the Payments tab.
// lastMoneySince starts at the server's clock (set on the first poll) so we
// never replay old captures as a burst of pings when the app launches.
let lastMoneySince = null;
function pollMoneyIn() {
  if (!CONFIG.token) return;
  const since = lastMoneySince ? `&since=${encodeURIComponent(lastMoneySince)}` : '';
  httpGetJson(`${CONFIG.backendUrl}/admin/notifications?token=${encodeURIComponent(CONFIG.token)}${since}`,
    (ok, j) => {
      if (!ok || !j) return;
      if (j.now) lastMoneySince = j.now;         // advance the watermark
      const events = Array.isArray(j.events) ? j.events : [];
      if (!events.length) return;
      if (!Notification || !Notification.isSupported || !Notification.isSupported()) return;
      for (const e of events) {
        try {
          const amt = e.amount ? ('₹' + Number(e.amount).toLocaleString('en-IN')) : '';
          const n = new Notification({
            title: amt ? `Money in: ${amt}` : 'A customer says they paid',
            body: `${e.party || 'A customer'} - open Payments to check and post to Tally.`,
            silent: false,
          });
          n.on('click', () => {
            try { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } } catch (_) {}
            sendToWindow('focus-tab', { tab: 'payments' });
          });
          n.show();
        } catch (_) { /* a failed ping must never crash the app */ }
      }
    });
}

function sendHeartbeat() {
  if (!CONFIG.token) return;
  try {
    httpPostJson(`${CONFIG.backendUrl}/license/heartbeat`, {
      agent_token: CONFIG.token,
      machine_id: os.hostname(),
      agent_version: app.getVersion(),   // THIS shop's app build (e.g. 1.8.3), not the server's
      wa_ready: (typeof WA_READY === 'boolean') ? WA_READY : undefined,
    }, (ok, j) => {
      // If the server says this build is below the fleet floor, it is too old to
      // update itself (predates the self-updater). Auto-update can't rescue it,
      // so tell the owner plainly to reinstall the latest. Non-destructive nudge.
      if (ok && j && j.below_min) {
        sendToWindow('update', { state: 'required',
          version: j.latest_version || j.min_supported_version,
          url: `${CONFIG.backendUrl}/download` });
      }
      // Let the app adopt the server's saved language (fresh/re-paired laptop).
      if (ok && j && j.owner_language) {
        sendToWindow('lang', { language: j.owner_language });
      }
    });
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
      // Onboarding-tour signals (verify each step is actually done).
      out.upi = d.upi; out.ownerNumber = d.owner_number;
      out.botNumber = d.bot_number; out.lastInboundAt = d.last_inbound_at;
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
  // Closing the window CLOSES ASVA (after a confirm) and stops every background
  // process - it does NOT hide to the tray and keep running. The confirm makes
  // clear what stops, so nobody closes it by accident and wonders why bills
  // stopped going.
  mainWindow.on('close', (e) => {
    if (app.isQuitting) return;                 // a real quit is already underway
    // A downloaded update is waiting: closing = apply it. Never show the
    // close-confirm here, or it blocks the updater ("ASVA cannot be closed").
    if (updateReady) { e.preventDefault(); applyUpdateAndQuit(); return; }
    e.preventDefault();
    const { dialog } = require('electron');
    const choice = dialog.showMessageBoxSync(mainWindow, {
      type: 'question',
      buttons: ['Keep ASVA running', 'Close ASVA'],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
      title: 'Close ASVA?',
      message: 'Close ASVA completely?',
      detail: 'While ASVA is closed it will not send bills or reminders and will '
            + 'not read new entries from Tally. Everything starts again the next '
            + 'time you open ASVA.',
    });
    if (choice === 1) { app.isQuitting = true; stopAll(); app.quit(); }
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
// Recent service output for the "Show technical logs" panel. main keeps a ring
// buffer per service; the renderer pulls it on open so the box is never empty
// just because the interesting lines arrived before the window existed.
ipcMain.handle('get-logs', () => {
  const out = [];
  for (const name of Object.keys(logs)) {
    for (const line of logs[name]) out.push('[' + name + '] ' + line);
  }
  return out.join('');
});
// Change which Tally company ASVA reads, mid-run. Writes the new company, then
// restarts ONLY the Tally reader (never startAll - that would double-spawn
// WhatsApp + the drainer). The reader reads company_name fresh on start, so it
// begins reading the new company; the caller then forces a resync.
ipcMain.handle('change-company', async (e, name) => {
  const clean = String(name || '').trim();
  if (!clean) return { ok: false, error: 'No company chosen.' };
  const r = await runAgent(['--set-company', clean]);
  if (!r || !r.ok) return r || { ok: false, error: 'Could not save the company.' };
  Object.assign(CONFIG, loadConfig());        // pick up the new company_name
  const w = services.watcher && services.watcher.proc;
  if (w) killTree(w);                          // exit handler respawns it fresh
  else startService('watcher');
  return { ok: true, company: clean };
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
// Open a link in the owner's real browser (used by the "reinstall" bar to reach
// the download page). Only ever opens http(s) - never a local file or command.
ipcMain.handle('open-external', (e, url) => {
  try {
    const s = String(url || '');
    if (/^https?:\/\//i.test(s)) require('electron').shell.openExternal(s);
  } catch (_) {}
  return true;
});
// Persist the owner's chosen language on the server so the WhatsApp assistant
// answers in the same language as the app. Fire and forget.
ipcMain.handle('set-language', (e, l) => {
  if (!CONFIG.token) return false;
  const lang = (l === 'hinglish') ? 'hinglish' : 'english';
  try {
    httpPostJson(`${CONFIG.backendUrl}/license/set-language`,
      { agent_token: CONFIG.token, language: lang }, () => {});
  } catch (_) {}
  return true;
});

// ── Auto-update (notice style) ─────────────────────────────────────────────
// The app quietly checks the ASVA server's /updates feed, downloads a newer
// build IN THE BACKGROUND (differential, so it's small), then just SHOWS the
// owner a "new version ready" bar. Nothing is forced: they press Restart when
// convenient, or it applies on the next quit. Because config + WhatsApp login
// live in userData now, the update never loses data. This is what turns "visit
// every shop to update" into "one push to the i3 updates everyone".
function setupAutoUpdate() {
  if (!app.isPackaged) return;               // dev has no installer to replace
  try { ({ autoUpdater } = require('electron-updater')); }
  catch (e) { console.error('[update] electron-updater not available:', (e && e.message) || e); return; }

  autoUpdater.autoDownload = true;           // fetch quietly in the background
  autoUpdater.autoInstallOnAppQuit = true;   // also apply on the next normal quit
  // Differential (delta) downloads are ON: an update fetches only the CHANGED
  // bytes, which is fast when a release is small. Two rules keep it that way and
  // avoid the "stuck at 9%" that a HUGE delta caused through Cloudflare:
  //   1. the bundled Tally agent is rebuilt ONLY when tally_agent/ actually
  //      changes (a rebuild changes almost all of its bytes), and it is now built
  //      WITHOUT UPX so unchanged code stays byte-identical between releases;
  //   2. a release that DOES rebuild the agent is published WITHOUT its .blockmap
  //      so clients do one clean FULL download for that jump instead of a giant
  //      delta; ordinary (agent-unchanged) releases publish the .blockmap = fast
  //      delta. See deploy note / BACKUP-style runbook.
  const notesOf = (info) => {
    const n = info && info.releaseNotes;
    if (!n) return '';
    if (Array.isArray(n)) return n.map((x) => (x && x.note) || x).join('\n');
    return String(n);
  };
  autoUpdater.on('update-available', (info) =>
    sendToWindow('update', { state: 'downloading', version: info && info.version, notes: notesOf(info) }));
  autoUpdater.on('download-progress', (p) =>
    sendToWindow('update', { state: 'downloading', percent: Math.round((p && p.percent) || 0) }));
  autoUpdater.on('update-downloaded', (info) => {
    updateReady = true;                        // closing the window now applies it
    sendToWindow('update', { state: 'ready', version: info && info.version, notes: notesOf(info) });
  });
  autoUpdater.on('update-not-available', () => sendToWindow('update', { state: 'current' }));
  autoUpdater.on('error', (err) => {
    console.error('[update] error:', (err && err.message) || err);
    sendToWindow('update', { state: 'error' });
  });
  // The owner presses "Restart to update" -> apply now.
  ipcMain.handle('install-update', () => {
    try { applyUpdateAndQuit(); } catch (e) {}
    return true;
  });

  const check = (manual) => {
    if (manual) sendToWindow('update', { state: 'checking' });
    try { autoUpdater.checkForUpdates(); } catch (e) { sendToWindow('update', { state: 'error' }); }
  };
  // A manual "Check for updates" button (owner can pull an update the moment one
  // is published, instead of waiting for the 6-hour poll).
  ipcMain.handle('check-update', () => { check(true); return true; });
  setTimeout(check, 12 * 1000);              // shortly after launch
  setInterval(check, 3 * 60 * 60 * 1000);    // and every 3 hours after that
}

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
    // Money-in ping: prime the watermark, then poll for new payment captures.
    setTimeout(pollMoneyIn, 30000);
    setInterval(pollMoneyIn, 25000);
    setupAutoUpdate();
  });
  // Window closed = ASVA closed. Stop everything and exit (no tray-only ghost).
  app.on('window-all-closed', () => { stopAll(); app.quit(); });
  app.on('before-quit', () => stopAll());
}
