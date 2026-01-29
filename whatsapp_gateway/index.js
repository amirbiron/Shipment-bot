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

// Initialize WPPConnect client
async function initializeClient() {
    try {
        client = await wppconnect.create({
            session: 'shipment-bot',
            catchQR: (base64Qr, asciiQR) => {
                console.log('Scan QR Code:');
                console.log(asciiQR);
            },
            statusFind: (statusSession, session) => {
                console.log('Status Session:', statusSession);
                console.log('Session name:', session);
            },
            headless: true,
            devtools: false,
            useChrome: false,
            debug: false,
            logQR: true,
            browserArgs: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu'
            ],
        });

        isConnected = true;
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
    // QR code is logged to console during initialization
    res.json({
        message: 'Check server console for QR code'
    });
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
