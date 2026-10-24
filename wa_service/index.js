// ASVA WhatsApp service - Baileys engine (NO browser).
//
// We moved off whatsapp-web.js/puppeteer because the browser approach kept
// failing on real hardware: Chromium download failures, Windows EBUSY file
// locks, and a known whatsapp-web.js bug where the session authenticates then
// LOGOUTs mid-sync (github wwebjs/whatsapp-web.js#5682). Baileys talks to
// WhatsApp directly over WebSocket - no Chrome, no Edge, no Chromium download,
// no EBUSY - and reconnects transient drops on its own without a re-scan.
//
// The HTTP surface is unchanged, so the FastAPI backend and ASVA_BOT.bat need
// no changes:
//   GET  /api/wa/status  -> { ready, qr }
//   GET  /qr             -> human QR page
//   POST /api/wa/send    -> { phone, message, pdf_base64?, media_base64?, ... }
//   inbound messages     -> POST {BACKEND_URL}/webhooks/aisensy, reply on chat
// Baileys v6.7+ is published as an ES module, which a CommonJS require() cannot
// load (ERR_REQUIRE_ESM - the crash that left WhatsApp stuck on "starting").
// A dynamic import() loads BOTH an ESM and a CJS build from CommonJS, so this is
// future-proof against the next packaging change. The symbols are filled in by
// loadBaileys() before the first socket is created (top of start()).
let makeWASocket, useMultiFileAuthState, makeCacheableSignalKeyStore,
    fetchLatestBaileysVersion, fetchLatestWaWebVersion, downloadMediaMessage,
    DisconnectReason;
let _baileysReady = false;
let banned = false;   // 403: WhatsApp blocked this number. Reconnecting cannot fix it.
async function loadBaileys() {
    if (_baileysReady) return;
    const b = await import('@whiskeysockets/baileys');
    // ESM build: default export is makeWASocket, named exports on the namespace.
    // CJS-via-import(): the whole module.exports sits on b.default instead.
    const cjs = (b.default && typeof b.default === 'object' && b.default.useMultiFileAuthState)
        ? b.default : null;
    const src = cjs || b;
    makeWASocket = cjs ? (cjs.default || cjs.makeWASocket) : (b.default || b.makeWASocket);
    useMultiFileAuthState = src.useMultiFileAuthState;
    makeCacheableSignalKeyStore = src.makeCacheableSignalKeyStore;
    fetchLatestBaileysVersion = src.fetchLatestBaileysVersion;
    fetchLatestWaWebVersion = src.fetchLatestWaWebVersion;
    downloadMediaMessage = src.downloadMediaMessage;
    DisconnectReason = src.DisconnectReason;
    if (typeof makeWASocket !== 'function') {
        throw new Error('Baileys loaded but makeWASocket is not a function');
    }
    _baileysReady = true;
    console.log('Baileys loaded via dynamic import.');
}
const qrcode = require('qrcode');
const express = require('express');
const cors = require('cors');
const http = require('http');
const https = require('https');
const { URL } = require('url');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// Stay alive through transient errors instead of crashing + re-QR looping.
process.on('unhandledRejection', (e) => console.error('unhandledRejection:', (e && e.message) || e));
process.on('uncaughtException', (e) => console.error('uncaughtException:', (e && e.message) || e));

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';
const PORT = process.env.PORT || 3001;
const SESSION_ID = process.env.SESSION_ID || 'default';
// "shop" = customer-facing number; "bot" = owner-only ASVA assistant number.
const WA_CHANNEL = process.env.WA_CHANNEL || 'shop';
// WA_AUTH_DIR wins when set: the desktop app points it at Electron's userData
// folder so the shop's WhatsApp login survives a reinstall/auto-update (the
// install folder is replaced on every update). With no override we keep the
// login next to this file, so the i3 bot service is unchanged.
const AUTH_ROOT = process.env.WA_AUTH_DIR || __dirname;
const AUTH_DIR = path.join(AUTH_ROOT, '.baileys_auth', `session-${SESSION_ID}`);
// A known-good snapshot of the session. The desktop app force-kills wa_service
// (taskkill /F) on every auto-update; if that lands mid-saveCreds() the Baileys
// auth files tear and the next launch cannot resume -> a QR appears and the shop
// silently un-links ("session drops after each update"). We snapshot the session
// whenever it is cleanly connected, and restore that snapshot instead of wiping
// when the primary is corrupt or a stream error would otherwise force a re-scan.
const AUTH_BAK = AUTH_DIR + '.bak';

