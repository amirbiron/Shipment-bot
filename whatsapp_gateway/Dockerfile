FROM node:20-slim

# Install Chromium and dependencies for WPPConnect/Puppeteer
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-ipafont-gothic \
    fonts-wqy-zenhei \
    fonts-thai-tlwg \
    fonts-kacst \
    fonts-freefont-ttf \
    fonts-liberation \
    libxss1 \
    libxtst6 \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libasound2 \
    libgbm1 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Set Puppeteer/WPPConnect to use system Chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV CHROME_PATH=/usr/bin/chromium

# Create app directory
WORKDIR /app

# Copy package files from whatsapp_gateway folder (context is repo root)
COPY whatsapp_gateway/package.json ./

# Install dependencies
RUN npm install --omit=dev

# Copy application code
COPY whatsapp_gateway/index.js ./

# Create directories for sessions and tokens
RUN mkdir -p sessions tokens && \
    chmod -R 777 sessions tokens

# Use PORT env variable (Render sets this)
ENV PORT=10000
EXPOSE 10000

CMD ["node", "index.js"]
