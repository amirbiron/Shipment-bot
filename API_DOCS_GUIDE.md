# מדריך לתיעוד ה-API - Shipment Bot

## מבוא

תיעוד ה-API נמצא בכתובת: `https://shipment-bot-api.onrender.com/docs`

זהו תיעוד אינטראקטיבי מבוסס **Swagger UI** שנוצר אוטומטית על ידי FastAPI, ומאפשר לך:
- לצפות בכל ה-endpoints הזמינים
- לראות את המבנה של הבקשות והתגובות
- **לנסות את ה-API ישירות מהדפדפן** (Try it out!)
- להבין את סכמות הנתונים (schemas)

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
- לראות את קודי השגיאה האפשריים (400, 404, 422, 500)
- לבדוק validation errors
- לוודא שהשדות נשלחים בפורמט הנכון

---

## מבנה התיעוד - סקירה מלאה

### 📋 תגיות (Tags) - קטגוריות של Endpoints

התיעוד מחולק לקטגוריות לפי תגיות:

#### 1️⃣ **Deliveries** (משלוחים)
כל הפעולות הקשורות לניהול משלוחים.

#### 2️⃣ **Users** (משתמשים)
ניהול משתמשים - שליחים ושולחים.

#### 3️⃣ **Wallets** (ארנקים)
ניהול ארנקים, יתרות וטרנזקציות של שליחים.

#### 4️⃣ **Webhooks** (ווב-הוקים)
endpoints לקבלת הודעות מ-WhatsApp ו-Telegram.

#### 5️⃣ **Migrations** (מיגרציות)
endpoints להרצת מיגרציות של מסד הנתונים.

---

## פירוט מלא של כל Endpoint

### 🟢 Health Check

**כתובת:** `GET /health`
**תיאור:** בדיקת בריאות של השרת
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

## 📦 Deliveries - ניהול משלוחים

### 1. יצירת משלוח חדש

**כתובת:** `POST /api/deliveries/`
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

**מתי להשתמש:**
- כשמשתמש שולח רוצה ליצור משלוח חדש
- באפליקציית ווב או במובייל
- במסגרת זרימת ה-State Machine לשולחים

---

### 2. קבלת משלוחים פתוחים