// ── Send-once ledger ────────────────────────────────────────────────────
// The backend retries a send whenever it does not hear a clean answer, and a
// ReadTimeout is exactly that: the request ARRIVED and we simply did not hear
// back in time. WhatsApp had already taken the message. Retrying it delivered
// the reminder a second time, which is the "sent twice" the owners report -
// and it happened most around a reconnect, when answers are slowest.
//
// So every send may carry a client_msg_id (the outbox row id, stable across
// every retry of that row). The first time we see one we send and remember the
// result; every later request with the same id returns that result WITHOUT
// sending. Kept outside AUTH_DIR on purpose: a session wipe must not reopen
// the duplicate window.
const SENT_ONCE = path.join(AUTH_ROOT, '.baileys_auth', `sent-once-${SESSION_ID}.json`);
const SENT_TTL_MS = 24 * 60 * 60 * 1000;
let sentOnce = new Map();
try {
    const raw = JSON.parse(fs.readFileSync(SENT_ONCE, 'utf8'));
    const cut = Date.now() - SENT_TTL_MS;
    for (const [k, v] of Object.entries(raw)) if (v && v.at > cut) sentOnce.set(k, v);
} catch (e) { /* first run, or unreadable: start empty */ }

function rememberSent(id, result) {
    if (!id) return;
    sentOnce.set(id, { at: Date.now(), result });
    const cut = Date.now() - SENT_TTL_MS;
    for (const [k, v] of sentOnce) if (!v || v.at <= cut) sentOnce.delete(k);
    try {
        fs.mkdirSync(path.dirname(SENT_ONCE), { recursive: true });
        fs.writeFileSync(SENT_ONCE, JSON.stringify(Object.fromEntries(sentOnce)), 'utf8');
    } catch (e) { console.warn('send-once ledger not saved:', e.message); }
}
let restoredFromBackup = false;   // restore at most once per process, then trust it
function _credsOk(dir) {
    try { JSON.parse(fs.readFileSync(path.join(dir, 'creds.json'), 'utf8')); return true; }
    catch (e) { return false; }
}
function backupAuth() {
    try {
        if (!_credsOk(AUTH_DIR)) return;
        fs.rmSync(AUTH_BAK, { recursive: true, force: true });
        fs.cpSync(AUTH_DIR, AUTH_BAK, { recursive: true });
    } catch (e) { /* best-effort: a missing backup only costs one re-scan */ }
}
function restoreAuthFromBackup() {
    try {
        if (!_credsOk(AUTH_BAK)) return false;
        fs.rmSync(AUTH_DIR, { recursive: true, force: true });
        fs.cpSync(AUTH_BAK, AUTH_DIR, { recursive: true });
        console.log('Restored WhatsApp session from last-good backup (no re-scan needed).');
        return true;
    } catch (e) { return false; }
}

// Silent pino-compatible logger (Baileys requires one; we don't want its noise).
function mkLogger() {
    const l = {
        level: 'silent',
        child: () => l,
        trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {},
    };
    return l;
}
const logger = mkLogger();

// POST JSON with Node's built-in http/https (no global fetch dependency).
function postJson(urlStr, bodyObj, timeoutMs = 30000, extraHeaders = {}) {
    return new Promise((resolve, reject) => {
        let u;
        try { u = new URL(urlStr); } catch (e) { return reject(e); }
        const payload = Buffer.from(JSON.stringify(bodyObj));
        const lib = u.protocol === 'https:' ? https : http;
        const req = lib.request({
            hostname: u.hostname,
            port: u.port || (u.protocol === 'https:' ? 443 : 80),
            path: u.pathname + u.search,
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Content-Length': payload.length,
                       ...(extraHeaders || {}) },
            timeout: timeoutMs,
        }, (res) => {
            let data = '';
            res.on('data', (d) => (data += d));
            res.on('end', () => {
                let json = {};
                try { json = JSON.parse(data); } catch (e) { /* non-JSON */ }
                resolve({ status: res.statusCode, json });
            });
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(new Error('request timeout')); });
        req.write(payload);
        req.end();
    });
}

// GET JSON with Node's built-in http/https (no global fetch dependency).
function getJson(urlStr, timeoutMs = 20000) {
    return new Promise((resolve, reject) => {
        let u;
        try { u = new URL(urlStr); } catch (e) { return reject(e); }
        const lib = u.protocol === 'https:' ? https : http;
        const req = lib.request({
            hostname: u.hostname,
            port: u.port || (u.protocol === 'https:' ? 443 : 80),
            path: u.pathname + u.search, method: 'GET', timeout: timeoutMs,
        }, (res) => {
            let data = '';
            res.on('data', (d) => (data += d));
            res.on('end', () => { let json = {}; try { json = JSON.parse(data); } catch (e) {} resolve({ status: res.statusCode, json }); });
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(new Error('request timeout')); });
        req.end();
    });
}

