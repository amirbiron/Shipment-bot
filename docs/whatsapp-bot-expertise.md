# התמחות בפיתוח בוט WhatsApp — סקירת מימוש

מסמך זה מפרט את ההתמחויות הטכנולוגיות שנצברו במהלך פיתוח מערכת בוט שליחויות מבוססת WhatsApp, כפי שמיושמות בפרויקט זה.

---

## 1. ארכיטקטורת Dual-Provider (WPPConnect + Cloud API)

### תיאור
תכנון וביצוע ארכיטקטורת ספקים כפולה שמאפשרת עבודה מקבילה עם שני ממשקי WhatsApp:

- **WPPConnect** — gateway מבוסס Node.js לשליחת הודעות דרך WhatsApp Web, כולל תמיכה בקבוצות לא רשמיות
- **WhatsApp Cloud API (Meta)** — ממשק רשמי עם webhook verification, חתימות HMAC-SHA256, וכפתורים אינטראקטיביים

### מימוש
- **Abstract Base Provider** (`base_provider.py`) — ממשק אחיד עם מתודות `send_text`, `send_media`, `format_text`, `normalize_phone`
- **WPPConnectProvider** (`wppconnect_provider.py`) — מימוש עם retry ו-exponential backoff, המרת HTML ל-WhatsApp markdown, ונורמליזציה ל-E.164
- **PyWaProvider** (`pywa_provider.py`) — מימוש מעל `pywa_async` עם שלוש אסטרטגיות רינדור כפתורים, טיפול ב-base64 media, ו-lazy initialization
- **Provider Factory** (`provider_factory.py`) — Singleton thread-safe עם הפרדה בין הודעות פרטיות, קבוצתיות, ואדמין

### Hybrid Mode
במצב היברידי:
- הודעות פרטיות → Cloud API (כפתורים אינטראקטיביים, אמינות גבוהה)
- הודעות קבוצתיות → WPPConnect (Cloud API לא תומך בקבוצות לא רשמיות)
- הודעות אדמין → circuit breaker נפרד (כשלון בהודעות אדמין לא חוסם הודעות למשתמשים)

---

## 2. Webhook Handlers — עיבוד הודעות נכנסות

### WPPConnect Webhook (`whatsapp.py` — 2,091 שורות)
- פרסור payload מורכב עם תמיכה במגוון סוגי הודעות (טקסט, מדיה, מיקום)
- ניתוב לפי תפקיד (שולח, שליח, סדרן, בעל תחנה, נהג, אדמין) עם טיפול מפורש בכל תפקיד
- זיהוי והבחנה בין הודעות קבוצתיות להודעות פרטיות
- אידמפוטנטיות מבוססת DB — מנגנון `_try_acquire_message` עם מצבים (processing → completed) ו-retry אחרי 120 שניות

### Cloud API Webhook (`whatsapp_cloud.py` — 980 שורות)
- **Webhook Verification** — אימות `hub.challenge` מ-Meta
- **חתימת HMAC-SHA256** — ולידציה של כל payload נכנס מול `APP_SECRET`
- **חילוץ הודעות** — תמיכה בטקסט, כפתורים אינטראקטיביים (callback), מדיה (תמונה/מסמך/וידאו), מיקום GPS
- **תפיסה מקישור** — תמיכה ב-wa.me links עם `capture_TOKEN` לתפיסת משלוח ישירה

---

## 3. מערכת כפתורים אינטראקטיביים

### אסטרטגיות רינדור (Cloud API)
1. **Reply Buttons** (עד 3 כפתורים) — כפתורי תגובה עם callback_data
2. **Interactive Lists** (4–10 פריטים) — רשימת בחירה עם SectionRow
3. **Text Fallback** (מעל 10 פריטים) — רשימה ממוספרת בגוף ההודעה

### Guard Functions
- ולידציית אורך label (256 bytes לכפתורים, 200 ל-list rows)
- בדיקת ייחודיות labels ברשימות
- בדיקת מגבלות כמות (3 לכפתורים, 10 לרשימות)
- fallback אוטומטי עם logging בעת כשלון guard

### טיפול בקבוצות
כפתורים לא עובדים בקבוצות WhatsApp — מימוש fallback אוטומטי עם `keyboard=None` והנחיות טקסטואליות.

