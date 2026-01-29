# בוט משלוחים - Delivery Bot System V4

מערכת בוט לניהול משלוחים עבור WhatsApp ו-Telegram בעברית.

## תכונות עיקריות

- **שליחת הודעות אסינכרונית** - Transactional Outbox Pattern למניעת חסימת השרת
- **ניהול מצבי שיחה** - State Machine מפורש לזרימת שיחה
- **אטומיות בתפיסת משלוח** - תפיסה וחיוב ארנק בטרנזקציה אחת
- **תמיכה ב-WhatsApp ו-Telegram** - שני ערוצי תקשורת

## ארכיטקטורה

```
┌─────────────────┐     ┌─────────────────┐
│   WhatsApp      │     │    Telegram     │
│   Gateway       │     │    Bot API      │
└────────┬────────┘     └────────┬────────┘
         │                       │
         └───────────┬───────────┘
                     │
         ┌───────────▼───────────┐
         │     Bot Gateway       │
         │   (Webhooks Layer)    │
         └───────────┬───────────┘
                     │
         ┌───────────▼───────────┐
         │   Application Layer   │
         │  (State Machine/Flow) │
         └───────────┬───────────┘
                     │
         ┌───────────▼───────────┐
         │    Domain Layer       │
         │  (Business Logic)     │
         └───────────┬───────────┘
                     │
         ┌───────────▼───────────┐
         │     Data Layer        │
         │    (PostgreSQL)       │
         └───────────────────────┘
                     │
         ┌───────────▼───────────┐
         │    Task Queue         │
         │  (Celery + Redis)     │
         └───────────────────────┘
```

## טכנולוגיות

- **Backend**: FastAPI (Python)
- **Database**: PostgreSQL
- **Queue**: Redis + Celery
- **WhatsApp**: Node.js + WPPConnect
- **Deployment**: Render / Docker

## התקנה מהירה

### דרישות מקדימות

- Docker & Docker Compose
- Python 3.11+
- Node.js 18+ (עבור WhatsApp Gateway)

### הרצה עם Docker

```bash
# העתקת קובץ הגדרות
cp .env.example .env

# עריכת הגדרות (Telegram token וכו')
nano .env

# הרצת כל השירותים
docker-compose up -d
```

### הרצה מקומית (פיתוח)

```bash
# התקנת תלויות Python
pip install -r requirements.txt

# הרצת PostgreSQL ו-Redis
docker-compose up -d postgres redis

# הרצת מיגרציות
alembic upgrade head

# הרצת השרת
uvicorn app.main:app --reload

# הרצת Celery Worker (בטרמינל נפרד)
celery -A app.workers.celery_app worker --loglevel=info

# הרצת WhatsApp Gateway (בטרמינל נפרד)
cd whatsapp_gateway && npm install && npm start
```

## מבנה הפרויקט

```
├── app/
│   ├── api/
│   │   ├── routes/          # נתיבי API
│   │   └── webhooks/        # Webhooks לבוטים
│   ├── core/                # הגדרות
│   ├── db/
│   │   ├── models/          # מודלים של DB
│   │   └── repositories/    # שכבת גישה לנתונים
│   ├── domain/
│   │   └── services/        # לוגיקה עסקית
│   ├── state_machine/       # ניהול מצבי שיחה
│   └── workers/             # משימות Celery
├── whatsapp_gateway/        # שירות WhatsApp
├── docs/                    # תיעוד נוסף
├── tests/                   # בדיקות
├── schema.sql               # סכמת DB
├── docker-compose.yml
└── render.yaml              # הגדרות Render
```

## הגדרת Webhooks

### Telegram
```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://your-domain.com/api/telegram/webhook"
```

### WhatsApp
ה-WhatsApp Gateway יתחבר אוטומטית לאחר סריקת QR code.

## API Endpoints

| Method | Endpoint | תיאור |
|--------|----------|-------|
| POST | `/api/telegram/webhook` | Webhook לטלגרם |
| POST | `/api/whatsapp/webhook` | Webhook לווטסאפ |
| GET | `/api/deliveries` | רשימת משלוחים |
| POST | `/api/deliveries` | יצירת משלוח |
| POST | `/api/deliveries/{id}/capture` | תפיסת משלוח |
| GET | `/api/wallets/{courier_id}` | יתרת ארנק |

## תיעוד נוסף

- [ARCHITECTURE.md](./ARCHITECTURE.md) - ארכיטקטורת המערכת
- [STATE_MACHINE.md](./STATE_MACHINE.md) - דיאגרמות מצבים
- [DATABASE.md](./DATABASE.md) - תיעוד סכמת DB
- [DEPLOYMENT.md](./DEPLOYMENT.md) - מדריך העלאה ל-Render

## רישיון

MIT License