// ── Privacy allow-list (shop channel only) ────────────────────────────────
// The shop's own customer numbers, fetched from the backend. When loaded, the
// shop's WhatsApp only forwards messages FROM these numbers to the server; a
// message from anyone else (family, friends, unknown) is dropped right here and
// never leaves the laptop. Fail-OPEN: until the list has loaded (or if a fetch
// fails), we keep forwarding so a real customer's reply is never missed.
const WA_TOKEN = process.env.WA_TOKEN || '';
// Shared secret proving a BOT-channel forward really came from our own ASVA
// assistant number (set on the host bot service + backend). Shop channel proves
// itself with WA_TOKEN instead, so this stays empty on shop laptops.
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || '';
// Shared secret guarding this service's OWN HTTP API. Every route here can send
// WhatsApp messages as this number, log the session out, or hand a fresh pairing
// QR to whoever asks - so exposing them unauthenticated is account takeover.
// Empty = open (a shop laptop reachable only from its own machine); the internet
// facing bot host MUST set it.
const WA_API_KEY = process.env.WA_API_KEY || '';
// Bind address. The bot host serves this through a Cloudflare tunnel that
// connects from localhost, so binding to 127.0.0.1 removes the public-IP path
// entirely while keeping the tunnel working.
const BIND_HOST = process.env.WA_BIND_HOST || '0.0.0.0';
let allowSet = null;                 // null = not loaded yet -> fail open
function _last10(n) { const d = String(n || '').replace(/\D/g, ''); return d.length > 10 ? d.slice(-10) : d; }
async function refreshAllowlist() {
    if (WA_CHANNEL !== 'shop' || !WA_TOKEN) return;   // only the customer-facing number filters
    try {
        const r = await getJson(`${BACKEND_URL}/webhooks/allowlist?token=${encodeURIComponent(WA_TOKEN)}`);
        if (r && r.json && Array.isArray(r.json.numbers)) {
            // Only adopt a non-empty list; an empty result (backend hiccup / not
            // synced yet) must NOT silence a shop that does have customers.
            if (r.json.numbers.length > 0) {
                allowSet = new Set(r.json.numbers.map(_last10));
                console.log(`Allow-list loaded: ${allowSet.size} customer numbers (personal messages stay on this laptop).`);
            }
        }
    } catch (e) { /* keep the previous list; fail open if never loaded */ }
}

let sock = null;
let qrCodeData = null;   // data: URL of the current QR (null once connected)
let clientReady = false;
let starting = false;
let reconnectFails = 0;  // consecutive TRANSIENT failures -> backoff
let badSessionFails = 0; // consecutive code=500 badSession -> retry a few times BEFORE wiping
const BAD_SESSION_MAX = 5;  // only wipe a paired session after this many 500s in a row
let gen = 0;             // socket generation: events from an old socket are ignored
let registered = false;  // creds are paired with a phone (no QR needed)
let lastCloseCode = null;
let lastProgressAt = Date.now();  // last time the connection made ANY progress
let restartTimer = null;

// ── Message cache for getMessage() ──────────────────────────────────────
// When a recipient's device can't decrypt a message, WhatsApp asks the sender
// to re-send it (a "retry receipt"); poll updates need it too. If we can't
// answer, Baileys retries decryption in a loop that surfaces as code=500
// badSession and drops the link (the exact repeated-disconnect symptom).
// Caching what we send + receive lets getMessage() answer instantly. Bounded
// so it never grows without limit.
const MSG_CACHE_MAX = 1500;
const msgStore = new Map();   // message id -> WAMessageContent
// The cache has to OUTLIVE this process. wa_service restarts often (reconnect,
// update, reboot), and an in-memory-only cache meant every restart orphaned the
// messages sent just before it: the recipient's retry could not be answered and
// their chat showed "Waiting for this message" forever. Keeping it next to the
// session file costs a few hundred KB and fixes exactly that.
const MSG_CACHE_FILE = path.join(AUTH_DIR, 'msg-cache.json');
const MSG_CACHE_PERSIST = 400;        // newest N kept on disk
let msgCacheDirty = false;
let msgCacheTimer = null;

function saveMsgCache() {
    msgCacheTimer = null;
    if (!msgCacheDirty) return;
    msgCacheDirty = false;
    try {
        fs.mkdirSync(AUTH_DIR, { recursive: true });
        const all = Array.from(msgStore.entries());
        const tail = all.slice(Math.max(0, all.length - MSG_CACHE_PERSIST));
        const tmp = MSG_CACHE_FILE + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(tail));
        fs.renameSync(tmp, MSG_CACHE_FILE);   // atomic: never a half-written cache
    } catch (e) { /* a cache is a nicety, never a reason to fail a send */ }
}

function loadMsgCache() {
    try {
        if (!fs.existsSync(MSG_CACHE_FILE)) return;
        const rows = JSON.parse(fs.readFileSync(MSG_CACHE_FILE, 'utf8'));
        if (Array.isArray(rows)) {
            for (const [id, content] of rows) if (id && content) msgStore.set(id, content);
            console.log(`Message cache restored (${msgStore.size} recent messages).`);
        }
    } catch (e) { /* corrupt or absent: start empty */ }
}

function cacheMsg(id, content) {
    if (!id || !content) return;
    if (msgStore.size >= MSG_CACHE_MAX) {          // trim oldest ~10% in one pass
        let n = Math.ceil(MSG_CACHE_MAX * 0.1);
        for (const k of msgStore.keys()) { msgStore.delete(k); if (--n <= 0) break; }
    }
    msgStore.set(id, content);
    msgCacheDirty = true;
    // Debounced: a burst of sends writes once, not once per message.
    if (!msgCacheTimer) msgCacheTimer = setTimeout(saveMsgCache, 4000);
}
loadMsgCache();
for (const sig of ['SIGTERM', 'SIGINT']) {
    try { process.on(sig, () => { saveMsgCache(); process.exit(0); }); } catch (e) {}
}
process.on('exit', () => { try { saveMsgCache(); } catch (e) {} });
// A tiny NodeCache-compatible shim so Baileys can throttle its own decryption
// retries (msgRetryCounterCache), which also curbs the 500-loop.
function mkCache() {
    const m = new Map();
    return { get: (k) => m.get(k), set: (k, v) => m.set(k, v),
             del: (k) => m.delete(k), flushAll: () => m.clear(),
             keys: () => Array.from(m.keys()) };
}
const msgRetryCounterCache = mkCache();