---

## 4. מכונת מצבים (State Machine) מרובת תפקידים

### תפקידים נתמכים
- **שולח (Sender)** — יצירת משלוחים עם כתובות איסוף ומסירה, דחיפות, מחיר, ותיאור
- **שליח (Courier)** — צפייה במשלוחים זמינים, תפיסה, סימון איסוף/מסירה, ארנק דיגיטלי
- **סדרן (Dispatcher)** — ניהול משלוחים, חיוב ידני, פרסום נסיעות
- **בעל תחנה (Station Owner)** — ניהול סדרנים/בעלים, רשימה שחורה, הגדרות, ארנק
- **נהג (Driver/iDriver)** — חיפוש נסיעות מבוסס GPS, סשן 24 שעות

### מנגנוני הגנה
- **Multi-step flow guard** — מניעת חטיפת מילות מפתח באמצע זרימה (בדיקת prefixes: `DISPATCHER.`, `STATION.`, ועוד)
- **ולידציית מעברים** — כל מעבר state חייב להיות מוגדר ב-TRANSITIONS, אחרת force_state עם warning
- **ניקוי context** — ניקוי מפתחות context ספציפיים לזרימה ביציאה לתפריט
- **בדיקת רישום קיים** — ב-INITIAL, בדיקת `is_registration_complete` למניעת דריסת נתונים

---

## 5. ניהול אדמין דרך WhatsApp

### זיהוי אדמין
- תמיכה במזהים מרובים: `sender_id`, `reply_to`, `from_number`, `resolved_phone`
- נורמליזציה של פורמטים: `@lid`, `@c.us`, `050`, `972`, `+972`
- רשימת אדמינים מוגדרת ב-settings (comma-separated)

### פקודות אדמין
- **אישור/דחיית שליח** — "אשר שליח 123", "דחה שליח 456 סיבה"
- **אישור/דחיית נהג** — "אשר נהג 789"
- **אישור/דחיית משלוח** — regex עם תמיכה באמוג'י ורווחים
- פעולות זמינות גם בקבוצת אדמין וגם בצ'אט פרטי

### שימור קונטקסט אדמין
- **שמירת קונטקסט** — שמירת תפקיד מקורי לפני מעבר זמני לתפקיד אחר
- **שחזור** — חזרה לתפקיד אדמין אחרי פעולה כתפקיד אחר
- **כפתור חזרה** — הזרקת כפתור "חזרה לאדמין" בכל מסך

---

## 6. טיפול בבעיית LID (@lid) לעומת @c.us

### הבעיה
WhatsApp עבר ממזהי @c.us למזהי @lid, מה שגורם לכשלונות שקטים בשליחת `sendListMessage`.

### הפתרון שמומש
- **רזולוציית LID → טלפון** בשכבת ה-gateway (WPPConnect 1.29.0+)
- חילוץ מספר מ: `contact.number`, `contact.formattedName`, `getChatById()`, `message.sender.formattedName`
- **מטמון `lidToCusMap`** לשימוש חוזר
- **רזולוציית יעד שליחה לאדמין** — לוגיקת עדיפויות לבחירת הפורמט הנכון

---

## 7. טיפול במדיה

### סוגי מדיה נתמכים
- **תמונות** — `media_type: image` או `media_type: document` עם `mime_type` שמתחיל ב-`image/`
- **מסמכים** — PDF, קבצים
- **וידאו** — קליפים
- **מיקום GPS** — קואורדינטות `latitude`/`longitude` עם תמיכה בשני פורמטים (שדות שטוחים ואובייקט מקונן)

### מימוש
- חילוץ `photo_file_id` עבור state machine handlers
- תמיכה ב-base64 data URIs ב-Cloud API
- caption formatting מותאם לפלטפורמה

---

## 8. המרת טקסט HTML ← WhatsApp Markdown

פונקציית `convert_html_to_whatsapp()` שממירה תגיות HTML לפורמט WhatsApp:

| HTML | WhatsApp |
|------|----------|
| `<b>טקסט</b>` | `*טקסט*` |
| `<i>טקסט</i>` | `_טקסט_` |
| `<s>טקסט</s>` | `~טקסט~` |
| `<code>טקסט</code>` | `` `טקסט` `` |
| `<pre>טקסט</pre>` | ```` ```טקסט``` ```` |
| `<br>` | `\n` |
| HTML entities | unescaped |

