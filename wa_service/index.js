const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode');
const express = require('express');
const cors = require('cors');

// Where the FastAPI backend lives — inbound messages are forwarded there
// so bot commands (LIST / STOP <name> / PAID) work.
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8000';
const PORT = process.env.PORT || 3001;
// SESSION_ID lets multiple instances run side by side with separate
// WhatsApp logins (e.g. shop number on 3001, platform number on 3002).
const SESSION_ID = process.env.SESSION_ID || 'default';

const app = express();
app.use(cors());
// 10mb: invoice PDFs arrive base64-encoded in the JSON body
app.use(express.json({ limit: '10mb' }));

const client = new Client({
    authStrategy: new LocalAuth({ clientId: SESSION_ID }),
    puppeteer: {
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
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

client.on('authenticated', () => {
    console.log('WhatsApp Client Authenticated!');
});

client.on('auth_failure', msg => {
    console.error('AUTHENTICATION FAILURE', msg);
    clientReady = false;
});

client.on('disconnected', (reason) => {
    console.log('WhatsApp Client was disconnected', reason);
    clientReady = false;
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
        if (!text) return;

        const sender = msg.from.replace('@c.us', '');
        const resp = await fetch(`${BACKEND_URL}/webhooks/aisensy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                data: {
                    sender: sender,
                    message: text,
                    messageId: msg.id ? msg.id._serialized : undefined,
                },
            }),
        });
        const data = await resp.json().catch(() => ({}));
        console.log(`Inbound from ${sender}: "${text.slice(0, 40)}" -> backend ${resp.status}`);

        // If the bot produced a reply, send it back on the same chat
        if (data && typeof data.reply === 'string' && data.reply.length > 0) {
            await client.sendMessage(msg.from, data.reply);
        }
    } catch (err) {
        console.error('Failed to forward inbound message:', err.message);
    }
});

client.initialize();

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
    console.log(`WhatsApp Background Service running on port ${PORT}`);
    console.log(`Forwarding inbound messages to ${BACKEND_URL}/webhooks/aisensy`);
});
