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
});
