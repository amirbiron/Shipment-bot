/**
 * WhatsApp Gateway - Node.js microservice wrapping WPPConnect
 *
 * This service handles WhatsApp communication separately from the main
 * FastAPI application, as recommended in the architecture.
 */

const express = require('express');
const cors = require('cors');
const fs = require('fs');
const path = require('path');
const wppconnect = require('@wppconnect-team/wppconnect');

const app = express();
app.use(cors());
app.use(express.json());

// Get API webhook URL from environment variable
const API_WEBHOOK_URL = process.env.API_WEBHOOK_URL || 'http://localhost:8000/api/webhooks/whatsapp/webhook';

// Session folder path - use absolute path for Render's disk mount
const SESSION_FOLDER = '/app/sessions';
const SESSION_NAME = 'shipment-bot';

/**
 * Clean up stale Chrome lock files that prevent browser from starting.
 * This happens when the container restarts but the persistent disk keeps the lock.
 * Searches recursively for lock files.
 */
function cleanupStaleLocks() {
    const lockFiles = ['SingletonLock', 'SingletonSocket', 'SingletonCookie'];

    function findAndDeleteLocks(dir) {
        if (!fs.existsSync(dir)) return;

        try {
            const items = fs.readdirSync(dir, { withFileTypes: true });
            for (const item of items) {
                const fullPath = path.join(dir, item.name);
                if (item.isDirectory()) {
                    findAndDeleteLocks(fullPath);  // Recurse into subdirectories
                } else if (lockFiles.includes(item.name)) {
                    try {
                        fs.unlinkSync(fullPath);
                        console.log(`Removed stale lock file: ${fullPath}`);
                    } catch (err) {
                        console.log(`Could not remove ${fullPath}:`, err.message);
                    }
                }
            }
        } catch (err) {
            console.log(`Error reading ${dir}:`, err.message);
        }
    }

    console.log('Starting lock cleanup in:', SESSION_FOLDER);
    findAndDeleteLocks(SESSION_FOLDER);
    findAndDeleteLocks('./sessions');  // Also check relative path
    console.log('Lock cleanup completed');
}

let client = null;
let isConnected = false;
let currentQR = null;
let qrTimestamp = null;