// Exactly ONE pending restart, ever. Duplicate timers = two sockets on the
// same session = they kick each other in an endless 440-conflict loop
// (phone shows "linked", service shows disconnected forever).
function scheduleRestart(ms) {
    if (restartTimer) clearTimeout(restartTimer);
    restartTimer = setTimeout(() => { restartTimer = null; start(); }, ms);
}

// Tear down the current socket (if any) and start a fresh one.
function forceRestart(why, delayMs = 1000) {
    console.warn(`Restarting socket: ${why}`);
    gen += 1;                       // orphan the old socket's event handlers
    try { if (sock) sock.end(new Error(why)); } catch (e) { /* already dead */ }
    clientReady = false;
    qrCodeData = null;
    starting = false;
    scheduleRestart(delayMs);
}

// ── Watchdog: the self-healing that Baileys does not do ─────────────────
// 1. Zombie: socket died with NO close event (laptop sleep, network switch).
//    Phone says "linked", we say ready, but the ws is not open -> restart.
// 2. Stuck: not ready, no QR on screen, no progress for 3+ minutes -> restart.
//    (A QR on screen = waiting for a human. That is never "stuck".)
setInterval(() => {
    try {
        if (clientReady) {
            lastProgressAt = Date.now();
            const wsOpen = sock && sock.ws && sock.ws.isOpen !== false;
            if (!wsOpen) forceRestart('zombie socket (marked ready but ws is closed)');
            return;
        }
        if (qrCodeData) { lastProgressAt = Date.now(); return; }
        if (Date.now() - lastProgressAt > 180000) {
            forceRestart('stuck for 3+ minutes with no QR and no connection');
        }
    } catch (e) { /* watchdog must never crash the service */ }
}, 60000);

// The REAL current WhatsApp Web version. fetchLatestBaileysVersion() reads
// Baileys' own hosted list, which is known to go stale and silently break
// NEW device pairing (Baileys issue #2679). fetchLatestWaWebVersion() reads
// web.whatsapp.com itself, so it is always current. This runs on every
// (re)connect, so the version auto-upgrades over time with NO re-scan -
// the version is just a handshake header; the login lives in .baileys_auth.
async function waWebVersion() {
    try {
        if (typeof fetchLatestWaWebVersion === 'function') {
            const r = await fetchLatestWaWebVersion({});
            if (r && r.version && !r.error) return r.version;
        }
    } catch (e) { /* fall through */ }
    try {
        const r = await fetchLatestBaileysVersion();
        return r.version;
    } catch (e) { return undefined; } // library default
}

function digitsToJid(phone) {
    const d = String(phone || '').replace(/\D/g, '');
    return { d, jid: `${d}@s.whatsapp.net` };
}

function textOf(m) {
    if (!m) return '';
    return (m.conversation
        || (m.extendedTextMessage && m.extendedTextMessage.text)
        || (m.imageMessage && m.imageMessage.caption)
        || (m.documentMessage && m.documentMessage.caption)
        || '').trim();
}

// ── LID -> phone resolution ─────────────────────────────────────────────
// WhatsApp now hides many 1:1 chats behind a LID (anonymised id), so
// remoteJid arrives as e.g. 238396538634426@lid instead of the phone. The
// backend matches senders by PHONE (businesses/clients.whatsapp_number), so
// we must resolve the real number: WhatsApp sends it as sender_pn on lid
// stanzas (msg.key.senderPn in Baileys). We also persist every lid->phone
// pair we see, so a rare stanza missing sender_pn still resolves.
const LIDMAP_FILE = path.join(AUTH_DIR, 'lidmap.json');
let lidMap = {};
try { lidMap = JSON.parse(fs.readFileSync(LIDMAP_FILE, 'utf8')); } catch (e) { /* fresh */ }
function saveLidMap() {
    try {
        fs.mkdirSync(AUTH_DIR, { recursive: true });
        fs.writeFileSync(LIDMAP_FILE, JSON.stringify(lidMap));
    } catch (e) { /* best-effort */ }
}
function digitsOf(jidStr) {
    // '919444294894:12@s.whatsapp.net' -> '919444294894'
    return String(jidStr || '').split('@')[0].split(':')[0].replace(/\D/g, '');
}
function resolveSender(key) {
    const jid = key.remoteJid || '';
    const raw = jid.split('@')[0].split(':')[0];
    if (!jid.endsWith('@lid')) return raw;
    const pn = digitsOf(key.senderPn || key.participantPn || '');
    if (pn) {
        if (lidMap[raw] !== pn) { lidMap[raw] = pn; saveLidMap(); }
        return pn;
    }
    if (lidMap[raw]) return lidMap[raw];
    console.warn(`LID ${raw} has no sender_pn and no cached mapping - forwarding lid as sender`);
    return raw;
}

