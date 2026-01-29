/**
 * WhatsApp Gateway - Node.js microservice wrapping WPPConnect
 *
 * This service handles WhatsApp communication separately from the main
 * FastAPI application, as recommended in the architecture.
 */

const express = require('express');
const cors = require('cors');
const wppconnect = require('@wppconnect-team/wppconnect');

const app = express();
app.use(cors());
app.use(express.json());

let client = null;
let isConnected = false;
let currentQR = null;
let qrTimestamp = null;

// Initialize WPPConnect client
async function initializeClient() {
    try {
        console.log('Initializing WhatsApp client...');
        console.log('Chrome path:', process.env.PUPPETEER_EXECUTABLE_PATH || 'default');

        client = await wppconnect.create({
            session: 'shipment-bot',
            autoClose: 0, // Disable auto-close (0 = never)
            tokenStore: 'file',
            folderNameToken: './sessions',
            catchQR: (base64Qr, asciiQR, attempts, urlCode) => {
                console.log('=== QR CODE READY ===');
                console.log(`Attempt ${attempts} of 10`);
                console.log('Scan with WhatsApp:');
                console.log(asciiQR);
                console.log('=== END QR CODE ===');
                // Store QR code for API access
                currentQR = {
                    base64: base64Qr,
                    ascii: asciiQR,
                    attempts: attempts
                };
                qrTimestamp = Date.now();
            },
            statusFind: (statusSession, session) => {
                console.log('Status Session:', statusSession);
                console.log('Session name:', session);
                if (statusSession === 'isLogged' || statusSession === 'inChat') {
                    isConnected = true;
                    currentQR = null;
                }
                if (statusSession === 'qrReadError' || statusSession === 'qrReadFail') {
                    console.log('QR read failed, will retry...');
                }
            },
            headless: true,
            devtools: false,
            useChrome: true,
            debug: false,
            logQR: true,
            waitForLogin: true,
            qrTimeout: 0, // No timeout for QR
            puppeteerOptions: {
                executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium',
                args: [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--no-first-run',
                    '--no-zygote',
                    '--single-process',
                    '--disable-gpu',
                    '--disable-extensions',
                    '--disable-software-rasterizer'
                ],
            },
        });

        isConnected = true;
        currentQR = null; // Clear QR once connected
        console.log('WhatsApp client connected successfully');

        // Listen for incoming messages
        client.onMessage(async (message) => {
            console.log('Received message:', message.body);

            // Forward to FastAPI webhook
            try {
                const response = await fetch('http://api:8000/api/webhooks/whatsapp/webhook', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        messages: [{
                            from_number: message.from.replace('@c.us', ''),
                            message_id: message.id,
                            text: message.body,
                            timestamp: message.timestamp
                        }]
                    })
                });
                console.log('Forwarded to API, status:', response.status);
            } catch (error) {
                console.error('Error forwarding message:', error);
            }
        });

    } catch (error) {
        console.error('Error initializing WhatsApp client:', error);
        isConnected = false;
    }
}

// Health check endpoint
app.get('/health', (req, res) => {
    res.json({
        status: 'ok',
        connected: isConnected
    });
});

// Send message endpoint
app.post('/send', async (req, res) => {
    const { phone, message, keyboard } = req.body;

    if (!client || !isConnected) {
        return res.status(503).json({
            error: 'WhatsApp client not connected'
        });
    }

    try {
        // Format phone number (add @c.us suffix if not present)
        const formattedPhone = phone.includes('@c.us')
            ? phone
            : `${phone.replace(/\D/g, '')}@c.us`;

        // Send message
        const result = await client.sendText(formattedPhone, message);

        console.log('Message sent to:', phone);
        res.json({ success: true, messageId: result.id });

    } catch (error) {
        console.error('Error sending message:', error);
        res.status(500).json({
            error: 'Failed to send message',
            details: error.message
        });
    }
});

// Send message with buttons (if supported)
app.post('/send-buttons', async (req, res) => {
    const { phone, message, buttons } = req.body;

    if (!client || !isConnected) {
        return res.status(503).json({
            error: 'WhatsApp client not connected'
        });
    }

    try {
        const formattedPhone = phone.includes('@c.us')
            ? phone
            : `${phone.replace(/\D/g, '')}@c.us`;

        // Note: Button support depends on WhatsApp version
        // Fallback to regular text if buttons not supported
        const result = await client.sendText(formattedPhone, message);

        res.json({ success: true, messageId: result.id });

    } catch (error) {
        console.error('Error sending message with buttons:', error);
        res.status(500).json({
            error: 'Failed to send message',
            details: error.message
        });
    }
});

// Get QR code for authentication
app.get('/qr', (req, res) => {
    if (isConnected) {
        return res.json({
            status: 'connected',
            message: 'WhatsApp is already connected'
        });
    }

    if (!currentQR) {
        return res.json({
            status: 'waiting',
            message: 'QR code not yet generated. Please wait and refresh.'
        });
    }

    // Return QR code data
    res.json({
        status: 'pending',
        qr: currentQR.base64,
        ascii: currentQR.ascii,
        timestamp: qrTimestamp,
        message: 'Scan QR code with WhatsApp on your phone'
    });
});

// Get QR code as image
app.get('/qr/image', (req, res) => {
    if (isConnected) {
        return res.status(200).send('Already connected');
    }

    if (!currentQR || !currentQR.base64) {
        return res.status(404).send('QR code not available');
    }

    // Extract base64 data and send as image
    const base64Data = currentQR.base64.replace(/^data:image\/\w+;base64,/, '');
    const imageBuffer = Buffer.from(base64Data, 'base64');

    res.set('Content-Type', 'image/png');
    res.send(imageBuffer);
});

// Disconnect endpoint
app.post('/disconnect', async (req, res) => {
    if (client) {
        try {
            await client.close();
            isConnected = false;
            res.json({ success: true, message: 'Disconnected' });
        } catch (error) {
            res.status(500).json({ error: error.message });
        }
    } else {
        res.json({ message: 'Client not initialized' });
    }
});

const PORT = process.env.PORT || 3000;

app.listen(PORT, async () => {
    console.log(`WhatsApp Gateway running on port ${PORT}`);
    await initializeClient();
});
