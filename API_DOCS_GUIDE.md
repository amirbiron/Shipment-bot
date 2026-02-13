# מדריך לתיעוד ה-API - Shipment Bot

## מבוא

תיעוד ה-API נמצא בכתובת: `https://shipment-bot-api.onrender.com/docs`

זהו תיעוד אינטראקטיבי מבוסס **Swagger UI** שנוצר אוטומטית על ידי FastAPI, ומאפשר לך:
- לצפות בכל ה-endpoints הזמינים
- לראות את המבנה של הבקשות והתגובות
- **לנסות את ה-API ישירות מהדפדפן** (Try it out!)
- להבין את סכמות הנתונים (schemas)

---

## אימות (Authentication)

המערכת תומכת ב-3 שיטות אימות:

### 1. Admin API Key
**header:** `X-Admin-API-Key`
משמש עבור endpoints של אדמין: תחנות, דיבוג, מיגרציות.

### 2. JWT Token (Bearer)
**header:** `Authorization: Bearer <token>`
משמש עבור endpoints של פאנל ניהול תחנה. מתקבל לאחר אימות OTP.

### 3. OTP (One-Time Password)
קוד חד-פעמי בן 6 ספרות שנשלח לבעל התחנה דרך הבוט (Telegram/WhatsApp).
משמש לקבלת JWT token.

### כניסה מהירה ב-Swagger UI
ב-Swagger UI יש **ווידג'ט כניסה מהירה** שמאפשר:
- הזנת Admin API Key ישירות
- התחברות עם OTP לקבלת JWT
- הווידג'ט שומר את הטוקנים ב-session ומזריק אותם אוטומטית לכל הבקשות

---

## מתי כדאי להשתמש בתיעוד?

### 1. פיתוח ואינטגרציה
- כשאתה כותב קוד חדש שמשתמש ב-API
- כשאתה רוצה להבין איך endpoint מסוים עובד
- כשאתה צריך לראות דוגמאות של request/response
- **לפני שכותבים קריאת API חדשה** - בדוק מה הפרמטרים הנדרשים

### 2. בדיקות ידניות
- **במקום Postman** - אפשר לנסות endpoints ישירות מהדפדפן
- לבדוק שינויים שעשית בקוד
- לאמת שהולידציה עובדת כמו שצריך
- לבדוק error responses

### 3. הבנת המערכת
- כשמצטרף מפתח חדש לפרויקט
- כשצריך להבין את זרימת הנתונים
- לראות אילו שדות חובה ואילו אופציונליים
- להבין את המבנה של הבקשות

### 4. דיבאג ותיקון באגים
- לבדוק בדיוק איזה נתונים ה-API מצפה לקבל
- לראות את קודי השגיאה האפשריים (400, 401, 403, 404, 422, 500)
- לבדוק validation errors
- לוודא שהשדות נשלחים בפורמט הנכון

---

## מבנה התיעוד - סקירה מלאה

### תגיות (Tags) - קטגוריות של Endpoints

התיעוד מחולק לקטגוריות לפי תגיות:

#### 1. **Health** (בריאות)
בדיקות חיוּת (liveness) ומוכנות (readiness) של השרת.

#### 2. **Deliveries** (משלוחים)
כל הפעולות הקשורות לניהול משלוחים.

#### 3. **Users** (משתמשים)
ניהול משתמשים - שליחים ושולחים.

#### 4. **Wallets** (ארנקים)
ניהול ארנקים, יתרות וטרנזקציות של שליחים.

#### 5. **Stations** (תחנות)
ניהול תחנות משלוחים. דורש מפתח אדמין.

#### 6. **Webhooks** (ווב-הוקים)
endpoints לקבלת הודעות מ-WhatsApp ו-Telegram.

#### 7. **Migrations** (מיגרציות)
endpoints להרצת מיגרציות של מסד הנתונים.

#### 8. **Admin Debug** (דיאגנוסטיקה)
כלי דיאגנוסטיקה לאדמין: circuit breakers, הודעות כושלות, ומצב state machine של משתמשים.

#### 9. **Panel - אימות**
התחברות לפאנל ניהול תחנה באמצעות OTP ו-JWT.

#### 10. **Panel - דשבורד**
סיכום נתוני תחנה.

#### 11. **Panel - בעלים**
ניהול בעלי תחנה.

#### 12. **Panel - סדרנים**
ניהול סדרנים בתחנה (הוספה, הסרה, הוספה מרובה).

#### 13. **Panel - משלוחים**
צפייה במשלוחים פעילים והיסטוריה.

#### 14. **Panel - ארנק**
ארנק תחנה: יתרה והיסטוריית תנועות.

#### 15. **Panel - רשימה שחורה**
חסימת/שחרור נהגים בתחנה.

#### 16. **Panel - דוחות**
דוחות גבייה, הכנסות וייצוא CSV.

#### 17. **Panel - קבוצות**
הגדרות קבוצות Telegram/WhatsApp של התחנה.

---

## פירוט מלא של כל Endpoint

### Health Check

#### 1. בדיקת חיוּת (Liveness)

**כתובת:** `GET /health`
**אימות:** ללא
**תיאור:** בדיקה קלה שהתהליך חי ומגיב. משמש ל-Render/Load Balancer.
**תגובה:**
```json
{
  "status": "healthy"
}
```

**מתי להשתמש:**
- לוודא שהשרת עובד
- בכלי Monitoring
- לפני בדיקות אוטומטיות

---

#### 2. בדיקת מוכנות (Readiness)

**כתובת:** `GET /health/ready`
**אימות:** ללא
**תיאור:** בדיקה מקיפה של כל התלויות: DB, Redis, WhatsApp Gateway, Celery broker.

**תגובה תקינה (200):**
```json
{
  "status": "healthy",
  "db": "ok",
  "redis": "ok",
  "whatsapp_gateway": "ok",
  "celery": "ok"
}
```

**תגובה עם בעיה (503):**
```json
{
  "status": "degraded",
  "db": "ok",
  "redis": "ok",
  "whatsapp_gateway": "error: 404",
  "celery": "ok"
}
```

**מתי להשתמש:**
- לבדיקת מוכנות לפני הפניית תעבורה
- לדיאגנוסטיקה של תלויות חיצוניות
- ב-health checks של orchestrator

---

## Deliveries - ניהול משלוחים

### 1. יצירת משלוח חדש