async function handleInbound(msg) {
    try {
        if (!msg.message || msg.key.fromMe) return;
        const jid = msg.key.remoteJid || '';
        // Ignore groups, status broadcasts, newsletters.
        if (jid.endsWith('@g.us') || jid === 'status@broadcast' || jid.endsWith('@newsletter')) return;
        const sender = resolveSender(msg.key);

        // Privacy filter: on the customer-facing number, only forward messages
        // from the shop's OWN customers. Anything from a non-customer (family,
        // friends, unknown) is dropped here and never sent to the server. Fail
        // open while the allow-list has not loaded yet (allowSet === null).
        if (WA_CHANNEL === 'shop' && allowSet && !allowSet.has(_last10(sender))) {
            return;
        }

        const text = textOf(msg.message);

        // Bill photos: forward image so the backend can OCR it.
        let media_base64, media_type;
        const img = msg.message.imageMessage;
        if (img) {
            try {
                const buf = await downloadMediaMessage(msg, 'buffer', {}, { logger, reuploadRequest: sock.updateMediaMessage });
                if (buf && buf.length < 7_000_000) {
                    media_base64 = buf.toString('base64');
                    media_type = img.mimetype || 'image/jpeg';
                }
            } catch (e) { console.error('Media download failed:', e.message); }
        }
        if (!text && !media_base64) return;

        // Authenticate this forward so the public webhook can trust it (see the
        // backend's aisensy_inbound gate): the bot number carries the shared
        // WEBHOOK_SECRET; a shop carries its own agent token.
        const inHdrs = {};
        if (WEBHOOK_SECRET) inHdrs['x-webhook-secret'] = WEBHOOK_SECRET;
        if (WA_TOKEN) inHdrs['x-agent-token'] = WA_TOKEN;
        const resp = await postJson(`${BACKEND_URL}/webhooks/aisensy`, {
            data: { sender, message: text, messageId: msg.key.id, media_base64, media_type, channel: WA_CHANNEL },
        }, 30000, inHdrs);
        const reply = (resp.json && typeof resp.json.reply === 'string') ? resp.json.reply : '';
        const via = jid.endsWith('@lid') ? ` (lid ${jid.split('@')[0]})` : '';
        console.log(`Inbound from ${sender}${via}: "${text.slice(0, 40)}" -> backend ${resp.status}, reply ${reply.length} chars`);
        if (reply.length > 0) {
            try {
                const sent = await sock.sendMessage(jid, { text: reply });
                if (sent && sent.key) cacheMsg(sent.key.id, sent.message);
                console.log(`Replied to ${sender} (${reply.length} chars)`);
            } catch (e) { console.error(`Failed to reply to ${sender}:`, (e && e.message) || e); }
        }
    } catch (err) {
        // Owner-facing bot: tell the sender the backend is down. Never on the
        // shop number (customers must not see bot error messages).
        console.error(`Failed to handle inbound (${BACKEND_URL}):`, (err && err.message) || err);
    }
}

