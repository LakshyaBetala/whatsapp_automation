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
});