**כתובת:** `POST /api/deliveries/`
**אימות:** ללא
**תיאור:** יצירת בקשת משלוח חדשה עם כתובות איסוף ומסירה

**Request Body:**
```json
{
  "sender_id": 123,
  "pickup_address": "רחוב הרצל 10, תל אביב",
  "dropoff_address": "שדרות רוטשילד 50, תל אביב",
  "pickup_contact_name": "יוסי כהן",
  "pickup_contact_phone": "0501234567",
  "pickup_notes": "קומה 3, דירה 5",
  "dropoff_contact_name": "דנה לוי",
  "dropoff_contact_phone": "0507654321",
  "dropoff_notes": "להתקשר כשמגיעים",
  "fee": 25.0
}
```

**שדות חובה:**
- `sender_id` - מזהה השולח
- `pickup_address` - כתובת איסוף מלאה
- `dropoff_address` - כתובת מסירה מלאה

**שדות אופציונליים:**
- `pickup_contact_name` - שם איש קשר לאיסוף
- `pickup_contact_phone` - טלפון איש קשר לאיסוף (יעבור נרמול וולידציה)
- `pickup_notes` - הערות לאיסוף
- `dropoff_contact_name` - שם איש קשר למסירה
- `dropoff_contact_phone` - טלפון איש קשר למסירה
- `dropoff_notes` - הערות למסירה
- `fee` - עמלת משלוח (ברירת מחדל: 10.0)

**ולידציות:**
- כתובות מנורמלות על ידי `AddressValidator`
- מספרי טלפון מנורמלים לפורמט בינלאומי (+972...)
- שמות מסוננים מ-XSS
- הערות מוגבלות ל-500 תווים
- עמלה בטווח 0-10,000 ש"ח

**תגובה מוצלחת (200):**
```json
{
  "id": 456,
  "sender_id": 123,
  "pickup_address": "רחוב הרצל 10, תל אביב",
  "dropoff_address": "שדרות רוטשילד 50, תל אביב",
  "status": "OPEN",
  "courier_id": null,
  "fee": 25.0
}
```