**כתובת:** `GET /api/deliveries/open`
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
  },
  {
    "id": 457,
    "sender_id": 124,
    "pickup_address": "דיזנגוף 100, תל אביב",
    "dropoff_address": "אבן גבירול 30, תל אביב",
    "status": "OPEN",
    "courier_id": null,
    "fee": 15.0
  }
]
```

**מתי להשתמש:**
- כשמציגים לשליח את המשלוחים הזמינים
- בתפריט הראשי של השליח
- לרענון רשימת משלוחים זמינים

---

### 3. קבלת משלוח ספציפי

**כתובת:** `GET /api/deliveries/{delivery_id}`
**תיאור:** מחזיר מידע מפורט על משלוח ספציפי

**דוגמה:** `GET /api/deliveries/456`

**תגובה מוצלחת (200):**
```json
{
  "id": 456,
  "sender_id": 123,
  "pickup_address": "רחוב הרצל 10, תל אביב",
  "dropoff_address": "שדרות רוטשילד 50, תל אביב",
  "status": "CAPTURED",
  "courier_id": 789,
  "fee": 25.0
}
```

**שגיאות אפשריות:**
- `404` - משלוח לא נמצא

**מתי להשתמש:**
- לבדיקת סטטוס משלוח
- להצגת פרטי משלוח למשתמש
- לאחר יצירת משלוח חדש

---

### 4. תפיסת משלוח (Capture)

**כתובת:** `POST /api/deliveries/{delivery_id}/capture`
**תיאור:** הקצאת שליח למשלוח. פעולה אטומית הכוללת בדיקת אשראי, ניכוי עמלה והקצאת שליח.

**Request Body:**
```json
{
  "courier_id": 789
}
```

**תגובה מוצלחת (200):**
```json
{
  "success": true,
  "message": "המשלוח נתפס בהצלחה",
  "delivery": {
    "id": 456,
    "sender_id": 123,
    "pickup_address": "רחוב הרצל 10, תל אביב",
    "dropoff_address": "שדרות רוטשילד 50, תל אביב",
    "status": "CAPTURED",
    "courier_id": 789,
    "fee": 25.0
  }
}
```

**שגיאות אפשריות:**
- `400` - לא ניתן לתפוס (כבר נתפס, אין מספיק אשראי, וכו')
- `404` - משלוח לא נמצא
- `500` - שגיאת שרת בזמן התפיסה

**מה קורה מאחורי הקלעים:**
1. בדיקה שהמשלוח פתוח (OPEN)
2. בדיקת אשראי של השליח (יתרה + credit_limit)
3. ניכוי העמלה מארנק השליח
4. עדכון סטטוס המשלוח ל-CAPTURED
5. הקצאת השליח למשלוח
6. יצירת רשומת ledger

**מתי להשתמש:**
- כששליח בוחר לקחת משלוח
- במסגרת זרימת ה-State Machine לשליחים

---

### 5. סימון משלוח כנמסר

**כתובת:** `POST /api/deliveries/{delivery_id}/deliver`
**תיאור:** סימון משלוח שנתפס כהושלם על ידי השליח

**תגובה מוצלחת (200):**
```json
{
  "success": true,
  "delivery": {
    "id": 456,
    "sender_id": 123,
    "pickup_address": "רחוב הרצל 10, תל אביב",
    "dropoff_address": "שדרות רוטשילד 50, תל אביב",
    "status": "DELIVERED",
    "courier_id": 789,
    "fee": 25.0
  }
}
```

**שגיאות אפשריות:**
- `400` - לא ניתן לסמן כנמסר (סטטוס לא תקין)

**מתי להשתמש:**
- כששליח מסיים את המשלוח
- בסוף זרימת המשלוח

---

### 6. ביטול משלוח

**כתובת:** `DELETE /api/deliveries/{delivery_id}`
**תיאור:** ביטול משלוח פתוח שטרם נתפס

**תגובה מוצלחת (200):**
```json
{
  "success": true,
  "message": "Delivery cancelled"
}
```

**שגיאות אפשריות:**
- `400` - לא ניתן לבטל (כבר נתפס או נמסר)

**מתי להשתמש:**
- כששולח רוצה לבטל משלוח
- רק למשלוחים בסטטוס OPEN

---

## 👥 Users - ניהול משתמשים

### 1. יצירת משתמש חדש

**כתובת:** `POST /api/users/`
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

**ולידציות:**
- מספר טלפון חייב להיות בפורמט ישראלי תקין
- שם מוגבל ל-100 תווים ומסונן מ-XSS
- role תומך גם ב-UPPERCASE וגם ב-lowercase
- telegram_chat_id חייב להיות מספר (יכול להיות שלילי לקבוצות)

**תגובה מוצלחת (200):**
```json
{
  "id": 123,
  "phone_number": "+972501234567",
  "name": "יוסי כהן",
  "role": "SENDER",
  "platform": "whatsapp",
  "is_active": true
}
```

**שגיאות אפשריות:**
- `400` - משתמש כבר קיים
- `422` - שגיאת ולידציה

**מתי להשתמש:**
- מעט! בדרך כלל המשתמש נוצר אוטומטית ב-webhook
- במקרים של הגדרה ידנית או בדיקות

---

### 2. קבלת משתמש לפי ID

**כתובת:** `GET /api/users/{user_id}`
**דוגמה:** `GET /api/users/123`

**תגובה מוצלחת (200):**
```json
{
  "id": 123,
  "phone_number": "+972501234567",
  "name": "יוסי כהן",
  "role": "SENDER",
  "platform": "whatsapp",
  "is_active": true
}
```

**שגיאות אפשריות:**
- `404` - משתמש לא נמצא

---

### 3. קבלת משתמש לפי מספר טלפון

**כתובת:** `GET /api/users/phone/{phone_number}`
**דוגמה:** `GET /api/users/phone/0501234567`

**תגובה:** זהה למעלה

**מתי להשתמש:**
- לאיתור משתמש קיים לפי טלפון
- במהלך זרימת הרשמה

---

### 4. קבלת כל השליחים הפעילים

**כתובת:** `GET /api/users/couriers/`
**תיאור:** מחזיר רשימה של כל השליחים עם `role=COURIER` ו-`is_active=true`

**תגובה מוצלחת (200):**
```json
[
  {
    "id": 789,
    "phone_number": "+972507654321",
    "name": "דני שליח",
    "role": "COURIER",
    "platform": "telegram",
    "is_active": true
  }
]
```

**מתי להשתמש:**
- להצגת רשימת שליחים
- לצורך שיוך משלוחים

---

### 5. עדכון משתמש

**כתובת:** `PATCH /api/users/{user_id}`
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

**תגובה מוצלחת (200):**
```json
{
  "id": 123,
  "phone_number": "+972501234567",
  "name": "שם חדש",
  "role": "SENDER",
  "platform": "whatsapp",
  "is_active": false
}
```

**שגיאות אפשריות:**
- `404` - משתמש לא נמצא
- `422` - שגיאת ולידציה בשם

**מתי להשתמש:**
- לעדכון שם משתמש
- להשבתת/הפעלת משתמש
- לא ניתן לשנות phone_number או role!

---

## 💰 Wallets - ניהול ארנקים

### 1. קבלת ארנק של שליח

**כתובת:** `GET /api/wallets/{courier_id}`
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
- אם `balance >= credit_limit`, השליח יכול לקחת משלוחים

**מתי להשתמש:**
- להצגת מצב הארנק לשליח
- לבדיקת יתרה

---

### 2. קבלת יתרה נוכחית

**כתובת:** `GET /api/wallets/{courier_id}/balance`
**תיאור:** מחזיר רק את היתרה

**תגובה מוצלחת (200):**
```json
{
  "courier_id": 789,
  "balance": -150.0
}
```

**מתי להשתמש:**
- כשצריך רק את היתרה ללא פרטים נוספים

---

### 3. קבלת היסטוריית טרנזקציות

**כתובת:** `GET /api/wallets/{courier_id}/history?limit=20`
**תיאור:** מחזיר את ההיסטוריה של תנועות בארנק

**פרמטרים:**
- `limit` - מספר רשומות מקסימלי (ברירת מחדל: 20)

**תגובה מוצלחת (200):**
```json
[
  {
    "id": 1,
    "entry_type": "capture",
    "amount": -25.0,
    "balance_after": -175.0,
    "description": "תפיסת משלוח #456"
  },
  {
    "id": 2,
    "entry_type": "deposit",
    "amount": 100.0,
    "balance_after": -75.0,
    "description": "הפקדה"
  }
]
```

**סוגי טרנזקציות:**
- `capture` - תפיסת משלוח (ניכוי)
- `deposit` - הפקדה (הוספה)
- `refund` - החזר (הוספה)

**מתי להשתמש:**
- להצגת היסטוריה לשליח
- לדיבאג תנועות בארנק

---

### 4. בדיקה אם שליח יכול לתפוס משלוח

**כתובת:** `GET /api/wallets/{courier_id}/can-capture?fee=25.0`
**תיאור:** בודק אם לשליח יש מספיק אשראי לתפוס משלוח

**פרמטרים:**
- `fee` - עמלת המשלוח (ברירת מחדל: 10.0)

**תגובה מוצלחת (200):**
```json
{
  "can_capture": true,
  "message": "יש מספיק אשראי"
}
```

או:
```json
{
  "can_capture": false,
  "message": "אין מספיק אשראי. יתרה: -450, נדרש: -475 (מגבלה: -500)"
}
```

**מתי להשתמש:**
- לפני הצגת משלוח לשליח
- לפני ביצוע capture
- להצגת אזהרה לשליח

---

## 🔗 Webhooks - קבלת הודעות

### 1. WhatsApp Webhook

**כתובת:** `POST /api/whatsapp/webhook`
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

**תגובה מוצלחת (200):**
```json
{
  "processed": 1,
  "responses": [
    {
      "from": "972501234567",
      "response": "שלום וברוכים הבאים! 👋...",
      "new_state": "SENDER.MENU"
    }
  ]
}
```

**מה קורה מאחורי הקלעים:**
1. יצירת/איתור משתמש לפי sender_id
2. ניתוב לפי role (SENDER/COURIER)
3. העברה ל-State Machine Handler
4. שליחת תגובה דרך WhatsApp Gateway

**Webhook Verification:**
```
GET /api/whatsapp/webhook?hub_mode=subscribe&hub_challenge=123&hub_verify_token=token
```
מחזיר את hub_challenge לאימות.

**מתי להשתמש:**
- זה נקרא אוטומטית על ידי WhatsApp Gateway
- לא צריך לקרוא לזה ידנית!

---

### 2. Telegram Webhook

**כתובת:** `POST /api/telegram/webhook`
**תיאור:** מקבל עדכונים מ-Telegram Bot API

**Request Body:**
```json
{
  "update_id": 12345,
  "message": {
    "message_id": 1,
    "from": {
      "id": 123456789,
      "first_name": "יוסי",
      "last_name": "כהן",
      "username": "yossi_k"
    },
    "chat": {
      "id": 123456789,
      "type": "private"
    },
    "text": "שלום",
    "date": 1234567890
  }
}
```

**תגובה מוצלחת (200):**
```json
{
  "ok": true,
  "new_state": "SENDER.MENU"
}
```

**תמיכה ב-Callback Queries (כפתורים inline):**
```json
{
  "update_id": 12346,
  "callback_query": {
    "id": "callback_123",
    "from": { "id": 123456789, "first_name": "יוסי" },
    "message": { ... },
    "data": "📦 אני רוצה לשלוח חבילה"
  }
}
```

**פקודות מיוחדות:**
- `/start` - איפוס למצב התחלתי
- `#` - חזרה לתפריט ראשי