async function start() {
    if (starting) return;
    starting = true;
    const myGen = ++gen;   // this socket's generation; stale events are dropped
    try {
        await loadBaileys();   // dynamic ESM import; idempotent, safe every start()
        // Torn-write recovery: if the primary session is corrupt (e.g. killed
        // mid-save on an auto-update) but a good backup exists, restore it so we
        // resume WITHOUT a re-scan instead of showing a fresh QR.
        if (!_credsOk(AUTH_DIR) && restoreAuthFromBackup()) restoredFromBackup = true;
        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
        registered = !!(state.creds && state.creds.registered);
        const version = await waWebVersion();
        if (version) console.log('Using WhatsApp Web version', version.join('.'));
        else console.log('Could not fetch WA version, using library default.');
        console.log(registered
            ? 'Existing session found - reconnecting WITHOUT a QR.'
            : 'No saved login found yet. If this is a first link, a QR will appear; if a session exists it will connect in a moment.');

        sock = makeWASocket({
            version,
            logger,
            printQRInTerminal: false,
            markOnlineOnConnect: false,
            syncFullHistory: false,
            browser: ['ASVA Bot', 'Chrome', '1.0.0'],
            // Detect dead links fast: ping every 25s, give up a connect after
            // 30s, and never let a query hang forever after laptop sleep.
            keepAliveIntervalMs: 25000,
            connectTimeoutMs: 30000,
            defaultQueryTimeoutMs: 60000,
            // Answer WhatsApp's re-send/poll requests + throttle decrypt retries:
            // the pair that stops the code=500 badSession disconnect loop.
            getMessage: async (key) => {
                try { return msgStore.get(key && key.id) || undefined; }
                catch (e) { return undefined; }
            },
            msgRetryCounterCache,
            retryRequestDelayMs: 350,
            maxMsgRetryCount: 5,
            auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger) },
        });

        sock.ev.on('creds.update', () => {
            if (myGen !== gen) return;
            registered = !!(sock.authState && sock.authState.creds && sock.authState.creds.registered);
            saveCreds();
        });

        sock.ev.on('connection.update', (u) => {
            if (myGen !== gen) return;   // event from a replaced socket: ignore
            const { connection, lastDisconnect, qr } = u;
            lastProgressAt = Date.now();
            if (qr) {
                qrcode.toDataURL(qr, (err, url) => {
                    if (!err && myGen === gen) { qrCodeData = url; console.log('QR RECEIVED, scan it at /qr'); }
                });
            }
            if (connection === 'open') {
                clientReady = true;
                registered = true;
                qrCodeData = null;
                reconnectFails = 0;
                badSessionFails = 0;
                console.log('WhatsApp CONNECTED (Baileys). Ready.');
                // Snapshot this known-good session so the next auto-update can
                // recover from a torn creds write without a re-scan.
                setTimeout(backupAuth, 3000);
            }
            if (connection === 'close') {
                clientReady = false;
                // CRITICAL: a QR from a closed socket is DEAD. Showing it makes
                // the phone say "couldn't link device" - clear it immediately;
                // the next socket generates a fresh one within seconds.
                qrCodeData = null;
                const code = lastDisconnect && lastDisconnect.error && lastDisconnect.error.output
                    && lastDisconnect.error.output.statusCode;
                lastCloseCode = code || null;
                console.log(`Connection closed (code=${code}).`);
                starting = false;

                if (code === DisconnectReason.restartRequired) {
                    // 515: NORMAL after a successful QR scan (and some stream
                    // errors). Not a failure - restart NOW to finish pairing.
                    console.log('Restart required (normal after pairing) - restarting immediately.');
                    scheduleRestart(400);
                    return;
                }
                if (code === DisconnectReason.loggedOut) {
                    // 401: genuinely unlinked from the phone (or phone offline
                    // 14+ days). Only THIS wipes - reconnecting would loop forever.
                    // Wipe the backup too, so a stale snapshot can't resurrect a
                    // session the owner deliberately unlinked.
                    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (e) {}
                    try { fs.rmSync(AUTH_BAK, { recursive: true, force: true }); } catch (e) {}
                    registered = false;
                    reconnectFails = 0;
                    console.log('Logged out from the phone: session wiped, a fresh QR will appear.');
                    scheduleRestart(2000);
                    return;
                }
                if (code === DisconnectReason.badSession) {
                    // 500: often a TRANSIENT WhatsApp stream error, not a truly dead
                    // session. Wiping on the first one destroys a working, unattended
                    // login and drops into an endless QR loop nobody is there to scan
                    // (the exact failure seen in the field). So reconnect with the
                    // SAME creds a few times first; only wipe if it keeps failing.
                    if (registered && badSessionFails < BAD_SESSION_MAX) {
                        badSessionFails += 1;
                        const delay = Math.min(5000 * badSessionFails, 60000);
                        console.log(`Stream error (500) - reconnecting WITHOUT re-scan `
                            + `(attempt ${badSessionFails}/${BAD_SESSION_MAX}, in ${Math.round(delay/1000)}s).`);
                        scheduleRestart(delay);
                        return;
                    }
                    // Before wiping, try the last-good backup once: a torn write
                    // from an update-time kill shows up as a persistent 500.
                    if (!restoredFromBackup && restoreAuthFromBackup()) {
                        restoredFromBackup = true;
                        badSessionFails = 0;
                        console.log('Repeated 500s - restored last-good session, retrying without a re-scan.');
                        scheduleRestart(2000);
                        return;
                    }
                    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (e) {}
                    try { fs.rmSync(AUTH_BAK, { recursive: true, force: true }); } catch (e) {}
                    registered = false;
                    reconnectFails = 0;
                    badSessionFails = 0;
                    console.log('Session bad after repeated retries: wiped, a fresh QR will appear.');
                    scheduleRestart(2000);
                    return;
                }
                if (code === DisconnectReason.connectionReplaced) {
                    // 440: ANOTHER process connected with this same session
                    // (a second ASVA window / leftover node.exe). Reconnecting
                    // fast just makes the two fight. Warn loudly, retry slow.
                    console.error('CONFLICT (440): another ASVA WhatsApp window is running with this same session. Close the extra one. Retrying in 60s.');
                    scheduleRestart(60000);
                    return;
                }
                if (code === DisconnectReason.forbidden) {
                    // 403: WhatsApp has blocked this number. Reconnecting cannot
                    // fix it, and hammering a block is how a temporary one is
                    // made permanent - so we STOP, and stay stopped, and say so
                    // loudly enough that a human finds out. The old code logged
                    // "re-scanning will not help" and then scheduled a restart
                    // every five minutes anyway, for as long as the process ran.
                    banned = true;
                    clientReady = false;
                    qrCodeData = null;
                    console.error('FORBIDDEN (403): WhatsApp has BLOCKED this number. '
                        + 'Halting - no further connection attempts will be made. '
                        + 'Re-scanning will not help; this needs WhatsApp support.');
                    return;
                }
                if (!registered) {
                    // QR cycle expired before anyone scanned (Baileys stops
                    // after ~5 QRs). Not a failure: restart quickly so the /qr
                    // page ALWAYS has a live code. Never back off here - a slow
                    // restart is what shows people dead QRs.
                    console.log('QR expired unscanned - generating a fresh one.');
                    scheduleRestart(2000);
                    return;
                }
                // Transient drop of a paired session (net blip, sleep, wifi
                // switch): resume with NO re-scan. Backoff grows only here.
                reconnectFails += 1;
                const delay = Math.min(3000 * reconnectFails, 60000);
                console.log(`Reconnecting in ${Math.round(delay / 1000)}s (attempt ${reconnectFails})...`);
                scheduleRestart(delay);
            }
        });

        sock.ev.on('messages.upsert', async (ev) => {
            if (myGen !== gen) return;
            // Cache every message (any type) so getMessage() can answer a later
            // re-send/poll request and avoid the decrypt-retry disconnect loop.
            for (const msg of ev.messages) {
                if (msg && msg.key && msg.message) cacheMsg(msg.key.id, msg.message);
            }
            if (ev.type !== 'notify') return;
            for (const msg of ev.messages) await handleInbound(msg);
        });
    } catch (e) {
        console.error('start() failed, retrying in 5s:', (e && e.message) || e);
        starting = false;
        scheduleRestart(5000);
    }
}
// NOTE: start() (the Baileys connect) is deliberately NOT called here. It is
// called only AFTER app.listen(PORT) succeeds - the port is our single-instance
// guard. If a second wa_service is spawned (e.g. an auto-update race left an old
// one alive), it must fail to bind the port and exit WITHOUT ever connecting
// Baileys; connecting a second socket on the same session triggers a 440
// "connection replaced" that knocks the primary offline - a real cause of the
// "WhatsApp keeps disconnecting" reports.