**שגיאות אפשריות:**
- `422` - שגיאת ולידציה (כתובת לא תקינה, מספר טלפון לא חוקי, וכו')

---

### 2. קבלת משלוחים פתוחים

**כתובת:** `GET /api/deliveries/open`
**אימות:** ללא
**תיאור:** מחזיר רשימה של כל המשלוחים עם סטטוס OPEN שטרם נתפסו

**תגובה מוצלחת (200):**
```json
[
  {
    "id": 456,
    "sender_id": 123,
    "pickup_address": "רחוב הרצל 10, תל אביב",
    "dropoff_address": "שדרות רוטשילד 50, תל אביב",
    "status": "OPEN",
    "courier_id": null,
    "fee": 25.0
  }
]
```

---

### 3. קבלת משלוח ספציפי

**כתובת:** `GET /api/deliveries/{delivery_id}`
**אימות:** ללא
**דוגמה:** `GET /api/deliveries/456`

**שגיאות אפשריות:**
- `404` - משלוח לא נמצא

---

### 4. תפיסת משלוח (Capture)

**כתובת:** `POST /api/deliveries/{delivery_id}/capture`
**אימות:** ללא
**תיאור:** הקצאת שליח למשלוח. פעולה אטומית הכוללת בדיקת אשראי, ניכוי עמלה והקצאת שליח.

**Request Body:**
```json
{
  "courier_id": 789
}
```

**מה קורה מאחורי הקלעים:**
1. בדיקה שהמשלוח פתוח (OPEN)
2. בדיקת אשראי של השליח (יתרה + credit_limit)
3. ניכוי העמלה מארנק השליח
4. עדכון סטטוס המשלוח ל-CAPTURED
5. הקצאת השליח למשלוח
6. יצירת רשומת ledger

**שגיאות אפשריות:**
- `400` - לא ניתן לתפוס (כבר נתפס, אין מספיק אשראי, וכו')
- `404` - משלוח לא נמצא

---

### 5. סימון משלוח כנמסר

**כתובת:** `POST /api/deliveries/{delivery_id}/deliver`
**אימות:** ללא
**תיאור:** סימון משלוח שנתפס כהושלם על ידי השליח

**שגיאות אפשריות:**
- `400` - לא ניתן לסמן כנמסר (סטטוס לא תקין)

---

### 6. ביטול משלוח

**כתובת:** `DELETE /api/deliveries/{delivery_id}`
**אימות:** ללא
**תיאור:** ביטול משלוח פתוח שטרם נתפס

**שגיאות אפשריות:**
- `400` - לא ניתן לבטל (כבר נתפס או נמסר)

---

## Users - ניהול משתמשים

### 1. יצירת משתמש חדש

**כתובת:** `POST /api/users/`
**אימות:** ללא
**תיאור:** יצירת משתמש חדש במערכת

**Request Body:**
```json
{
  "phone_number": "0501234567",
  "name": "יוסי כהן",
  "role": "sender",
  "platform": "whatsapp",
  "telegram_chat_id": null
}
```

**שדות חובה:**
- `phone_number` - מספר טלפון (יעבור נרמול לפורמט +972...)

**שדות אופציונליים:**
- `name` - שם (יעבור סניטציה)
- `role` - תפקיד: `sender` או `courier` (ברירת מחדל: `sender`)
- `platform` - פלטפורמה: `whatsapp` או `telegram` (ברירת מחדל: `whatsapp`)
- `telegram_chat_id` - מזהה צ'אט בטלגרם (רק למשתמשי טלגרם)

**שגיאות אפשריות:**
- `400` - משתמש כבר קיים
- `422` - שגיאת ולידציה

---

### 2. קבלת משתמש לפי ID

**כתובת:** `GET /api/users/{user_id}`
**אימות:** ללא
**דוגמה:** `GET /api/users/123`

**שגיאות אפשריות:**
- `404` - משתמש לא נמצא

---

### 3. קבלת משתמש לפי מספר טלפון

**כתובת:** `GET /api/users/phone/{phone_number}`
**אימות:** ללא
**דוגמה:** `GET /api/users/phone/0501234567`

---

### 4. קבלת כל השליחים הפעילים

**כתובת:** `GET /api/users/couriers/`
**אימות:** ללא
**תיאור:** מחזיר רשימה של כל השליחים עם `role=COURIER` ו-`is_active=true`

---

### 5. עדכון משתמש

**כתובת:** `PATCH /api/users/{user_id}`
**אימות:** ללא
**תיאור:** עדכון פרטי משתמש

**אפשרויות שליחה:**
1. **Query Parameters (תמיכה לאחור):**
   ```
   PATCH /api/users/123?name=שם חדש&is_active=false
   ```

2. **Request Body (מומלץ):**
   ```json
   {
     "name": "שם חדש",
     "is_active": false
   }
   ```

**שדות שניתן לעדכן:**
- `name` - שם (יעבור ולידציה וסניטציה)
- `is_active` - האם פעיל

**שגיאות אפשריות:**
- `404` - משתמש לא נמצא
- `422` - שגיאת ולידציה בשם

---

## Wallets - ניהול ארנקים

### 1. קבלת ארנק של שליח

**כתובת:** `GET /api/wallets/{courier_id}`
**אימות:** ללא
**תיאור:** מחזיר את הארנק של השליח, או יוצר חדש אם לא קיים

**תגובה מוצלחת (200):**
```json
{
  "courier_id": 789,
  "balance": -150.0,
  "credit_limit": -500.0
}
```

**הסבר:**
- `balance` - היתרה הנוכחית (שלילית = חוב)
- `credit_limit` - מגבלת האשראי (שלילית = עד כמה ניתן להיות בחוב)

---

### 2. קבלת יתרה נוכחית

**כתובת:** `GET /api/wallets/{courier_id}/balance`
**אימות:** ללא
**תיאור:** מחזיר רק את היתרה

---

### 3. קבלת היסטוריית טרנזקציות

**כתובת:** `GET /api/wallets/{courier_id}/history?limit=20`
**אימות:** ללא

**פרמטרים:**
- `limit` - מספר רשומות מקסימלי (ברירת מחדל: 20)

**סוגי טרנזקציות:**
- `capture` - תפיסת משלוח (ניכוי)
- `deposit` - הפקדה (הוספה)
- `refund` - החזר (הוספה)

---

### 4. בדיקה אם שליח יכול לתפוס משלוח

**כתובת:** `GET /api/wallets/{courier_id}/can-capture?fee=25.0`
**אימות:** ללא

**פרמטרים:**
- `fee` - עמלת המשלוח (ברירת מחדל: 10.0)

**תגובה מוצלחת (200):**
```json
{
  "can_capture": true,
  "message": "יש מספיק אשראי"
}
```

---

## Stations - ניהול תחנות

> כל ה-endpoints דורשים אימות אדמין (`X-Admin-API-Key`).

### 1. רשימת כל התחנות הפעילות

**כתובת:** `GET /api/stations/`
**אימות:** Admin API Key

**תגובה מוצלחת (200):**
```json
{
  "stations": [
    {
      "id": 1,
      "name": "תחנת תל אביב",
      "owner_id": 42,
      "is_active": true
    }
  ],
  "total": 1
}
```

---

### 2. יצירת תחנה חדשה

**כתובת:** `POST /api/stations/`
**אימות:** Admin API Key
**תיאור:** יצירת תחנה חדשה והקצאת בעלים לפי מספר טלפון. המשתמש הופך אוטומטית ל-STATION_OWNER.

**Request Body:**
```json
{
  "name": "תחנת חיפה",
  "owner_phone": "0501234567"
}
```

**שדות חובה:**
- `name` - שם התחנה (לפחות 2 תווים)
- `owner_phone` - מספר טלפון של בעל התחנה (אם לא קיים - ייווצר אוטומטית)

**שגיאות אפשריות:**
- `400` - למשתמש כבר יש תחנה פעילה
- `401` - מפתח API חסר
- `403` - מפתח API שגוי
- `422` - שגיאת ולידציה

---

### 3. קבלת תחנה לפי מזהה

**כתובת:** `GET /api/stations/{station_id}`
**אימות:** Admin API Key

**שגיאות אפשריות:**
- `404` - תחנה לא נמצאה

---

## Webhooks - קבלת הודעות

### 1. WhatsApp Webhook

**כתובת:** `POST /api/whatsapp/webhook`
**אימות:** ללא
**תיאור:** מקבל הודעות מה-WhatsApp Gateway (Node.js microservice)

**Request Body:**
```json
{
  "messages": [
    {
      "from_number": "972501234567@c.us",
      "sender_id": "972501234567",
      "reply_to": "972501234567@c.us",
      "message_id": "msg_12345",
      "text": "שלום",
      "timestamp": 1234567890,
      "media_url": "https://example.com/image.jpg",
      "media_type": "image/jpeg"
    }
  ]
}
```

**Webhook Verification:**
```
GET /api/whatsapp/webhook?hub_mode=subscribe&hub_challenge=123&hub_verify_token=token
```
מחזיר את hub_challenge לאימות.

**מתי להשתמש:**
- נקרא אוטומטית על ידי WhatsApp Gateway - אין לקרוא ידנית!

---

### 2. Telegram Webhook

**כתובת:** `POST /api/telegram/webhook`
**אימות:** ללא
**תיאור:** מקבל עדכונים מ-Telegram Bot API

**פקודות מיוחדות:**
- `/start` - איפוס למצב התחלתי
- `#` - חזרה לתפריט ראשי

**מתי להשתמש:**
- נקרא אוטומטית על ידי Telegram - אין לקרוא ידנית!

---

## Migrations - מיגרציות

### 1. מיגרציה 001 (שדות הרשמת שליחים)

**כתובת:** `POST /api/migrations/run-migration-001`
**אימות:** ללא
**תיאור:** מוסיפה שדות הרשמת שליחים לטבלת users

**מה המיגרציה עושה:**
1. יוצרת enum type `approval_status`
2. מוסיפה עמודות: `full_name`, `approval_status`, `id_document_url`, `service_area`, `terms_accepted_at`
3. יוצרת אינדקס על `approval_status`
4. מגדירה credit_limit ברירת מחדל ל-500-

**תגובה מוצלחת (200):**
```json
{
  "success": true,
  "message": "Migration 001 completed successfully - courier fields added"
}
```

---

### 2. מיגרציה 002 (שדות KYC לשליחים)

**כתובת:** `POST /api/migrations/run-migration-002`
**אימות:** ללא
**תיאור:** מוסיפה שדות KYC חדשים לטבלת users.

**מה המיגרציה עושה:**
- מוסיפה עמודות: `selfie_file_id`, `vehicle_category`, `vehicle_photo_file_id`

**תגובה מוצלחת (200):**
```json
{
  "success": true,
  "message": "Migration 002 completed successfully - KYC fields added (selfie_file_id, vehicle_category, vehicle_photo_file_id)"
}
```

---

### 3. מיגרציה 003 (טבלאות תחנות + enum station_owner)

**כתובת:** `POST /api/migrations/run-migration-003`
**אימות:** ללא
**תיאור:** יוצרת טבלאות תחנות, סדרנים, ארנק תחנה, חיובים ידניים ורשימה שחורה. מוסיפה את הערך `station_owner` ל-enum של userrole.

**תגובה מוצלחת (200):**
```json
{
  "success": true,
  "message": "Migration 003 completed successfully - station tables created, station_owner enum value added"
}
```

**הערה:** כל המיגרציות בטוחות להרצה מספר פעמים (משתמשות ב-IF NOT EXISTS). בנוסף, המיגרציות רצות אוטומטית ב-startup של האפליקציה על PostgreSQL.

---

## Admin Debug - כלי דיאגנוסטיקה

> כל ה-endpoints דורשים אימות אדמין (`X-Admin-API-Key`).

### 1. סטטוס Circuit Breakers

**כתובת:** `GET /api/admin/debug/circuit-breakers`
**אימות:** Admin API Key
**תיאור:** מחזיר את המצב הנוכחי של כל circuit breaker רשום (Telegram, WhatsApp, WhatsApp Admin).

**תגובה מוצלחת (200):**
```json
[
  {
    "service": "telegram",
    "state": "closed",
    "failure_count": 0,
    "success_count": 42,
    "half_open_calls": 0,
    "retry_after_seconds": 0.0
  },
  {
    "service": "whatsapp",
    "state": "open",
    "failure_count": 5,
    "success_count": 0,
    "half_open_calls": 0,
    "retry_after_seconds": 28.5
  }
]
```

**הסבר מצבים:**
- `closed` - תקין, קריאות עוברות
- `open` - מושבת, קריאות נחסמות עד `retry_after_seconds`
- `half_open` - בודק אם השירות התאושש

---

### 2. סיכום הודעות Outbox

**כתובת:** `GET /api/admin/debug/outbox/summary`
**אימות:** Admin API Key
**תיאור:** ספירה כמותית של הודעות outbox לפי סטטוס.

**תגובה מוצלחת (200):**
```json
{
  "pending": 3,
  "processing": 1,
  "sent": 1520,
  "failed": 7,
  "total": 1531
}
```

---

### 3. שאילתת הודעות Outbox

**כתובת:** `GET /api/admin/debug/outbox/messages`
**אימות:** Admin API Key

**פרמטרים:**
- `message_status` - סינון לפי סטטוס: `pending`, `processing`, `sent`, `failed` (ברירת מחדל: `failed`)
- `limit` - מספר הודעות מקסימלי: 1-200 (ברירת מחדל: 50)

**תגובה מוצלחת (200):**
```json
[
  {
    "id": 42,
    "platform": "telegram",
    "recipient_id": "123456789",
    "message_type": "delivery_notification",
    "status": "failed",
    "retry_count": 3,
    "max_retries": 5,
    "last_error": "Telegram API: 429 Too Many Requests",
    "next_retry_at": "2026-02-13T10:30:00",
    "created_at": "2026-02-13T10:00:00",
    "processed_at": null
  }
]
```

---

### 4. Retry ידני להודעה כושלת

**כתובת:** `POST /api/admin/debug/outbox/messages/{message_id}/retry`
**אימות:** Admin API Key
**תיאור:** מאפס את סטטוס ההודעה ל-pending כדי ש-worker ישלח אותה מחדש. עובד רק על הודעות בסטטוס `failed`.

**תגובה מוצלחת (200):**
```json
{
  "message_id": 42,
  "previous_status": "failed",
  "new_status": "pending",
  "retry_count": 3
}
```

**שגיאות אפשריות:**
- `400` - ההודעה לא בסטטוס `failed`
- `404` - הודעה לא נמצאה

---

### 5. בדיקת מצב State Machine של משתמש

**כתובת:** `GET /api/admin/debug/users/{user_id}/state`
**אימות:** Admin API Key
**תיאור:** מחזיר את המצב הנוכחי של שיחת המשתמש כולל context data. שימושי לדיבוג משתמשים שתקועים בזרימה.

**פרמטרים:**
- `platform` (אופציונלי) - סינון לפי פלטפורמה (`telegram` או `whatsapp`). ברירת מחדל: מחזיר את ה-session שעודכן לאחרונה.

**תגובה מוצלחת (200):**
```json
{
  "user_id": 123,
  "user_name": "יוסי כהן",
  "user_role": "sender",
  "platform": "telegram",
  "current_state": "SENDER.DELIVERY_PICKUP_CITY",
  "context_data": {
    "pickup_city": "תל אביב"
  },
  "updated_at": "2026-02-13T10:00:00",
  "last_activity_at": "2026-02-13T10:05:00"
}
```

**שגיאות אפשריות:**
- `404` - משתמש או session לא נמצאו

---

### 6. איפוס כפוי של State Machine

**כתובת:** `POST /api/admin/debug/users/{user_id}/force-state`
**אימות:** Admin API Key
**תיאור:** מאפס את מצב ה-state machine של משתמש למצב חדש. עוקף ולידציית מעברים. שימושי לשחרור משתמשים שתקועים בזרימה שבורה.

**Request Body:**
```json
{
  "platform": "telegram",
  "new_state": "SENDER.MENU",
  "clear_context": true
}
```

**שדות:**
- `platform` - `telegram` או `whatsapp` (חובה)
- `new_state` - המצב החדש (חובה)
- `clear_context` - האם לנקות את context_data (ברירת מחדל: `true`)

**שגיאות אפשריות:**
- `404` - משתמש או session לא נמצאו

---

## Panel - אימות (פאנל ניהול תחנה)

> פאנל הניהול פועל כ-SPA (Single Page Application) ב-`/panel`.
> כל endpoints של הפאנל (למעט אימות) דורשים JWT token.

### 1. בקשת קוד כניסה (OTP)

**כתובת:** `POST /api/panel/auth/request-otp`
**אימות:** ללא
**תיאור:** שולח קוד OTP בן 6 ספרות לבעל התחנה דרך הבוט (Telegram/WhatsApp). תשובה גנרית למניעת חשיפת מידע (user-enumeration).

**Request Body:**
```json
{
  "phone_number": "0501234567"
}
```

**תגובה (200 — תמיד):**
```json
{
  "success": true,
  "message": "אם המספר רשום במערכת ויש לו הרשאה, קוד כניסה יישלח בקרוב"
}
```

**שגיאות אפשריות:**
- `429` - בקשת OTP מוקדמת מדי (rate limiting: דקה בין בקשות)

---

### 2. אימות קוד כניסה

**כתובת:** `POST /api/panel/auth/verify-otp`
**אימות:** ללא
**תיאור:** אימות קוד OTP וקבלת JWT token. אם למשתמש יש כמה תחנות, מחזיר רשימה לבחירה.

**Request Body:**
```json
{
  "phone_number": "0501234567",
  "otp": "123456",
  "station_id": null
}
```

**תגובת התחברות מוצלחת (200):**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "station_id": 1,
  "station_name": "תחנת תל אביב"
}
```

**תגובת בחירת תחנה (200) — כשיש כמה תחנות:**
```json
{
  "choose_station": true,
  "stations": [
    { "station_id": 1, "station_name": "תחנת תל אביב" },
    { "station_id": 2, "station_name": "תחנת חיפה" }
  ]
}
```
במקרה זה, יש לשלוח שוב עם `station_id` שנבחר.

**שגיאות אפשריות:**
- `401` - קוד שגוי, פג תוקף, או משתמש לא זוהה
- `403` - אין הרשאה לתחנה שנבחרה

---

### 3. פרטי המשתמש המחובר

**כתובת:** `GET /api/panel/auth/me`
**אימות:** JWT Token
**תיאור:** מחזיר פרטי המשתמש והתחנה של הטוקן הנוכחי.

**תגובה מוצלחת (200):**
```json
{
  "user_id": 42,
  "station_id": 1,
  "station_name": "תחנת תל אביב",
  "role": "station_owner"
}
```

---

### 4. רענון טוקן

**כתובת:** `POST /api/panel/auth/refresh`
**אימות:** ללא (הטוקן בגוף הבקשה)
**תיאור:** שליחת refresh token לקבלת access token חדש + refresh token חדש. ה-refresh token הישן נמחק (rotation) — כל טוקן חד-פעמי.

**Request Body:**
```json
{
  "refresh_token": "eyJ..."
}
```

**שגיאות אפשריות:**
- `401` - refresh token לא תקין או פג תוקף
- `403` - המשתמש/תחנה לא פעילים

---

## Panel - דשבורד

### נתוני דשבורד תחנה

**כתובת:** `GET /api/panel/dashboard`
**אימות:** JWT Token
**תיאור:** סיכום נתונים מרכזיים: משלוחים פעילים, סטטיסטיקות יומיות, ארנק, סדרנים.

**תגובה מוצלחת (200):**
```json
{
  "station_name": "תחנת תל אביב",
  "active_deliveries_count": 12,
  "today_deliveries_count": 28,
  "today_delivered_count": 15,
  "wallet_balance": 3450.0,
  "commission_rate": 0.1,
  "today_revenue": 420.0,
  "active_dispatchers_count": 5,
  "blacklisted_count": 2
}
```

---

## Panel - בעלים

> כל ה-endpoints דורשים JWT Token.

### 1. רשימת בעלים

**כתובת:** `GET /api/panel/owners`
**אימות:** JWT Token
**תיאור:** מחזיר את כל הבעלים הפעילים בתחנה.

**תגובה מוצלחת (200):**
```json
[
  {
    "user_id": 42,
    "name": "יוסי כהן",
    "phone_masked": "+97250123****",
    "is_active": true,
    "created_at": "2026-01-15T10:00:00"
  }
]
```

---

### 2. הוספת בעלים

**כתובת:** `POST /api/panel/owners`
**אימות:** JWT Token

**Request Body:**
```json
{
  "phone_number": "0501234567"
}
```

**שגיאות אפשריות:**
- `400` - שגיאה בהוספה

---

### 3. הסרת בעלים

**כתובת:** `DELETE /api/panel/owners/{user_id}`
**אימות:** JWT Token
**תיאור:** הסרת בעלים מהתחנה. לא ניתן להסיר את הבעלים האחרון.

**שגיאות אפשריות:**
- `400` - לא ניתן להסיר (בעלים אחרון או לא נמצא)

---

## Panel - סדרנים

> כל ה-endpoints דורשים JWT Token.

### 1. רשימת סדרנים

**כתובת:** `GET /api/panel/dispatchers`
**אימות:** JWT Token
**תיאור:** מחזיר את כל הסדרנים הפעילים בתחנה.

**תגובה מוצלחת (200):**
```json
[
  {
    "user_id": 55,
    "name": "דני סדרן",
    "phone_masked": "+97250765****",
    "is_active": true,
    "created_at": "2026-01-20T08:00:00"
  }
]
```

---

### 2. הוספת סדרן

**כתובת:** `POST /api/panel/dispatchers`
**אימות:** JWT Token

**Request Body:**
```json
{
  "phone_number": "0507654321"
}
```

**שגיאות אפשריות:**
- `400` - שגיאה בהוספה
- `422` - שגיאת ולידציה

---

### 3. הוספת סדרנים בכמות

**כתובת:** `POST /api/panel/dispatchers/bulk`
**אימות:** JWT Token
**תיאור:** הוספת כמה סדרנים בפעולה אחת (מקסימום 50). מחזיר תוצאה מפורטת לכל מספר.

**Request Body:**
```json
{
  "phone_numbers": ["0501111111", "0502222222", "0503333333"]
}
```

**תגובה מוצלחת (200):**
```json
{
  "results": [
    { "phone_masked": "+97250111****", "success": true, "message": "הסדרן נוסף בהצלחה" },
    { "phone_masked": "+97250222****", "success": false, "message": "הסדרן כבר קיים בתחנה" },
    { "phone_masked": "+97250333****", "success": true, "message": "הסדרן נוסף בהצלחה" }
  ],
  "total": 3,
  "success_count": 2
}
```

---

### 4. הסרת סדרן

**כתובת:** `DELETE /api/panel/dispatchers/{user_id}`
**אימות:** JWT Token

**שגיאות אפשריות:**
- `400` - הסדרן לא נמצא

---

## Panel - משלוחים

> כל ה-endpoints דורשים JWT Token.

### 1. משלוחים פעילים

**כתובת:** `GET /api/panel/deliveries/active`
**אימות:** JWT Token

**פרמטרים:**
- `page` - מספר עמוד (ברירת מחדל: 1)
- `page_size` - פריטים בעמוד: 1-100 (ברירת מחדל: 20)

**תגובה מוצלחת (200):**
```json
{
  "items": [
    {
      "id": 456,
      "pickup_address": "רחוב הרצל 10, תל אביב",
      "dropoff_address": "שדרות רוטשילד 50, תל אביב",
      "status": "captured",
      "fee": 25.0,
      "courier_name": "דני שליח",
      "sender_name": "יוסי כהן",
      "created_at": "2026-02-13T10:00:00",
      "delivered_at": null
    }
  ],
  "total": 12,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

---

### 2. היסטוריית משלוחים

**כתובת:** `GET /api/panel/deliveries/history`
**אימות:** JWT Token

**פרמטרים:**
- `page`, `page_size` - pagination
- `status_filter` - סטטוס לסינון (`open`/`captured`/`delivered`/`cancelled`)
- `date_from` - מתאריך (YYYY-MM-DD)
- `date_to` - עד תאריך (YYYY-MM-DD)

**תגובה:** מבנה זהה למשלוחים פעילים. ברירת מחדל: רק משלוחים שהסתיימו (delivered/cancelled).

---

### 3. פרטי משלוח

**כתובת:** `GET /api/panel/deliveries/{delivery_id}`
**אימות:** JWT Token
**תיאור:** פרטי משלוח מלאים. מספרי טלפון של אנשי קשר ממוסכים (4 ספרות אחרונות מוסתרות).

**תגובה מוצלחת (200):**
```json
{
  "id": 456,
  "pickup_address": "רחוב הרצל 10, תל אביב",
  "pickup_contact_name": "יוסי כהן",
  "pickup_contact_phone": "+97250123****",
  "dropoff_address": "שדרות רוטשילד 50, תל אביב",
  "dropoff_contact_name": "דנה לוי",
  "dropoff_contact_phone": "+97250765****",
  "status": "captured",
  "fee": 25.0,
  "courier_name": "דני שליח",
  "sender_name": "יוסי כהן",
  "created_at": "2026-02-13T10:00:00",
  "captured_at": "2026-02-13T10:05:00",
  "delivered_at": null
}
```

**שגיאות אפשריות:**
- `404` - משלוח לא נמצא (כולל משלוח שלא שייך לתחנה)

---

## Panel - ארנק

> כל ה-endpoints דורשים JWT Token.

### 1. יתרת ארנק

**כתובת:** `GET /api/panel/wallet`
**אימות:** JWT Token

**תגובה מוצלחת (200):**
```json
{
  "balance": 3450.0,
  "commission_rate": 0.1
}
```

---

### 2. היסטוריית תנועות ארנק

**כתובת:** `GET /api/panel/wallet/ledger`
**אימות:** JWT Token

**פרמטרים:**
- `page`, `page_size` - pagination
- `entry_type` - סוג תנועה: `commission_credit`/`manual_charge`/`withdrawal`
- `date_from` - מתאריך (YYYY-MM-DD)
- `date_to` - עד תאריך (YYYY-MM-DD)

**תגובה מוצלחת (200):**
```json
{
  "items": [
    {
      "id": 1,
      "entry_type": "commission_credit",
      "amount": 2.5,
      "balance_after": 3450.0,
      "description": "עמלה על משלוח #456",
      "created_at": "2026-02-13T10:05:00"
    }
  ],
  "total": 150,
  "page": 1,
  "page_size": 20,
  "total_pages": 8,
  "summary": {
    "commission_credit": 3200.0,
    "manual_charge": 500.0,
    "withdrawal": -250.0
  }
}
```

---

## Panel - רשימה שחורה

> כל ה-endpoints דורשים JWT Token.

### 1. רשימת נהגים חסומים

**כתובת:** `GET /api/panel/blacklist`
**אימות:** JWT Token

**תגובה מוצלחת (200):**
```json
[
  {
    "courier_id": 99,
    "name": "משה נהג",
    "phone_masked": "+97250999****",
    "reason": "לא הגיע למשלוחים",
    "blocked_at": "2026-02-10T14:00:00"
  }
]
```

---

### 2. הוספה לרשימה שחורה

**כתובת:** `POST /api/panel/blacklist`
**אימות:** JWT Token

**Request Body:**
```json
{
  "phone_number": "0509998888",
  "reason": "לא הגיע למשלוחים"
}
```

**שגיאות אפשריות:**
- `400` - שגיאה בהוספה (כבר חסום, לא נמצא, וכו')

---

### 3. הוספה מרובה לרשימה שחורה

**כתובת:** `POST /api/panel/blacklist/bulk`
**אימות:** JWT Token
**תיאור:** חסימת כמה נהגים בפעולה אחת (מקסימום 50).

**Request Body:**
```json
{
  "entries": [
    { "phone_number": "0501111111", "reason": "סיבה 1" },
    { "phone_number": "0502222222", "reason": "סיבה 2" }
  ]
}
```

**תגובה:** מבנה זהה להוספה מרובה של סדרנים (results, total, success_count).

---

### 4. הסרה מרשימה שחורה

**כתובת:** `DELETE /api/panel/blacklist/{courier_id}`
**אימות:** JWT Token

**שגיאות אפשריות:**
- `400` - הנהג לא נמצא ברשימה

---

## Panel - דוחות

> כל ה-endpoints דורשים JWT Token.

### 1. דוח גבייה

**כתובת:** `GET /api/panel/reports/collection`
**אימות:** JWT Token
**תיאור:** דוח חובות נהגים לתחנה במחזור חיוב ספציפי.

**פרמטרים:**
- `cycle_start` (אופציונלי) - תחילת מחזור (YYYY-MM-DD). ברירת מחדל: מחזור נוכחי.

**תגובה מוצלחת (200):**
```json
{
  "items": [
    {
      "driver_name": "דני שליח",
      "total_debt": 450.0,
      "charge_count": 18
    }
  ],
  "total_debt": 450.0,
  "cycle_start": "2026-02-01",
  "cycle_end": "2026-03-01"
}
```

---

### 2. ייצוא דוח גבייה ל-CSV

**כתובת:** `GET /api/panel/reports/collection/export`
**אימות:** JWT Token
**תיאור:** מייצא את דוח הגבייה כקובץ CSV (עם תמיכה בעברית ב-Excel — BOM).

**פרמטרים:**
- `cycle_start` (אופציונלי) - תחילת מחזור (YYYY-MM-DD)

**תגובה:** קובץ CSV עם headers:
```
שם נהג, סה"כ חוב, מספר חיובים
```

---

### 3. דוח הכנסות

**כתובת:** `GET /api/panel/reports/revenue`
**אימות:** JWT Token
**תיאור:** סיכום הכנסות התחנה בטווח תאריכים.

**פרמטרים:**
- `date_from` (אופציונלי) - מתאריך (YYYY-MM-DD). ברירת מחדל: תחילת החודש.
- `date_to` (אופציונלי) - עד תאריך (YYYY-MM-DD). ברירת מחדל: היום.

**תגובה מוצלחת (200):**
```json
{
  "total_commissions": 3200.0,
  "total_manual_charges": 500.0,
  "total_withdrawals": 250.0,
  "net_total": 3450.0,
  "date_from": "2026-02-01",
  "date_to": "2026-02-13"
}
```

---

## Panel - קבוצות

> כל ה-endpoints דורשים JWT Token.

### 1. הגדרות קבוצות

**כתובת:** `GET /api/panel/groups`
**אימות:** JWT Token
**תיאור:** מחזיר הגדרות קבוצות נוכחיות של התחנה (ציבורית/פרטית).

**תגובה מוצלחת (200):**
```json
{
  "public_group_chat_id": "-1001234567890",
  "public_group_platform": "telegram",
  "private_group_chat_id": "-1009876543210",
  "private_group_platform": "telegram"
}
```

---

### 2. עדכון הגדרות קבוצות

**כתובת:** `PUT /api/panel/groups`
**אימות:** JWT Token
**תיאור:** עדכון מזהי קבוצות התחנה.

**Request Body:**
```json
{
  "public_group_chat_id": "-1001234567890",
  "public_group_platform": "telegram",
  "private_group_chat_id": "-1009876543210",
  "private_group_platform": "telegram"
}
```

כל השדות אופציונליים. `platform` חייב להיות `telegram` או `whatsapp`.

**שגיאות אפשריות:**
- `400` - שגיאה בעדכון

---

## תכונות מתקדמות בתיעוד

### 1. Try it out! (נסה זאת!)

בכל endpoint יש כפתור **"Try it out"** שמאפשר:
- למלא את הפרמטרים
- לשלוח בקשה אמיתית לשרת
- לראות את התגובה בזמן אמת

**איך להשתמש:**
1. לחץ על endpoint
2. לחץ "Try it out"
3. מלא את הפרמטרים הנדרשים
4. לחץ "Execute"
5. ראה את התוצאה ב-"Responses"

---

### 2. Schemas (סכמות)

בתחתית העמוד יש **"Schemas"** שמציג:
- `DeliveryCreate` / `DeliveryResponse` - משלוחים
- `UserCreate` / `UserResponse` - משתמשים
- `StationCreate` / `StationResponse` - תחנות
- `OTPRequest` / `TokenResponse` - אימות פאנל
- `DashboardResponse` - דשבורד
- `CollectionReportResponse` / `RevenueReportResponse` - דוחות
- ועוד...

---

### 3. Response Codes

כל endpoint מציג את קודי התגובה האפשריים:
- **200** - הצלחה
- **400** - Bad Request (לדוגמה: לא ניתן לבטל משלוח)
- **401** - Unauthorized (חסר אימות)
- **403** - Forbidden (אין הרשאה)
- **404** - Not Found (המשאב לא נמצא)
- **422** - Validation Error (שגיאת ולידציה)
- **429** - Too Many Requests (rate limiting)
- **500** - Server Error (שגיאת שרת)
- **503** - Service Unavailable (שרת לא זמין — readiness probe)

---

### 4. ReDoc (תיעוד חלופי)

יש גם תיעוד ב-ReDoc בכתובת: `https://shipment-bot-api.onrender.com/redoc`

**הבדלים:**
- Swagger UI - אינטראקטיבי, אפשר לנסות
- ReDoc - נקי יותר, טוב לקריאה

---

## טיפים לשימוש

### 1. פיתוח מקומי
```bash
# הרצת השרת מקומית
uvicorn app.main:app --reload

# התיעוד יהיה זמין ב:
http://localhost:8000/docs
```

### 2. בדיקת Validation
נסה לשלוח נתונים לא תקינים ב-"Try it out" כדי לראות את הולידציה בפעולה:
- מספר טלפון לא תקין
- כתובת ריקה
- עמלה שלילית

### 3. קריאת Errors
כשיש שגיאה 422, התיעוד מציג:
```json
{
  "detail": [
    {
      "loc": ["body", "phone_number"],
      "msg": "Invalid phone number format",
      "type": "value_error"
    }
  ]
}
```

### 4. Copy as cURL
בכל endpoint, אחרי "Execute", יש אפשרות ל-"Copy as cURL" - מועיל להעתקת הקריאה לטרמינל.

---

## סיכום - מתי להשתמש בכל endpoint

### Health

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /health` | ללא | בדיקת חיוּת (liveness) |
| `GET /health/ready` | ללא | בדיקת מוכנות (readiness) — בודק DB, Redis, WhatsApp, Celery |

### Deliveries

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `POST /api/deliveries/` | ללא | יצירת משלוח חדש |
| `GET /api/deliveries/open` | ללא | משלוחים זמינים לתפיסה |
| `GET /api/deliveries/{id}` | ללא | פרטי משלוח ספציפי |
| `POST /api/deliveries/{id}/capture` | ללא | שליח תופס משלוח |
| `POST /api/deliveries/{id}/deliver` | ללא | שליח מסיים משלוח |
| `DELETE /api/deliveries/{id}` | ללא | ביטול משלוח פתוח |

### Users

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `POST /api/users/` | ללא | יצירת משתמש (נדיר — בד"כ נוצר אוטומטית) |
| `GET /api/users/{id}` | ללא | קבלת פרטי משתמש |
| `GET /api/users/phone/{phone}` | ללא | חיפוש משתמש לפי טלפון |
| `GET /api/users/couriers/` | ללא | רשימת כל השליחים |
| `PATCH /api/users/{id}` | ללא | עדכון שם/סטטוס |

### Wallets

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/wallets/{id}` | ללא | ארנק שליח |
| `GET /api/wallets/{id}/balance` | ללא | יתרה בלבד |
| `GET /api/wallets/{id}/history` | ללא | היסטוריית תנועות |
| `GET /api/wallets/{id}/can-capture` | ללא | בדיקת אשראי |

### Stations

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/stations/` | Admin API Key | רשימת תחנות פעילות |
| `POST /api/stations/` | Admin API Key | יצירת תחנה חדשה |
| `GET /api/stations/{id}` | Admin API Key | פרטי תחנה |

### Webhooks

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `POST /api/whatsapp/webhook` | ללא | (אוטומטי) קבלת הודעות WhatsApp |
| `GET /api/whatsapp/webhook` | ללא | (אוטומטי) אימות webhook |
| `POST /api/telegram/webhook` | ללא | (אוטומטי) קבלת הודעות Telegram |

### Migrations

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `POST /api/migrations/run-migration-001` | ללא | שדות הרשמת שליחים |
| `POST /api/migrations/run-migration-002` | ללא | שדות KYC |
| `POST /api/migrations/run-migration-003` | ללא | טבלאות תחנות + enum |

### Admin Debug

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/admin/debug/circuit-breakers` | Admin API Key | סטטוס circuit breakers |
| `GET /api/admin/debug/outbox/summary` | Admin API Key | סיכום הודעות outbox |
| `GET /api/admin/debug/outbox/messages` | Admin API Key | שאילתת הודעות כושלות |
| `POST /api/admin/debug/outbox/messages/{id}/retry` | Admin API Key | retry ידני להודעה |
| `GET /api/admin/debug/users/{id}/state` | Admin API Key | מצב state machine |
| `POST /api/admin/debug/users/{id}/force-state` | Admin API Key | איפוס כפוי של state |

### Panel - אימות

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `POST /api/panel/auth/request-otp` | ללא | בקשת קוד כניסה |
| `POST /api/panel/auth/verify-otp` | ללא | אימות OTP וקבלת JWT |
| `GET /api/panel/auth/me` | JWT | פרטי המשתמש המחובר |
| `POST /api/panel/auth/refresh` | ללא | רענון טוקן |

### Panel - דשבורד

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/panel/dashboard` | JWT | סיכום נתוני תחנה |

### Panel - בעלים

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/panel/owners` | JWT | רשימת בעלים |
| `POST /api/panel/owners` | JWT | הוספת בעלים |
| `DELETE /api/panel/owners/{user_id}` | JWT | הסרת בעלים |

### Panel - סדרנים

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/panel/dispatchers` | JWT | רשימת סדרנים |
| `POST /api/panel/dispatchers` | JWT | הוספת סדרן |
| `POST /api/panel/dispatchers/bulk` | JWT | הוספת סדרנים בכמות (עד 50) |
| `DELETE /api/panel/dispatchers/{user_id}` | JWT | הסרת סדרן |

### Panel - משלוחים

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/panel/deliveries/active` | JWT | משלוחים פעילים עם pagination |
| `GET /api/panel/deliveries/history` | JWT | היסטוריית משלוחים עם סינון |
| `GET /api/panel/deliveries/{id}` | JWT | פרטי משלוח מלאים |

### Panel - ארנק

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/panel/wallet` | JWT | יתרה ושיעור עמלה |
| `GET /api/panel/wallet/ledger` | JWT | תנועות ארנק עם pagination וסינון |

### Panel - רשימה שחורה

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/panel/blacklist` | JWT | רשימת נהגים חסומים |
| `POST /api/panel/blacklist` | JWT | חסימת נהג |
| `POST /api/panel/blacklist/bulk` | JWT | חסימת כמה נהגים (עד 50) |
| `DELETE /api/panel/blacklist/{courier_id}` | JWT | ביטול חסימה |

### Panel - דוחות

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/panel/reports/collection` | JWT | דוח גבייה |
| `GET /api/panel/reports/collection/export` | JWT | ייצוא דוח גבייה ל-CSV |
| `GET /api/panel/reports/revenue` | JWT | דוח הכנסות |

### Panel - קבוצות

| Endpoint | אימות | תיאור |
|----------|-------|-------|
| `GET /api/panel/groups` | JWT | הגדרות קבוצות |
| `PUT /api/panel/groups` | JWT | עדכון הגדרות קבוצות |

---

## הערות חשובות

1. **אימות** - ישנם 3 רמות אימות: ללא, Admin API Key, ו-JWT Token. ודא שאתה משתמש בשיטה המתאימה.
2. **Swagger Auth Widget** - ב-Swagger UI יש ווידג'ט כניסה מהירה שמאפשר הזנת Admin API Key או התחברות עם OTP.
3. **Webhooks אוטומטיים** - אל תקרא להם ידנית בפרודקשן.
4. **כל הנתונים עוברים ולידציה** - ראה את ה-field_validators בקוד.
5. **מספרי טלפון מוסתרים** - בתגובות הפאנל מספרים ממוסכים (`+97250123****`), בלוגים `PhoneNumberValidator.mask()`.
6. **Circuit Breaker** - כל קריאות ה-API החיצוניות מוגנות. ניתן לבדוק סטטוס דרך Admin Debug.
7. **Correlation ID** - כל בקשה מקבלת מזהה למעקב בלוגים.
8. **Pagination** - endpoints של פאנל תומכים ב-pagination עם `page` ו-`page_size`.
9. **Bulk Operations** - סדרנים ורשימה שחורה תומכים בהוספה מרובה (עד 50).
10. **הבקשות הן אמיתיות!** - ב-Try it out הבקשות נשלחות לשרת בפועל. השתמש בזהירות בפרודקשן.

---

## לסיכום

תיעוד ה-API הוא הכלי העיקרי שלך להבנת המערכת:
- **קריאה** - הבן את המבנה והפרמטרים
- **בדיקה** - נסה endpoints ישירות מהדפדפן (עם ווידג'ט האימות)
- **דיבאג** - בדוק שגיאות, ולידציה, ומצב state machine
- **ניטור** - בדוק circuit breakers, הודעות כושלות, ומוכנות שרת

בכל פעם שאתה כותב קוד חדש או מתקן באג - **התחל מהתיעוד!**