**מתי להשתמש:**
- נקרא אוטומטית על ידי Telegram
- לא צריך לקרוא לזה ידנית!

---

## 🔧 Migrations - מיגרציות

### הרצת מיגרציה 001

**כתובת:** `GET` או `POST /api/migrations/run-migration-001`
**תיאור:** מוסיפה שדות הרשמת שליחים לטבלת users

**מה המיגרציה עושה:**
1. יוצרת enum type `approval_status`
2. מוסיפה עמודות:
   - `full_name` - שם מלא
   - `approval_status` - סטטוס אישור (pending/approved/rejected/blocked)
   - `id_document_url` - קישור לתעודת זהות
   - `service_area` - אזור שירות
   - `terms_accepted_at` - מועד אישור תנאים
3. יוצרת אינדקס על `approval_status`
4. מגדירה credit_limit ברירת מחדל ל-500-

**תגובה מוצלחת (200):**
```json
{
  "success": true,
  "message": "Migration 001 completed successfully - courier fields added"
}
```

**תגובה בשגיאה:**
```json
{
  "success": false,
  "error": "error message"
}
```

**מתי להשתמש:**
- פעם אחת אחרי deploy
- בטוח להריץ מספר פעמים (uses IF NOT EXISTS)
- לא למחוק את ה-endpoint הזה!

