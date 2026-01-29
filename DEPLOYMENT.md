# Deployment Guide - מדריך העלאה

## Render Deployment

### שירותים נדרשים

1. **Web Service** - FastAPI Application
2. **Background Worker** - Celery Worker
3. **PostgreSQL** - Database
4. **Redis** - Message Broker
5. **Private Service** - WhatsApp Gateway (Node.js)

### הגדרת render.yaml

הקובץ `render.yaml` מגדיר את כל השירותים אוטומטית.

### שלבי העלאה

#### 1. יצירת חשבון Render
היכנסו ל-[render.com](https://render.com) וצרו חשבון.

#### 2. חיבור Repository
1. לחצו על "New" → "Blueprint"
2. חברו את ה-GitHub repository
3. Render יזהה את `render.yaml` אוטומטית

#### 3. הגדרת Environment Variables

ב-Render Dashboard, הגדירו:

```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
DATABASE_URL=<auto-populated by Render>
REDIS_URL=<auto-populated by Render>
```

#### 4. הגדרת Telegram Webhook

לאחר ה-deploy, הריצו:

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://your-app.onrender.com/api/telegram/webhook"
```

### WhatsApp Gateway

ה-WhatsApp Gateway דורש טיפול מיוחד:

1. **QR Code Authentication**: בפעם הראשונה תצטרכו לסרוק QR
2. **Session Persistence**: השתמשו ב-Render Disk לשמירת session
3. **Health Checks**: ודאו שה-health endpoint עובד

## Docker Deployment

### Build & Run

```bash
# Build all images
docker-compose build

# Run in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Production Configuration

עדכנו את `docker-compose.yml` לסביבת production:

```yaml
services:
  api:
    environment:
      - DEBUG=false
    deploy:
      replicas: 2
      resources:
        limits:
          memory: 512M
```

## Manual Deployment

### Prerequisites

```bash
# Python 3.11+
python --version

# PostgreSQL 14+
psql --version

# Redis
redis-cli ping

# Node.js 18+
node --version
```

### Setup

```bash
# Clone repository
git clone https://github.com/your-repo/shipment-bot.git
cd shipment-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Setup environment
cp .env.example .env
# Edit .env with your values

# Run migrations
alembic upgrade head

# Start services
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Process Management with Supervisor

```ini
# /etc/supervisor/conf.d/shipment-bot.conf

[program:api]
command=/path/to/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
directory=/path/to/shipment-bot
user=www-data
autostart=true
autorestart=true
stderr_logfile=/var/log/shipment-bot/api.err.log
stdout_logfile=/var/log/shipment-bot/api.out.log

[program:celery]
command=/path/to/venv/bin/celery -A app.workers.celery_app worker --loglevel=info
directory=/path/to/shipment-bot
user=www-data
autostart=true
autorestart=true
stderr_logfile=/var/log/shipment-bot/celery.err.log
stdout_logfile=/var/log/shipment-bot/celery.out.log

[program:celery-beat]
command=/path/to/venv/bin/celery -A app.workers.celery_app beat --loglevel=info
directory=/path/to/shipment-bot
user=www-data
autostart=true
autorestart=true
```

### Nginx Configuration

```nginx
# /etc/nginx/sites-available/shipment-bot

upstream api {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## SSL/HTTPS

### Let's Encrypt with Certbot

```bash
# Install certbot
apt install certbot python3-certbot-nginx

# Get certificate
certbot --nginx -d your-domain.com

# Auto-renewal
certbot renew --dry-run
```

## Monitoring

### Health Checks

```bash
# API Health
curl https://your-domain.com/health

# Database connectivity
curl https://your-domain.com/health/db

# Redis connectivity
curl https://your-domain.com/health/redis
```

### Logs

```bash
# Docker logs
docker-compose logs -f api

# Render logs
# Available in Render Dashboard

# System logs
tail -f /var/log/shipment-bot/api.out.log
```

## Scaling

### Horizontal Scaling

1. **API**: הוסיפו instances מאחורי load balancer
2. **Celery**: הוסיפו workers
3. **Database**: השתמשו ב-read replicas

### Render Scaling

```yaml
# render.yaml
services:
  - type: web
    plan: standard  # or pro for more resources
    scaling:
      minInstances: 2
      maxInstances: 10
      targetCPUPercent: 70
```

## Troubleshooting

### Common Issues

**Database connection error:**
```bash
# Check DATABASE_URL
echo $DATABASE_URL

# Test connection
psql $DATABASE_URL -c "SELECT 1"
```

**Celery not processing tasks:**
```bash
# Check Redis connection
redis-cli -u $REDIS_URL ping

# Check Celery workers
celery -A app.workers.celery_app inspect active
```

**WhatsApp not connecting:**
1. Delete session files
2. Restart gateway
3. Scan new QR code

### Rollback

```bash
# Docker
docker-compose down
git checkout previous-tag
docker-compose up -d

# Render
# Use Render Dashboard to rollback to previous deploy
```