// ── HTTP API (unchanged shape) ──────────────────────────────────────────
const app = express();
// A wide-open CORS policy let ANY web page call this API from a visitor's
// browser. Same-origin only; the backend talks to us server-to-server.
app.use(cors({ origin: false }));
app.use(express.json({ limit: '10mb' }));

// ── Auth guard ────────────────────────────────────────────────────────────
// Constant-time compare so the key cannot be recovered a byte at a time.
function keyOk(req) {
    if (!WA_API_KEY) return true;                    // not configured = open
    const given = String(req.get('x-asva-key') || req.query.key || '');
    const a = Buffer.from(given), b = Buffer.from(WA_API_KEY);
    return a.length === b.length && crypto.timingSafeEqual(a, b);
}
function requireKey(req, res, next) {
    if (keyOk(req)) return next();
    console.warn(`Rejected unauthenticated ${req.method} ${req.path}`);
    return res.status(401).json({ success: false, error: 'unauthorized' });
}

app.get('/api/wa/status', requireKey, (req, res) => {
    res.json({ ready: clientReady, qr: qrCodeData, registered, banned,
               last_close_code: lastCloseCode });
});

// ── Re-link: the ONE reliable escape hatch when linking goes wrong ──────
// Repeated "couldn't link device" on the phone means the server still holds
// a half-dead registration for this session. The fix is a REAL logout
// (clears the server side), a clean local wipe, and a fresh QR - plus
// removing the old entry in the phone's Linked Devices.
app.post('/api/wa/relink', requireKey, async (req, res) => {
    banned = false;   // a deliberate fresh start clears the halt
    console.log('RELINK requested: logging out, wiping session, fresh QR coming.');
    gen += 1;   // orphan the old socket first so nothing races the wipe
    try { if (sock) await sock.logout(); } catch (e) { /* best-effort: server may be unreachable */ }
    try { if (sock) sock.end(new Error('relink')); } catch (e) {}
    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (e) {}
    clientReady = false;
    qrCodeData = null;
    registered = false;
    reconnectFails = 0;
    starting = false;
    scheduleRestart(500);
    res.json({ ok: true });
});

// ── Leave the server: the owner asked for their WhatsApp back ──────────
// A real logout (so ASVA disappears from the phone's Linked devices), then the
// session files go. The manager stops this process once the shop drops off its
// list; exiting here just makes it immediate.
app.post('/api/wa/logout', requireKey, async (req, res) => {
    console.log('LOGOUT requested: unlinking this number from the server.');
    gen += 1;                       // orphan the socket before the wipe
    try { if (sock) await sock.logout(); } catch (e) { /* phone may be offline */ }
    try { if (sock) sock.end(new Error('logout')); } catch (e) {}
    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (e) {}
    clientReady = false;
    qrCodeData = null;
    registered = false;
    res.json({ ok: true });
    // Let the response flush, then stop; the manager will not restart us
    // because the shop is no longer on its list.
    setTimeout(() => process.exit(0), 400);
});