---

## 🎯 תכונות מתקדמות בתיעוד

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
- `DeliveryCreate` - מבנה ליצירת משלוח
- `DeliveryResponse` - מבנה תגובת משלוח
- `UserCreate` - מבנה ליצירת משתמש
- `UserResponse` - מבנה תגובת משתמש
- ועוד...

**מתי להשתמש:**
- כשצריך להבין בדיוק מה המבנה של request/response
- לראות אילו שדות חובה/אופציונליים
- להבין את סוגי הנתונים

---

### 3. Response Codes

כל endpoint מציג את קודי התגובה האפשריים:
- **200** - הצלחה
- **400** - Bad Request (לדוגמה: לא ניתן לבטל משלוח)
- **404** - Not Found (המשאב לא נמצא)
- **422** - Validation Error (שגיאת ולידציה)
- **500** - Server Error (שגיאת שרת)

---

### 4. ReDoc (תיעוד חלופי)

יש גם תיעוד ב-ReDoc בכתובת: `https://shipment-bot-api.onrender.com/redoc`

**הבדלים:**
- Swagger UI - אינטראקטיבי, אפשר לנסות
- ReDoc - נקי יותר, טוב לקריאה

---

## 💡 טיפים לשימוש

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

זה עוזר להבין בדיוק מה השדה שלא עבר ולידציה.

