const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode');
const express = require('express');
const cors = require('cors');
const http = require('http');
const https = require('https');
const { URL } = require('url');

// POST JSON using Node's built-in http/https so inbound forwarding NEVER
// depends on a global `fetch` (undefined on Node < 18 -> every inbound reply
// silently failed, which looked like "the bot is dead"). Always available.
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
                try { json = JSON.parse(data); } catch (e) { /* non-JSON body */ }
                resolve({ status: res.statusCode, json });
            });
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(new Error('request timeout')); });
        req.write(payload);
        req.end();
    });
}

// Survive transient whatsapp-web.js errors (e.g. EBUSY while cleaning up the
// session on a WhatsApp-side logout). Without this, such an error crashes the
// Node process, the supervisor restarts it, and you get a NEW QR every time —
// the "connects then disconnects again and again" loop. We stay alive instead
// and recover to a QR/ready state cleanly.
process.on('unhandledRejection', (e) => console.error('unhandledRejection:', (e && e.message) || e));
process.on('uncaughtException', (e) => console.error('uncaughtException:', (e && e.message) || e));

// Where the FastAPI backend lives — inbound messages are forwarded there
// so bot commands (LIST / STOP <name> / PAID) work.
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';
const PORT = process.env.PORT || 3001;
// SESSION_ID lets multiple instances run side by side with separate
// WhatsApp logins (e.g. shop number on 3001, platform number on 3002).
const SESSION_ID = process.env.SESSION_ID || 'default';
// WA_CHANNEL tells the backend which number an inbound message hit:
//   "shop" = the shop's own number (customer-facing: bills, reminders, HISAB/PAID)
//   "bot"  = the ASVA assistant number (owner-only: LIST, BILL, photo, digest)
// The backend uses this to keep the bot number strictly owner-only.
const WA_CHANNEL = process.env.WA_CHANNEL || 'shop';

const app = express();
app.use(cors());
// 10mb: invoice PDFs arrive base64-encoded in the JSON body
app.use(express.json({ limit: '10mb' }));

const client = new Client({
    authStrategy: new LocalAuth({ clientId: SESSION_ID }),
    // Pin a known-good WhatsApp Web build. Without this, whatsapp-web.js
    // loads whatever web.whatsapp.com serves; when that page is newer than
    // the library expects, it reloads mid-injection and initialize() dies
    // with "Execution context was destroyed". The pinned HTML keeps the
    // page stable so the QR/auth handshake completes.
    webVersionCache: {
        type: 'remote',
        remotePath: 'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/2.3000.1040310160-alpha.html',
    },
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',   // low-RAM machines: don't use /dev/shm
            '--disable-gpu',
            '--disable-extensions',
            '--no-first-run',
        ],
    },
});

let qrCodeData = null;
let clientReady = false;

client.on('qr', (qr) => {
    // Generate and save QR code data string
    qrcode.toDataURL(qr, (err, url) => {
        qrCodeData = url;
    });
    console.log('QR RECEIVED, ready for dashboard to fetch.');
});

client.on('ready', () => {
    console.log('WhatsApp Client is ready!');
    clientReady = true;
    qrCodeData = null; // Clear QR code as it's no longer needed
});

// Shows sync progress after scanning. A busy number (many chats) can take
// several minutes on a slow PC — this proves it is loading, not frozen.
client.on('loading_screen', (percent, message) => {
    console.log(`Loading ${percent}% - ${message}`);
});

client.on('authenticated', () => {
    console.log('WhatsApp Client Authenticated!');
});

client.on('auth_failure', msg => {
    console.error('AUTHENTICATION FAILURE', msg);
    clientReady = false;
});

let reinitTimer = null;
client.on('disconnected', (reason) => {
    console.log('WhatsApp Client was disconnected', reason);
    clientReady = false;
    qrCodeData = null;
    // On a real LOGOUT/CONFLICT whatsapp-web.js tears the browser down and does
    // NOT come back on its own - it just sits dead (no QR, no sends, no bot).
    // Recover by fully destroying the client, then re-initializing to surface a
    // fresh QR. destroy() closes the browser first, so re-init does not spawn a
    // second one. EBUSY during session cleanup is swallowed by the process-level
    // handlers above. Guard against overlapping timers + auto-recovery races.
    if (reinitTimer) return;
    reinitTimer = setTimeout(async () => {
        reinitTimer = null;
        if (clientReady) return;   // reconnected on its own in the meantime
        try {
            await client.destroy();
        } catch (e) {
            console.error('destroy() during recovery failed (continuing):', (e && e.message) || e);
        }
        console.log('Re-initializing after disconnect to bring back a fresh QR...');
        startClient();
    }, 8000);
});