const QR_HELP = `
    <div style="max-width:420px;font-family:system-ui;color:#444;line-height:1.6">
      <p><b>Phone says "couldn't link device"?</b> Do this once:</p>
      <ol>
        <li>On the phone: WhatsApp &rarr; Linked devices &rarr; tap the old ASVA entry &rarr; <b>Log out</b>.</li>
        <li>Press the button below.</li>
        <li>Scan the fresh QR that appears.</li>
      </ol>
      <button onclick="if(confirm('Get a fresh QR? You will need to scan again.'))fetch('/api/wa/relink',{method:'POST'}).then(()=>setTimeout(()=>location.reload(),1500))"
        style="padding:10px 18px;font-size:1em;cursor:pointer">Re-link (fresh QR)</button>
    </div>`;

app.get('/qr', requireKey, (req, res) => {
    if (clientReady) return res.send('<h2>&#9989; WhatsApp is connected.</h2>' + QR_HELP);
    if (!qrCodeData) {
        return res.send('<h2>&#9203; Getting a fresh QR&hellip; this page refreshes itself.</h2><script>setTimeout(()=>location.reload(),3000)</script>');
    }
    res.send(`<h2>Scan with this number's WhatsApp (Linked devices):</h2>
        <img src="${qrCodeData}" width="300" height="300">
        <p style="font-family:system-ui;color:#666">The code renews itself - scan the one on screen.</p>
        ${QR_HELP}
        <script>setTimeout(()=>location.reload(),8000)</script>`);
});

app.post('/api/wa/send', requireKey, async (req, res) => {
    if (!clientReady || !sock) {
        return res.status(503).json({ success: false, error: 'WhatsApp not ready' });
    }
    const { phone, message, pdf_base64, pdf_name, media_base64, media_type, media_name,
            client_msg_id } = req.body;
    if (!phone || !message) {
        return res.status(400).json({ success: false, error: 'Missing phone or message' });
    }
    // Already delivered under this id: answer with the original result and do
    // NOT send again. This is what makes a backend retry safe.
    const prior = client_msg_id ? sentOnce.get(client_msg_id) : null;
    if (prior && prior.at > Date.now() - SENT_TTL_MS) {
        console.log(`Duplicate send suppressed (client_msg_id=${client_msg_id}).`);
        return res.json({ ...prior.result, deduped: true });
    }
    try {
        const { d, jid: rawJid } = digitsToJid(phone);
        let jid = rawJid;
        // Verify the number is on WhatsApp before sending (sending to non-WA
        // numbers is a ban signal). Best-effort: proceed if the check errors.
        try {
            const r = await sock.onWhatsApp(d);
            if (!(r && r[0] && r[0].exists)) {
                console.warn(`Skip send: ${d} is not on WhatsApp`);
                const miss = { success: false, error: 'not_on_whatsapp', skipped: true };
                rememberSent(client_msg_id, miss);   // a settled outcome, not a retry
                return res.json(miss);
            }
            jid = r[0].jid;
        } catch (e) { console.warn(`Number check failed for ${d} (sending anyway):`, e.message); }

        let sent;
        if (pdf_base64) {
            sent = await sock.sendMessage(jid, {
                document: Buffer.from(pdf_base64, 'base64'),
                mimetype: 'application/pdf', fileName: pdf_name || 'invoice.pdf', caption: message,
            });
        } else if (media_base64) {
            sent = await sock.sendMessage(jid, {
                image: Buffer.from(media_base64, 'base64'), caption: message,
            });
        } else {
            sent = await sock.sendMessage(jid, { text: message });
        }
        if (sent && sent.key) cacheMsg(sent.key.id, sent.message);
        const ok = { success: true, message: 'Message sent successfully',
                     messageId: (sent && sent.key && sent.key.id) || undefined };
        rememberSent(client_msg_id, ok);
        res.json(ok);
    } catch (error) {
        console.error('Error sending message:', error.message);
        res.status(500).json({ success: false, error: error.message });
    }
});

const server = app.listen(PORT, BIND_HOST, () => {
    console.log(`WhatsApp Background Service (Baileys) on ${BIND_HOST}:${PORT} [channel=${WA_CHANNEL}, session=${SESSION_ID}, auth=${WA_API_KEY ? 'on' : 'OFF'}]`);
    console.log(`Forwarding inbound messages to ${BACKEND_URL}/webhooks/aisensy`);
    // Port bound = we are the only instance. NOW it is safe to connect WhatsApp.
    start();
});
server.on('error', (e) => {
    if (e && e.code === 'EADDRINUSE') {
        console.error(`Port ${PORT} already in use - another ASVA WhatsApp service is `
            + `already running. Exiting WITHOUT touching the WhatsApp session (this `
            + `prevents a 440 conflict that would disconnect the live one).`);
        process.exit(0);   // clean exit; the supervisor keeps the original running
    }
    console.error('HTTP server error:', (e && e.message) || e);
});

// Load the privacy allow-list shortly after boot, then refresh every 10 min so
// new customers are picked up. Shop channel only; no-op without a token.
if (WA_CHANNEL === 'shop' && WA_TOKEN) {
    setTimeout(refreshAllowlist, 8000);
    setInterval(refreshAllowlist, 10 * 60 * 1000);
}