### 4. Copy as cURL
בכל endpoint, אחרי "Execute", יש אפשרות ל-"Copy as cURL" - מועיל להעתקת הקריאה לטרמינל.

---

## 🔍 סיכום - מתי להשתמש בכל endpoint

| Endpoint | מתי להשתמש |
|----------|------------|
| `POST /api/deliveries/` | יצירת משלוח חדש על ידי שולח |
| `GET /api/deliveries/open` | הצגת משלוחים זמינים לשליח |
| `GET /api/deliveries/{id}` | בדיקת פרטי משלוח ספציפי |
| `POST /api/deliveries/{id}/capture` | שליח תופס משלוח |
| `POST /api/deliveries/{id}/deliver` | שליח מסיים משלוח |
| `DELETE /api/deliveries/{id}` | שולח מבטל משלוח |
| `POST /api/users/` | יצירת משתמש ידנית (נדיר) |
| `GET /api/users/{id}` | קבלת פרטי משתמש |
| `GET /api/users/phone/{phone}` | חיפוש משתמש לפי טלפון |
| `GET /api/users/couriers/` | רשימת כל השליחים |
| `PATCH /api/users/{id}` | עדכון שם/סטטוס משתמש |
| `GET /api/wallets/{id}` | בדיקת ארנק שליח |
| `GET /api/wallets/{id}/balance` | בדיקת יתרה |
| `GET /api/wallets/{id}/history` | היסטוריית תנועות |
| `GET /api/wallets/{id}/can-capture` | בדיקה אם יש אשראי |
| `POST /api/whatsapp/webhook` | (אוטומטי) קבלת הודעות WhatsApp |
| `POST /api/telegram/webhook` | (אוטומטי) קבלת הודעות Telegram |
| `GET /health` | בדיקת בריאות שרת |

---

## ⚠️ הערות חשובות

1. **אל תחשוף טוקנים או secrets** - התיעוד פומבי!
2. **Webhooks אוטומטיים** - אל תקרא להם ידנית בפרודקשן
3. **כל הנתונים עוברים ולידציה** - ראה את ה-field_validators בקוד
4. **מספרי טלפון מוסתרים בלוגים** - PhoneNumberValidator.mask()
5. **Circuit Breaker** - כל קריאות ה-API החיצוניות מוגנות
6. **Correlation ID** - כל בקשה מקבלת מזהה למעקב בלוגים

---

## 🎓 לסיכום

תיעוד ה-API הוא הכלי העיקרי שלך להבנת המערכת:
- **📖 קריאה** - הבן את המבנה והפרמטרים
- **🧪 בדיקה** - נסה endpoints ישירות מהדפדפן
- **🔍 דיבאג** - בדוק שגיאות וולידציה
- **💻 פיתוח** - השתמש בזה כמדריך בזמן כתיבת קוד

בכל פעם שאתה כותב קוד חדש או מתקן באג - **התחל מהתיעוד!**
