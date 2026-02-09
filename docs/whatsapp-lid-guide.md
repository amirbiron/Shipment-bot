# WhatsApp LID (@lid) - מדריך ותובנות

## מה זה LID?

WhatsApp עובר בהדרגה ממזהים מבוססי מספר טלפון (`972501234567@c.us`) למזהים פנימיים שנקראים **Linked ID** (`6661762744366@lid`). ה-LID הוא מספר פנימי של WhatsApp שלא מכיל את מספר הטלפון של המשתמש.

לא כל המשתמשים עברו ל-LID. חלקם עדיין משתמשים ב-`@c.us`. אין דרך לשלוט בזה — WhatsApp מחליט.

---

## התנהגות API לפי סוג מזהה

| פעולה | `@c.us` | `@lid` |
|---|---|---|
| `sendText` | עובד | עובד |
| `sendListMessage` | עובד | **מחזיר הצלחה אבל ההודעה לא מגיעה** |
| `sendButtons` | לא קיים (WPPConnect 1.38+) | לא קיים |
| `sendImage` / `sendFile` | עובד | עובד |
| `getContact` | מחזיר מספר טלפון | **עשוי להחזיר רק LID** |

> **הבעיה המרכזית:** `sendListMessage` עם `@lid` מחזיר תשובת הצלחה (לא זורק שגיאה), אבל ההודעה **לא מגיעה ליעד**. זו "הצלחה שקטה" שקשה מאוד לאבחן.

---

## אסטרטגיית הפתרון בגייטוויי

### 1. מיפוי LID → @c.us (lidToCusMap)

כשמתקבלת הודעה מ-`@lid` ב-`onMessage`, הגייטוויי מנסה לפתור את ה-LID למספר טלפון:
- `contact.id._serialized` — אם לא @lid
- `contact.number` — מספר ישיר
- **`contact.formattedName`** — לפעמים מכיל את המספר (למשל `"⁦+972 54-397-8620⁩"`)
- `client.getChatById()` — בודק `number` ו-`formattedName` ב-chat contact
- `message.chatId`, `message.sender.id` — מזהים חלופיים
- **`message.sender.formattedName`** — מאמץ אחרון, חילוץ מספר מ-formatted name

> **תובנה קריטית:** כש-`contact.number` לא קיים (קורה עם משתמשי LID), `formattedName` עשוי להכיל את המספר בפורמט `⁦+972 XX-XXX-XXXX⁩`. הפונקציה `extractIsraeliPhoneFromCandidates` מחלצת את הספרות ומנרמלת.

אם הפתרון מצליח, **שומרים את המיפוי** ב-`lidToCusMap`:
```javascript
lidToCusMap.set(message.from, replyTo);  // "xxx@lid" → "972xxx@c.us"
```

### 2. שימוש במיפוי בזמן שליחה (/send)

כשצריך לשלוח הודעה אינטראקטיבית (רשימה/כפתורים) ל-`@lid`:
1. בודקים אם יש מיפוי ב-`lidToCusMap`
2. אם אין — מנסים `getContact` שוב
3. אם נמצא `@c.us` — שולחים `sendListMessage` אליו (עובד!)
4. אם לא נמצא — `sendText` עם אפשרויות בטקסט (fallback)

> עדכון: בפועל נתקלנו במקרים שבהם `sendListMessage` "מחזיר הצלחה" אבל ההודעה לא מגיעה גם כשהיעד הוא `@c.us`.
> לכן בגייטוויי **שולחים תמיד קודם תפריט כטקסט** (`sendText`) בתור ברירת מחדל אמינה,
> ורק אם משתנה הסביבה `WHATSAPP_INTERACTIVE_ENABLED=true` מופעל — מנסים בנוסף גם הודעה אינטראקטיבית.

### 3. נתיב retry ("No LID for user")

כשהשליחה ל-`@c.us` נכשלת עם `"No LID for user"`:
- המשתמש עבר ל-LID, ה-`@c.us` כבר לא תקין
- ה-retry בונה כתובת `@lid` ושולח `sendText` ישירות
- **לא מנסים `sendListMessage` עם `@lid`** — ידוע שזה הצלחה שקטה

---

## Circuit Breaker — הפרדה בין admin למשתמשים

### הבעיה
שליחת תמונות (`/send-media`) למנהלים נכשלת → Circuit breaker מתמלא (threshold=5) → Circuit breaker נפתח → **חוסם את כל ההודעות** כולל תשובות למשתמשים רגילים.

