## ריוויו קוד — PR #230: הסרת Reply Keyboard בטלגרם

### סיכום
ה-PR מטפל בשני דברים:
1. **הסרת Reply Keyboard** — כל התפריטים המרכזיים עוברים ל-inline-only עם `clear_reply_keyboard=True`, שמנקה את ה-Reply Keyboard הישן.
2. **טיפול במגבלת 64 bytes של `callback_data`** — כפתורים ארוכים (עברית + אימוג'י) עוברים מיפוי ב-Redis עם token קצר, ובלחיצה הטקסט המלא נפתח בחזרה.

הקומיט השני הוא פורמט black בלבד.

---

### בעיות קריטיות 🔴

#### 1. שליחה דו-שלבית (send + editReplyMarkup) יוצרת UX רע ו-race condition
**קובץ:** `app/api/webhooks/telegram.py` שורות ~778-793

כשמגיע `clear_reply_keyboard=True`, ההודעה נשלחת קודם **בלי כפתורים** (`remove_keyboard`) ואז נעשה `editMessageReplyMarkup` כדי להוסיף inline keyboard. זה יוצר:
- **הבהוב (flicker)** — המשתמש רואה הודעה בלי כפתורים לרגע, ואז הם מופיעים.
- **Race condition** — אם ה-edit נכשל (timeout, rate limit), ההודעה נשארת **בלי כפתורים כלל**. המשתמש תקוע.
- **שתי קריאות API במקום אחת** — מכפיל את ה-rate limiting ב-Telegram.

**המלצה:** גישה טובה יותר — לשלוח הודעת placeholder קצרה עם `remove_keyboard` (כמו "⏳"), ומיד אחריה הודעה שנייה עם ה-inline keyboard והטקסט המקורי. כך גם אם ההודעה השנייה נכשלת, אין הודעה "שבורה" בלי כפתורים — יש רק placeholder שמשודר שזה מצב ביניים. לחלופין, אפשר פשוט לא לנקות Reply Keyboard באופן אקטיבי — `one_time_keyboard: true` כבר גורם לו להיעלם אחרי לחיצה.

#### 2. `_resolve_inline_button_mapping` — fallback חסר כשה-Redis key פג תוקף
**קובץ:** `app/api/webhooks/telegram.py` שורות ~976-987

אם המשתמש לוחץ על כפתור אחרי שה-TTL עבר, ה-Redis key כבר לא קיים. `_resolve_inline_button_mapping` מחזיר `None`, ואז הטקסט שנשלח ל-state machine הוא `"btn:AbCd1234"` — מחרוזת שלא תתאים לשום handler. המשתמש יקבל הודעת "לא הבנתי" בלי הסבר.

**המלצה:** אם ה-resolve מחזיר `None` לטקסט שמתחיל ב-`btn:`, כדאי לשלוח הודעה ברורה למשתמש ("הכפתור פג תוקף, חזור לתפריט") ולעשות reset ל-MENU, במקום להעביר token חסר-משמעות ל-state machine.

#### 3. `_build_inline_keyboard` — הלוגיקה מפוצלת בין יצירת ה-token לשמירה ב-Redis
**קובץ:** `app/api/webhooks/telegram.py` שורות ~693-716, ~760-775

הפונקציה `_build_inline_keyboard` (סינכרונית) יוצרת token ומחזירה keyboard, אבל ה-Redis storage מתבצע בלולאה נפרדת מאוחר יותר. הלוגיקה מפוצלת לשני מקומות שונים, מה שמקשה על תחזוקה. אם מישהו ישנה את הלולאה בשורות 760-775 או יקרא ל-`_build_inline_keyboard` ממקום אחר, המיפוי יישבר.

**המלצה:** לאחד למתודה אחת async שבונה keyboard + שומרת ב-Redis באותו שלב.

---

### בעיות בינוניות 🟡

#### 4. TTL ארוך מדי (שבוע) — בזבוז זיכרון Redis
`_INLINE_BUTTON_TTL_SECONDS = 7 * 24 * 60 * 60` (604,800 שניות).

כל לחיצת כפתור בתפריט יוצרת key ב-Redis שחי שבוע. עם הרבה משתמשים ותפריטים, זה יכול להצטבר. ברוב המקרים, כפתורים רלוונטיים למספר דקות עד שעות.

**המלצה:** TTL של 24-48 שעות מספיק. אם מישהו לא לחץ על הכפתור תוך יום, כנראה הוא כבר ביקש תפריט חדש.

#### 5. הסרת import שלא קשורה לשינוי
```diff
-from app.domain.services import AdminNotificationService
```
Import זה הוסר אבל לא ברור אם זה dead code — אם הוא בשימוש במקום אחר בקובץ (שלא השתנה בדיף), זו שבירה. צריך לוודא שזה אכן import מיותר.

#### 6. חוסר עקביות — לא כל התפריטים מקבלים `clear_reply_keyboard=True`
רק התפריטים הראשיים (MENU) של כל תפקיד מקבלים `clear_reply_keyboard=True`, אבל תפריטים משניים (כמו `_handle_view_wallet`, `_handle_deposit_request`) לא. אם משתמש שעדיין יש לו Reply Keyboard ישן נכנס לתפריט משני, ה-Reply Keyboard לא יתנקה.

**המלצה:** להוסיף logic אוטומטי ב-`_queue_response_send` שתמיד מנקה Reply Keyboard כשיש inline keyboard, או לוודא שזה חד-פעמי (פעם ראשונה אחרי עדכון) ושזה מספיק.

#### 7. `_edit_reply_markup` לא עוטף שגיאה בצורה graceful
ב-`_edit_reply_markup` (שורה ~735), אם ה-Telegram API מחזיר שגיאה (למשל, "message is not modified"), הפונקציה זורקת `TelegramError` שנתפס ב-catch הכללי. אבל ברגע הזה, ההודעה **כבר נשלחה** בלי כפתורים. ה-log ידווח "Telegram send failed" אבל ההודעה הראשונה כבר הגיעה למשתמש. אין retry ואין recovery.

---

### הערות קלות 🟢

#### 8. פורמט black — commit נפרד, תקין
הקומיט השני ("chore: פורמט black") מטפל רק בפורמט. זה מקובל ומקל על ריוויו, אבל רוב ה-diff (>80%) הוא פורמט בלבד. מומלץ בעתיד לעשות פורמט **לפני** ה-feature commit כדי שה-feature diff יהיה נקי יותר.

#### 9. בדיקות חסרות
הבדיקות שנוספו (`test_truncate_utf8_never_exceeds_max_bytes`, `test_inline_button_mapping_store_and_resolve`) טובות ומכסות את הלוגיקה החדשה. אבל חסרות בדיקות ל:
- **fallback** כשה-Redis נכשל — האם `_truncate_utf8` מחזיר תוצאה תקינה ב-edge cases?
- **TTL expiry** — מה קורה כשה-key לא נמצא ב-resolve? (הנקודה מסעיף 2)
- **שליחה דו-שלבית** — mock ל-`_edit_reply_markup` שנכשל — מה קורה למשתמש?
- **callback עם prefix `btn:` שלא נפתח** — מה ה-state machine עושה עם זה?

#### 10. `_truncate_utf8` עם `errors="ignore"` יכול לחתוך תו עברי באמצע
כרגע `_truncate_utf8` חותך ב-byte boundary ואז `decode("utf-8", errors="ignore")` משמיט bytes שבורים. זה עובד נכון מבחינת אורך, אבל יכול לגרום לאיבוד של תו שלם (למשל, תו בן 3 bytes שנחתך אחרי byte 1 — כל 3 הבתים יימחקו). זה לא באג קריטי, אבל שווה לדעת.

---

### סיכום
ה-PR פותר בעיה אמיתית (Reply Keyboard תופס מקום מיותר + מגבלת 64 bytes), אבל הגישה הדו-שלבית (send + edit) לניקוי Reply Keyboard היא הבעיה העיקרית — היא יוצרת flicker וסיכון שהמשתמש ייתקע בלי כפתורים. כדאי לשקול גישה חלופית (שתי הודעות נפרדות, או placeholder + הודעה מלאה) או לוותר על ניקוי Reply Keyboard אקטיבי.

בנוסף, חסר טיפול במקרה שה-Redis key פג תוקף והמשתמש לוחץ על כפתור ישן.

**ציון:** ⚠️ דורש שינויים לפני מיזוג
