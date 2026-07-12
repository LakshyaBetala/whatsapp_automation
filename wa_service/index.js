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
const {
    default: makeWASocket,
    useMultiFileAuthState,
    makeCacheableSignalKeyStore,
    fetchLatestBaileysVersion,
    fetchLatestWaWebVersion,
    downloadMediaMessage,
    DisconnectReason,
} = require('@whiskeysockets/baileys');
const qrcode = require('qrcode');
const express = require('express');
const cors = require('cors');
const http = require('http');
const https = require('https');
const { URL } = require('url');
const fs = require('fs');
const path = require('path');

// Stay alive through transient errors instead of crashing + re-QR looping.
process.on('unhandledRejection', (e) => console.error('unhandledRejection:', (e && e.message) || e));
process.on('uncaughtException', (e) => console.error('uncaughtException:', (e && e.message) || e));

const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';
const PORT = process.env.PORT || 3001;
const SESSION_ID = process.env.SESSION_ID || 'default';
// "shop" = customer-facing number; "bot" = owner-only ASVA assistant number.
const WA_CHANNEL = process.env.WA_CHANNEL || 'shop';
const AUTH_DIR = path.join(__dirname, '.baileys_auth', `session-${SESSION_ID}`);

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
function postJson(urlStr, bodyObj, timeoutMs = 30000) {
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
            headers: { 'Content-Type': 'application/json', 'Content-Length': payload.length },
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

let sock = null;
let qrCodeData = null;   // data: URL of the current QR (null once connected)
let clientReady = false;
let starting = false;
let reconnectFails = 0;  // consecutive TRANSIENT failures -> backoff
let gen = 0;             // socket generation: events from an old socket are ignored
let registered = false;  // creds are paired with a phone (no QR needed)
let lastCloseCode = null;
let lastProgressAt = Date.now();  // last time the connection made ANY progress
let restartTimer = null;

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

        const resp = await postJson(`${BACKEND_URL}/webhooks/aisensy`, {
            data: { sender, message: text, messageId: msg.key.id, media_base64, media_type, channel: WA_CHANNEL },
        });
        const reply = (resp.json && typeof resp.json.reply === 'string') ? resp.json.reply : '';
        const via = jid.endsWith('@lid') ? ` (lid ${jid.split('@')[0]})` : '';
        console.log(`Inbound from ${sender}${via}: "${text.slice(0, 40)}" -> backend ${resp.status}, reply ${reply.length} chars`);
        if (reply.length > 0) {
            try {
                await sock.sendMessage(jid, { text: reply });
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
        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
        registered = !!(state.creds && state.creds.registered);
        const version = await waWebVersion();
        if (version) console.log('Using WhatsApp Web version', version.join('.'));
        else console.log('Could not fetch WA version, using library default.');
        console.log(registered
            ? 'Existing session found - reconnecting WITHOUT a QR.'
            : 'No session yet - a QR will appear for one-time linking.');

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
                console.log('WhatsApp CONNECTED (Baileys). Ready.');
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
                if (code === DisconnectReason.loggedOut || code === DisconnectReason.badSession) {
                    // 401: unlinked from the phone (or phone offline 14+ days).
                    // 500: corrupt creds. Both need a clean wipe + fresh QR -
                    // reconnecting with these creds would loop forever.
                    try { fs.rmSync(AUTH_DIR, { recursive: true, force: true }); } catch (e) {}
                    registered = false;
                    reconnectFails = 0;
                    console.log('Session dead (logged out / bad session): wiped, a fresh QR will appear.');
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
                    // 403: number blocked by WhatsApp. Nothing here fixes it.
                    console.error('FORBIDDEN (403): WhatsApp has blocked this number. Re-scanning will not help.');
                    scheduleRestart(300000);
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
            if (ev.type !== 'notify') return;
            for (const msg of ev.messages) await handleInbound(msg);
        });
    } catch (e) {
        console.error('start() failed, retrying in 5s:', (e && e.message) || e);
        starting = false;
        scheduleRestart(5000);
    }
}
start();

// ── HTTP API (unchanged shape) ──────────────────────────────────────────
const app = express();
app.use(cors());
app.use(express.json({ limit: '10mb' }));

app.get('/api/wa/status', (req, res) => {
    res.json({ ready: clientReady, qr: qrCodeData, registered, last_close_code: lastCloseCode });
});

// ── Re-link: the ONE reliable escape hatch when linking goes wrong ──────
// Repeated "couldn't link device" on the phone means the server still holds
// a half-dead registration for this session. The fix is a REAL logout
// (clears the server side), a clean local wipe, and a fresh QR - plus
// removing the old entry in the phone's Linked Devices.
app.post('/api/wa/relink', async (req, res) => {
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

app.get('/qr', (req, res) => {
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

app.post('/api/wa/send', async (req, res) => {
    if (!clientReady || !sock) {
        return res.status(503).json({ success: false, error: 'WhatsApp not ready' });
    }
    const { phone, message, pdf_base64, pdf_name, media_base64, media_type, media_name } = req.body;
    if (!phone || !message) {
        return res.status(400).json({ success: false, error: 'Missing phone or message' });
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
                return res.json({ success: false, error: 'not_on_whatsapp', skipped: true });
            }
            jid = r[0].jid;
        } catch (e) { console.warn(`Number check failed for ${d} (sending anyway):`, e.message); }

        if (pdf_base64) {
            await sock.sendMessage(jid, {
                document: Buffer.from(pdf_base64, 'base64'),
                mimetype: 'application/pdf', fileName: pdf_name || 'invoice.pdf', caption: message,
            });
        } else if (media_base64) {
            await sock.sendMessage(jid, {
                image: Buffer.from(media_base64, 'base64'), caption: message,
            });
        } else {
            await sock.sendMessage(jid, { text: message });
        }
        res.json({ success: true, message: 'Message sent successfully' });
    } catch (error) {
        console.error('Error sending message:', error.message);
        res.status(500).json({ success: false, error: error.message });
    }
});

app.listen(PORT, () => {
    console.log(`WhatsApp Background Service (Baileys) running on port ${PORT} [channel=${WA_CHANNEL}, session=${SESSION_ID}]`);
    console.log(`Forwarding inbound messages to ${BACKEND_URL}/webhooks/aisensy`);
});