---

## 9. עמידות ואמינות

### Circuit Breaker
- circuit breaker נפרד לכל ספק (WPPConnect, Cloud API, אדמין)
- הפרדה מונעת מצב שבו כשלון בספק אחד משפיע על אחרים

### Retry עם Exponential Backoff
- עד 3 ניסיונות חוזרים בברירת מחדל
- backoff מוגדר
- סטטוסים חולפים מוגדרים: 502, 503, 504, 429

### אידמפוטנטיות
- טבלת `WebhookEvent` ב-DB
- מצבים: `processing` → `completed`
- retry אוטומטי להודעות תקועות (stale) אחרי 120 שניות
- INSERT אטומי עם commit מיידי

---

## 10. אבטחה ופרטיות

### אימות Webhook
- **Cloud API**: חתימת HMAC-SHA256 על כל payload נכנס
- **Verification endpoint**: אימות `hub.verify_token` ו-`hub.challenge`

### פרטיות מספרי טלפון
- מיסוך מספרים בלוגים באמצעות `PhoneNumberValidator.mask()` — `+97250123****`
- סינון מזהי קבוצה (`@g.us`) ו-placeholders (`tg:`) לפני שליחת הודעה אישית

### ולידציית קלט
- כל קלט עובר ולידציה וסניטציה דרך validators ייעודיים
- בדיקת הרשאות (authorization) לפני כל פעולה

---

## 11. בדיקות (Testing)

### כיסוי בדיקות WhatsApp
- **test_whatsapp_provider.py** — בדיקות ספקים, retry, circuit breaker, factory
- **test_whatsapp_webhook_state.py** — ניתוב state machine, guards, קונטקסט אדמין
- **test_whatsapp_cloud_webhook.py** — חתימות, חילוץ הודעות, כפתורים, אידמפוטנטיות
- **test_whatsapp_connection_check.py** — בדיקות קישוריות gateway
- **test_driver_whatsapp.py** — זרימות נהג ב-WhatsApp

### עקרונות
- mock לכל שירות חיצוני (WPPConnect gateway, Cloud API)
- mock functions שמחזירות `patch` חדש (לא shared AsyncMock ברמת מודול)
- בדיקת edge cases: הודעות כפולות, מזהים לא תקינים, timeout-ים

---

## 12. קונפיגורציה ותשתית

### משתני סביבה
```
WHATSAPP_GATEWAY_URL          — כתובת gateway WPPConnect
WHATSAPP_PROVIDER             — ספק ברירת מחדל (wppconnect / pywa)
WHATSAPP_HYBRID_MODE          — הפעלת מצב היברידי

WHATSAPP_CLOUD_API_TOKEN      — טוקן Cloud API
WHATSAPP_CLOUD_API_PHONE_ID   — מזהה טלפון ב-Meta
WHATSAPP_CLOUD_API_PHONE_NUMBER — מספר טלפון ל-wa.me links
WHATSAPP_CLOUD_API_APP_SECRET — סוד אפליקציה לחתימות
WHATSAPP_CLOUD_API_VERIFY_TOKEN — טוקן אימות webhook

WHATSAPP_ADMIN_GROUP_ID       — מזהה קבוצת אדמין
WHATSAPP_ADMIN_NUMBERS        — רשימת מספרי אדמין
WHATSAPP_MAX_RETRIES          — מקסימום ניסיונות חוזרים
```

---

## סיכום טכנולוגיות וכלים

| תחום | טכנולוגיה |
|-------|-----------|
| שפת תכנות | Python (async/await) |
| פריימוורק | FastAPI |
| WhatsApp Gateway | WPPConnect (Node.js) |
| WhatsApp Cloud API | pywa_async |
| בסיס נתונים | PostgreSQL + SQLAlchemy (async) |
| תורי עבודה | Celery + Redis |
| ולידציה | Pydantic v2 |
| בדיקות | pytest + AsyncMock |
| תבנית ארכיטקטורה | State Machine, Provider Pattern, Circuit Breaker, Transactional Outbox |