// Initialize WPPConnect client
async function initializeClient() {
    try {
        console.log('Initializing WhatsApp client...');
        console.log('Chrome path:', process.env.PUPPETEER_EXECUTABLE_PATH || 'default');

        // Clean up stale lock files from previous runs
        cleanupStaleLocks();

        client = await wppconnect.create({
            session: SESSION_NAME,
            autoClose: 0, // Disable auto-close (0 = never)
            tokenStore: 'file',
            folderNameToken: SESSION_FOLDER,  // Use absolute path to match disk mount
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
            console.log('From:', message.from);
            console.log('ChatId:', message.chatId);
            console.log('Sender:', JSON.stringify(message.sender));
            console.log('NotifyName:', message.notifyName);

            // Get the correct ID to reply to
            let replyTo = message.from;

            // If it's a LID (@lid), try to get the real phone number
            if (replyTo.includes('@lid')) {
                console.log('LID detected, trying to get real phone number...');

                try {
                    // Try to get contact info which might have real phone
                    const contact = await client.getContact(replyTo);
                    console.log('Contact info:', JSON.stringify(contact));

                    if (contact && contact.id && contact.id._serialized && !contact.id._serialized.includes('@lid')) {
                        replyTo = contact.id._serialized;
                        console.log('Got real phone from contact:', replyTo);
                    } else if (contact && contact.number) {
                        replyTo = `${contact.number}@c.us`;
                        console.log('Got number from contact:', replyTo);
                    }
                } catch (e) {
                    console.log('Could not get contact:', e.message);
                }

                // Try getChatById for additional info
                if (replyTo.includes('@lid') && message.chatId) {
                    try {
                        const chat = await client.getChatById(message.chatId);
                        console.log('Chat info:', JSON.stringify(chat?.contact || chat?.id));
                        if (chat && chat.contact && chat.contact.number) {
                            replyTo = `${chat.contact.number}@c.us`;
                            console.log('Got number from chat contact:', replyTo);
                        } else if (chat && chat.id && chat.id._serialized && !chat.id._serialized.includes('@lid')) {
                            replyTo = chat.id._serialized;
                            console.log('Got ID from chat:', replyTo);
                        }
                    } catch (e) {
                        console.log('Could not get chat:', e.message);
                    }
                }

                // If still LID, try chatId directly
                if (replyTo.includes('@lid') && message.chatId && !message.chatId.includes('@lid')) {
                    replyTo = message.chatId;
                    console.log('Using chatId instead:', replyTo);
                }

                // Last resort: sender.id
                if (replyTo.includes('@lid') && message.sender && message.sender.id && !message.sender.id.includes('@lid')) {
                    replyTo = message.sender.id;
                    console.log('Using sender.id instead:', replyTo);
                }

                // If still LID after all attempts, log warning but continue
                // WPPConnect sendText might still work with LID in some cases
                if (replyTo.includes('@lid')) {
                    console.log('WARNING: Could not resolve LID to phone number, will try sending to LID directly');
                }
            }

            console.log('Final replyTo:', replyTo);

            // Check if message has media (image)
            let mediaUrl = null;
            let mediaType = null;

            if (message.isMedia || message.type === 'image' || message.mimetype) {
                console.log('Media message detected!');
                console.log('Message type:', message.type);
                console.log('Mimetype:', message.mimetype);

                try {
                    // Download media as base64
                    const mediaData = await client.downloadMedia(message);
                    if (mediaData) {
                        // Create a data URL from base64
                        mediaUrl = `data:${message.mimetype || 'image/jpeg'};base64,${mediaData}`;
                        mediaType = message.type || 'image';
                        console.log('Media downloaded, type:', mediaType);
                    }
                } catch (mediaError) {
                    console.log('Error downloading media:', mediaError.message);
                    // Try alternative method
                    try {
                        const buffer = await message.downloadMedia();
                        if (buffer) {
                            mediaUrl = `data:${message.mimetype || 'image/jpeg'};base64,${buffer.toString('base64')}`;
                            mediaType = message.type || 'image';
                            console.log('Media downloaded via alternative method');
                        }
                    } catch (altError) {
                        console.log('Alternative media download also failed:', altError.message);
                    }
                }
            }

            // Forward to FastAPI webhook
            try {
                console.log('Forwarding to:', API_WEBHOOK_URL);
                const payload = {
                    messages: [{
                        from_number: replyTo,
                        message_id: message.id,
                        text: message.body || '',
                        timestamp: message.timestamp,
                        media_url: mediaUrl,
                        media_type: mediaType
                    }]
                };
                console.log('Payload has media:', !!mediaUrl);

                const response = await fetch(API_WEBHOOK_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
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

    console.log('Send request received - phone:', phone, 'message:', message?.substring(0, 50));
    console.log('Keyboard:', keyboard);

    if (!client || !isConnected) {
        return res.status(503).json({
            error: 'WhatsApp client not connected'
        });
    }

    try {
        let chatId = phone;

        // If it already has a valid suffix (@c.us, @g.us, @lid), use as-is
        const hasValidSuffix = chatId.includes('@c.us') ||
                               chatId.includes('@g.us') ||
                               chatId.includes('@lid');

        if (!hasValidSuffix) {
            // Format phone number - ensure country code and @c.us suffix
            let cleanPhone = phone.replace(/\D/g, '');  // Remove non-digits

            // Add Israel country code if missing (starts with 0)
            if (cleanPhone.startsWith('0')) {
                cleanPhone = '972' + cleanPhone.substring(1);
            }

            chatId = `${cleanPhone}@c.us`;
        }

        console.log('Sending to:', chatId);

        let result;

        // Try to send with interactive list if keyboard is provided
        if (keyboard && Array.isArray(keyboard) && keyboard.length > 0) {
            // Flatten keyboard array
            const options = keyboard.flat();

            // Try sendListMessage (works better than buttons)
            try {
                result = await client.sendListMessage(chatId, {
                    buttonText: 'בחרו 👆',
                    description: message,
                    title: '',
                    footer: '',
                    sections: [{
                        title: 'אפשרויות',
                        rows: options.map((text, index) => ({
                            rowId: `option_${index}`,
                            title: text,
                            description: ''
                        }))
                    }]
                });
                console.log('Message sent with list to:', chatId);
            } catch (listError) {
                console.log('sendListMessage failed:', listError.message);
                // Fallback: send as plain text
                result = await client.sendText(chatId, message);
                console.log('Message sent as text (list fallback) to:', chatId);
            }
        } else {
            // Send message directly
            result = await client.sendText(chatId, message);
            console.log('Message sent to:', chatId);
        }

        res.json({ success: true, messageId: result?.id });

    } catch (error) {
        console.error('Error sending message:', error.message);
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
        let chatId = phone;
        const hasValidSuffix = chatId.includes('@c.us') ||
                               chatId.includes('@g.us') ||
                               chatId.includes('@lid');

        if (!hasValidSuffix) {
            let cleanPhone = phone.replace(/\D/g, '');
            if (cleanPhone.startsWith('0')) {
                cleanPhone = '972' + cleanPhone.substring(1);
            }
            chatId = `${cleanPhone}@c.us`;
        }

        const result = await client.sendText(chatId, message);
        res.json({ success: true, messageId: result.id });

    } catch (error) {
        console.error('Error sending message with buttons:', error.message);
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

// Reset session - deletes all session data and restarts
app.post('/reset-session', async (req, res) => {
    try {
        console.log('Resetting session...');

        // Close client if exists
        if (client) {
            try {
                await client.close();
            } catch (e) {
                console.log('Error closing client:', e.message);
            }
            client = null;
        }

        isConnected = false;
        currentQR = null;

        // Delete session folder
        const sessionPath = path.join(SESSION_FOLDER, SESSION_NAME);
        if (fs.existsSync(sessionPath)) {
            fs.rmSync(sessionPath, { recursive: true, force: true });
            console.log('Deleted session folder:', sessionPath);
        }

        // Also try relative path
        const relativeSessionPath = './sessions/shipment-bot';
        if (fs.existsSync(relativeSessionPath)) {
            fs.rmSync(relativeSessionPath, { recursive: true, force: true });
            console.log('Deleted relative session folder:', relativeSessionPath);
        }

        // Reinitialize
        await initializeClient();

        res.json({ success: true, message: 'Session reset. Scan new QR code.' });
    } catch (error) {
        console.error('Error resetting session:', error);
        res.status(500).json({ error: error.message });
    }
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
