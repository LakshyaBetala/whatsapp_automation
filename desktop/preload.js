// Safe bridge between the renderer and the supervisor (main process).
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('asva', {
  getConfig: () => ipcRenderer.invoke('get-config'),
  getAutostart: () => ipcRenderer.invoke('get-autostart'),
  setAutostart: (v) => ipcRenderer.invoke('set-autostart', v),
  restartService: (name) => ipcRenderer.invoke('restart-service', name),
  tallyReload: () => ipcRenderer.invoke('tally-reload'),
  onStatus: (cb) => ipcRenderer.on('status', (e, d) => cb(d)),
  onLog: (cb) => ipcRenderer.on('log', (e, d) => cb(d)),

  // First-run setup wizard. Every call resolves to {ok, ...} or {ok:false,
  // error} with a message written for a shopkeeper, never a raw stack trace.
  pairRedeem: (code) => ipcRenderer.invoke('pair-redeem', code),
  pairCompanies: () => ipcRenderer.invoke('pair-companies'),
  pairFinish: (company) => ipcRenderer.invoke('pair-finish', company),
  openDashboard: () => ipcRenderer.invoke('open-dashboard'),
  waStatus: () => ipcRenderer.invoke('wa-status'),

  // Connection doctor: {ok, checks:[{name, ok, detail}]}. One button that tells
  // the owner (or the operator, remotely) exactly which link is broken.
  diagnose: () => ipcRenderer.invoke('run-diagnose'),

  // Auto-update notice. main sends {state:'downloading'|'ready'|'current'|'error'
  // |'required', version, percent, url}. installUpdate() applies a downloaded
  // update and restarts. 'required' = build too old to self-update -> reinstall.
  onUpdate: (cb) => ipcRenderer.on('update', (e, d) => cb(d)),
  installUpdate: () => ipcRenderer.invoke('install-update'),
  checkUpdate: () => ipcRenderer.invoke('check-update'),
  // Open a link (the download page) in the owner's real browser. http(s) only.
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
  // Owner language (english | hinglish). setLanguage saves it on the server so
  // the WhatsApp assistant matches the app; onLang receives the server's value.
  setLanguage: (l) => ipcRenderer.invoke('set-language', l),
  onLang: (cb) => ipcRenderer.on('lang', (e, d) => cb(d)),

  // Recent technical logs (backlog) for the "Show technical logs" panel.
  getLogs: () => ipcRenderer.invoke('get-logs'),
  // Change the Tally company ASVA reads, mid-run. {ok} or {ok:false,error}.
  changeCompany: (name) => ipcRenderer.invoke('change-company', name),
  // Real sync progress: {done, total, label} while Tally is being read.
  onSyncProgress: (cb) => ipcRenderer.on('sync-progress', (e, d) => cb(d)),
});
