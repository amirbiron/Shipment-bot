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

WORKDIR /app

# Copy package.json and install
COPY package.json ./
RUN npm install --omit=dev

# Copy app code
COPY index.js ./

# Create session directories
RUN mkdir -p sessions tokens && chmod -R 777 sessions tokens

ENV PORT=10000
EXPOSE 10000

CMD ["node", "index.js"]