### הפתרון
שני circuit breakers נפרדים:
- `whatsapp` — להודעות למשתמשים רגילים
- `whatsapp_admin` — להתראות למנהלים

כשל בשליחה למנהלים לא חוסם את השירות למשתמשים.

```python
# app/core/circuit_breaker.py
get_whatsapp_circuit_breaker()        # למשתמשים
get_whatsapp_admin_circuit_breaker()  # למנהלים
```

---

## WHATSAPP_ADMIN_NUMBERS — טיפים

- אפשר להכניס מספרי טלפון רגילים (`0501234567`) או עם סיומת (`972501234567@c.us`, `xxx@lid`)
- הגייטוויי מנרמל אוטומטית: `0501234567` → `972501234567@c.us`
- אם מנהל הוא משתמש LID ואין לו `@lid` בהגדרות, השליחה ל-`@c.us` תיכשל עם `"No LID for user"` → ה-retry path ישלח ל-`@lid`
- **עדיף להוסיף את הכתובת עם הסיומת המדויקת** כדי למנוע retry מיותר

---

## WPPConnect — גרסאות ו-webVersionCache

### רקע
- **PR #98** שידרג WPPConnect מ-1.29.0 ל-1.38.0 והוסיף `webVersionCache: { type: 'none' }`
- `webVersionCache: { type: 'none' }` מכריח את WPPConnect לטעון את **הגרסה האחרונה** של WhatsApp Web בכל הפעלה

### השפעה על LID
- בגרסה 1.29.0 ללא `webVersionCache`, `sendListMessage` עם `@lid` עבד
- אחרי השדרוג ל-1.38.0 + `webVersionCache: none`, `sendListMessage` עם `@lid` הפסיק לעבוד (הצלחה שקטה)
- ייתכן שזה קשור לגרסת WhatsApp Web שנטענת, ולא לגרסת WPPConnect עצמה

### המלצה
- לא לסמוך על `sendListMessage` עם `@lid` בשום גרסה
- תמיד לפתור `@lid` → `@c.us` לפני שליחת הודעה אינטראקטיבית
- מנגנון `lidToCusMap` מטפל בזה

---

## זיהוי אדמין — 5 מזהים

בגלל ש-WhatsApp שולח מזהים שונים בשדות שונים, זיהוי אדמין בודק 5 מזהים:

```python
admin_identifiers = [
    sender_id,       # xxx@lid או 972xxx@c.us
    reply_to,        # כתובת תשובה (אחרי פתרון LID)
    from_number,     # מספר טלפון (אם נמצא)
    resolved_phone,  # מספר שחולץ מ-LID
    user.phone_number  # מספר שמור ב-DB
]
```

כל אחד מנורמל (הסרת סיומות, המרת 0→972) ומושווה לרשימת המנהלים.

---

## entered_as_admin — flag לזיהוי אדמין בזרימת שליח

כשאדמין נכנס לזרימת שליח, נשמר `entered_as_admin: true` ב-context. זה משמש ב-handler של `#` (חזרה לתפריט ראשי) כ-fallback לזיהוי — כי אחרי שינוי role מ-COURIER ל-SENDER, ייתכן שזיהוי לפי טלפון לא עובד.

---

## _resolve_admin_send_target — עדיפות reply_to

הפונקציה מחזירה את המזהה הטוב ביותר לשליחת תשובה למנהל:
1. אם הערך בהגדרות כולל סיומת (`@c.us`/`@lid`) — משתמשים בו ישירות
2. אם לא — מחפשים מזהה עם סיומת מבין `[reply_to, sender_id]`
3. **`reply_to` מקבל עדיפות** כי הוא הכתובת שפותרה (ולא ה-LID המקורי)

---

## טעויות נפוצות (ללמוד מהן)

1. **לא לסמוך על `sendListMessage` להחזרת הצלחה** — עם `@lid` ההודעה לא מגיעה אבל אין שגיאה
2. **לא לשתף circuit breaker** בין קבוצות שונות של הודעות — כשל באחת חוסם הכל
3. **`sendButtons` לא קיים** בגרסאות חדשות של WPPConnect — תמיד לתפוס שגיאה
4. **`"No LID for user"` = המשתמש עבר ל-LID** — צריך retry עם `@lid`, אבל רק `sendText`
5. **`else` גנרי בניתוב תפקידים** — תמיד לטפל בכל `UserRole` מפורשות