// ── Inbound: forward customer/owner replies to the backend bot ──────
// The backend's /webhooks/aisensy _extract() understands
// {data: {sender, message, messageId}} and dedups on messageId.
client.on('message', async (msg) => {
    try {
        // Ignore groups, broadcasts/status, and non-text messages
        if (!msg.from.endsWith('@c.us')) return;
        if (msg.isStatus) return;
        const text = (msg.body || '').trim();

        const sender = msg.from.replace('@c.us', '');

        // Bill photos: forward image media so the backend can run OCR
        let media_base64, media_type;
        if (msg.hasMedia) {
            try {
                const media = await msg.downloadMedia();
                if (media && media.mimetype && media.mimetype.startsWith('image/')
                    && media.data && media.data.length < 7_000_000) {
                    media_base64 = media.data;
                    media_type = media.mimetype;
                }
            } catch (e) {
                console.error('Media download failed:', e.message);
            }
        }
        if (!text && !media_base64) return;

        const resp = await postJson(`${BACKEND_URL}/webhooks/aisensy`, {
            data: {
                sender: sender,
                message: text,
                messageId: msg.id ? msg.id._serialized : undefined,
                media_base64: media_base64,
                media_type: media_type,
                channel: WA_CHANNEL,
            },
        });
        const data = resp.json || {};
        const reply = (data && typeof data.reply === 'string') ? data.reply : '';
        console.log(`Inbound from ${sender}: "${text.slice(0, 40)}" -> backend ${resp.status}, reply ${reply.length} chars`);

        // If the bot produced a reply, send it back on the same chat
        if (reply.length > 0) {
            try {
                await client.sendMessage(msg.from, reply);
                console.log(`Replied to ${sender} (${reply.length} chars)`);
            } catch (e) {
                console.error(`Failed to send reply to ${sender}:`, (e && e.message) || e);
            }
        }
    } catch (err) {
        console.error('Failed to forward inbound message:', (err && err.message) || err);
    }
});

// Injection can fail transiently (esp. on slow PCs) with a ProtocolError.
// Retry a few times instead of hard-crashing the whole service.
async function startClient(attempt = 1) {
    try {
        await client.initialize();
    } catch (err) {
        console.error(`initialize() failed (attempt ${attempt}): ${err.message}`);
        if (attempt < 5) {
            console.log('Retrying in 5s...');
            setTimeout(() => startClient(attempt + 1), 5000);
        } else {
            console.error('Gave up after 5 attempts. Close this window and run again.');
        }
    }
}
startClient();

// --- Express API --- //

app.get('/api/wa/status', (req, res) => {
    res.json({
        ready: clientReady,
        qr: qrCodeData
    });
});

// Human-friendly QR page for first-time linking: open http://localhost:3001/qr
app.get('/qr', (req, res) => {
    if (clientReady) {
        return res.send('<h2>✅ WhatsApp is connected.</h2>');
    }
    if (!qrCodeData) {
        return res.send('<h2>⏳ Starting up… refresh in a few seconds.</h2><script>setTimeout(()=>location.reload(),3000)</script>');
    }
    res.send(`<h2>Scan with the business WhatsApp (Linked devices):</h2>
        <img src="${qrCodeData}" width="300" height="300">
        <script>setTimeout(()=>location.reload(),10000)</script>`);
});

app.post('/api/wa/send', async (req, res) => {
    if (!clientReady) {
        return res.status(503).json({ success: false, error: "WhatsApp client is not ready" });
    }

    const { phone, message, pdf_base64, pdf_name, media_base64, media_type, media_name } = req.body;

    if (!phone || !message) {
        return res.status(400).json({ success: false, error: "Missing phone or message" });
    }

    try {
        // Format phone number to WhatsApp ID format (e.g., 91XXXXXXXXXX@c.us)
        const formattedNumber = phone.includes('@c.us') ? phone : `${phone.replace(/\D/g, '')}@c.us`;

        if (pdf_base64) {
            // Send the PDF as an attachment with the text as caption
            const media = new MessageMedia('application/pdf', pdf_base64, pdf_name || 'invoice.pdf');
            await client.sendMessage(formattedNumber, media, { caption: message });
        } else if (media_base64) {
            // Generic media (e.g. UPI QR image) with the text as caption
            const media = new MessageMedia(media_type || 'image/png', media_base64, media_name || 'media');
            await client.sendMessage(formattedNumber, media, { caption: message });
        } else {
            await client.sendMessage(formattedNumber, message);
        }
        res.json({ success: true, message: "Message sent successfully" });
    } catch (error) {
        console.error("Error sending message:", error);
        res.status(500).json({ success: false, error: error.message });
    }
});

app.listen(PORT, () => {
    console.log(`WhatsApp Background Service running on port ${PORT} [channel=${WA_CHANNEL}, session=${SESSION_ID}]`);
    console.log(`Forwarding inbound messages to ${BACKEND_URL}/webhooks/aisensy`);
});
