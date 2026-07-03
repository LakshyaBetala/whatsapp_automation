const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode');
const express = require('express');
const cors = require('cors');

const app = express();
app.use(cors());
// 10mb: invoice PDFs arrive base64-encoded in the JSON body
app.use(express.json({ limit: '10mb' }));

const client = new Client({
    authStrategy: new LocalAuth(),
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

client.initialize();

// --- Express API --- //

app.get('/api/wa/status', (req, res) => {
    res.json({
        ready: clientReady,
        qr: qrCodeData
    });
});

app.post('/api/wa/send', async (req, res) => {
    if (!clientReady) {
        return res.status(503).json({ success: false, error: "WhatsApp client is not ready" });
    }

    const { phone, message, pdf_base64, pdf_name } = req.body;

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
        } else {
            await client.sendMessage(formattedNumber, message);
        }
        res.json({ success: true, message: "Message sent successfully" });
    } catch (error) {
        console.error("Error sending message:", error);
        res.status(500).json({ success: false, error: error.message });
    }
});

const PORT = 3001;
app.listen(PORT, () => {
    console.log(`WhatsApp Background Service running on port ${PORT}`);
});
