# אנליזת איכות קוד — Amir Biron Projects (Multi-Repo)
> תאריך: 2026-03-17 | סה"כ באגים: 62 | פרויקטים: 8

## סיכום דפוסים
| דפוס | כמות | % מסה"כ | דוגמה מרכזית |
|---|---|---|---|
| ניהול מצב / concurrency | 15 | 24% | Race condition בבדיקת daily limit מחוץ ל-lock (Facebook-Leads) |
| async / control flow | 10 | 16% | await חסר — coroutine object תמיד truthy, חסם כל webhook (Shipment-bot) |
| בטיחות נתונים | 9 | 15% | דליפת courier_id ב-API response, דליפת passwordHash בקונטקסט (Shipment, routine) |
| ולידציית קלט | 7 | 11% | NaN/Inf עובר ולידציה כי NaN comparisons תמיד False (Shipment-bot) |
| לוגיקה עסקית / מקרי קצה | 8 | 13% | חצות מפורמטת כ-"24:00" — תזכורות חצות לא נשלחות (routine) |
| סכימת DB / מיגרציות | 5 | 8% | IF NOT EXISTS לא נתמך ב-MySQL ADD COLUMN (routine) |
| DOM / UI | 4 | 6% | CSS reset דורס Tailwind spacing — רווחים נעלמים בטופס (Web) |
| API design | 4 | 6% | SendResult.__bool__ חסר — `r is True` תמיד False לאובייקט (Shipment-bot) |

---

## רשימה מלאה לפי חומרה

### MAJOR

#### 1. Account Hijack — OAuth user password takeover
- **בעיה:** Signup flow אפשר להגדיר סיסמה למשתמש OAuth קיים רק על סמך ידיעת האימייל. תוקף שיודע אימייל של משתמש OAuth יכול לקבל גישה מלאה לחשבון.
- **תוצאה:** השתלטות על חשבונות משתמשים בפרודקשן.
- **מקור:** `routine` — commit `8a39df6`
- **דפוס:** בטיחות נתונים

#### 2. Rate Limiter Bypass via X-Forwarded-For Spoofing
- **בעיה:** Rate limiter קרא ישירות מ-`X-Forwarded-For` header בלי לוודא ש-proxy מהימן. תוקף יכול לשלוח header מזויף ולעקוף חסימת IP.
- **תוצאה:** Brute force attacks על login ו-webhook endpoints ללא הגבלה.
- **מקור:** `Shipment-bot` — commit `11e7379` | `routine` — commit `06ca796`
- **דפוס:** בטיחות נתונים

#### 3. Missing await — כל Webhook נחסם ב-429
- **בעיה:** `_is_ip_blocked` ו-`_record_failed_attempt` הפכו ל-async (העברה ל-Redis) אבל הקריאות לא עודכנו עם `await`. Coroutine object הוא תמיד truthy, כך `if _is_ip_blocked(ip)` תמיד True — **כל** webhook request נחסם.
- **תוצאה:** הבוט מפסיק לקבל הודעות מ-Telegram ו-WhatsApp. תקלה מלאה בפרודקשן.
- **מקור:** `Shipment-bot` — commit `f1e0fbb`
- **דפוס:** async / control flow

#### 4. OTP נשלח לפני שמירה ב-Redis — קוד לא ניתן לאימות
- **בעיה:** הסדר היה: commit outbox → store_otp ב-Redis. אם Redis כשל אחרי commit, ה-OTP נשלח למשתמש אבל לא נשמר — המשתמש מקבל קוד שלא ניתן לאמת.
- **תוצאה:** משתמשים לא יכולים להתחבר למרות שקיבלו קוד OTP תקין.
- **מקור:** `Shipment-bot` — commits `552f0f7`, `155aa81`
- **דפוס:** async / control flow

#### 5. TOCTOU Race — שליחה כפולה של הודעות Outbox
- **בעיה:** SELECT + mark_as_processing בשני שלבים נפרדים. כש-beat scheduler ו-send_message.delay רצים במקביל, שניהם קוראים את ההודעה כ-PENDING ושולחים אותה.
- **תוצאה:** הודעות נשלחות פעמיים ללקוחות (כולל OTP כפול).
- **מקור:** `Shipment-bot` — commit `457eea1`
- **דפוס:** ניהול מצב / concurrency

#### 6. דליפת passwordHash בקונטקסט בקשה
- **בעיה:** `ctx.user` הכיל את ה-passwordHash המלא. כל middleware או endpoint שמחזיר את ה-user object חשף את ה-hash.
- **תוצאה:** דליפת password hashes ב-API responses — מאפשר offline cracking.
- **מקור:** `routine` — commit `06ca796`
- **דפוס:** בטיחות נתונים

#### 7. Race Condition בדיקת Daily Limit מחוץ ל-Lock
- **בעיה:** `_check_daily_limit()` נבדק מחוץ ל-`scan_lock`. שתי קריאות מקבילות עוברות את הבדיקה לפני שאחת תופסת את ה-lock, ושתיהן מריצות סריקה.
- **תוצאה:** חריגה ממגבלת אוטומציה יומית, עומס מיותר על Facebook.
- **מקור:** `Facebook-Leads-New` — commit `5823724`
- **דפוס:** ניהול מצב / concurrency

#### 8. Bot Thread מת בשקט — Panel ממשיך לרוץ ללא Bot
- **בעיה:** `SystemExit` נזרק ב-thread של הבוט, אבל Python לא מפסיק process בגלל exception ב-thread. ה-panel המשיך לרוץ ו-Render לא זיהה תקלה.
- **תוצאה:** הבוט מת אבל השירות נראה תקין — אין ניטור, אין alerts.
- **מקור:** `Amazon-bot` — commit `c27c769`
- **דפוס:** async / control flow

#### 9. XSS בפאנל ניהול — innerHTML עם שמות משתמשים
- **בעיה:** `renderBlockedUsers` השתמש ב-`innerHTML` עם שמות display שמגיעים מ-Facebook. שם זדוני כמו `<script>alert(1)</script>` יבוצע.
- **תוצאה:** XSS attack דרך שם פרופיל פייסבוק — גניבת session של מנהל.
- **מקור:** `Facebook-Leads-New` — commit `6a6ec51`
- **דפוס:** בטיחות נתונים

#### 10. Privilege Escalation — Auto-Admin לפי אימייל לא מאומת
- **בעיה:** משתמש שנרשם עם אימייל ה-owner קיבל אוטומטית הרשאות admin — בלי לאמת שהאימייל באמת שלו.
- **תוצאה:** כל אחד יכול להירשם עם אימייל ה-owner ולקבל admin.
- **מקור:** `Markdown-Academy` — commit `4623bdb`
- **דפוס:** בטיחות נתונים

#### 11. Panel חשוף לרשת ללא אותנטיקציה
- **בעיה:** Flask panel רץ על `0.0.0.0` ללא token gate — כל מי שמגיע לפורט יכול לגשת לפאנל ההגדרות.
- **תוצאה:** חשיפה מלאה של פאנל ההגדרות לאינטרנט.
- **מקור:** `Amazon-bot` — commits `85776b5`, `c27c769`
- **דפוס:** בטיחות נתונים

#### 12. Wildcard Injection ב-LIKE Query
- **בעיה:** `get_config_by_prefix` השתמש ב-`LIKE ?%` — prefix כמו `test_key` תואם גם `testXkey` כי `_` הוא wildcard ב-LIKE.
- **תוצאה:** keywords של קבוצה אחת מוחלים על קבוצה אחרת (force_send שגוי).
- **מקור:** `Facebook-Leads-New` — commit `2f45eca`
- **דפוס:** ולידציית קלט

---

### MEDIUM

#### 13. MissingGreenlet — גישה ל-attributes אחרי commit
- **בעיה:** לולאה על `expiring` deliveries אחרי `db.commit()` — SQLAlchemy async דורש re-fetch אחרי commit כי ה-session פג.
- **תוצאה:** crash ב-auto-cancel loop — התראות תפוגה לא נשלחות.
- **מקור:** `Shipment-bot` — commit `85d7a8e`
- **דפוס:** async / control flow

#### 14. SendResult שובר תאימות — ספירת הצלחות Broadcast
- **בעיה:** broadcast counting השתמש ב-`r is True` — תמיד False כי `r` הוא אובייקט `SendResult`, לא boolean.
- **תוצאה:** broadcast מדווח 0 הצלחות תמיד, גם כשההודעות נשלחו.
- **מקור:** `Shipment-bot` — commit `11e7379`
- **דפוס:** API design

#### 15. CancelledError נספר כהצלחה ב-Broadcast
- **בעיה:** `isinstance(r, Exception)` לא תופס `CancelledError` שיורש מ-`BaseException`. משימות שבוטלו נספרו כהצלחה.
- **תוצאה:** ספירת broadcast מנופחת — מדווח על שליחות שלא קרו.
- **מקור:** `Shipment-bot` — commit `e0f4d59`
- **דפוס:** async / control flow

#### 16. Singleton Engine ב-Celery — Task שני נכשל
- **בעיה:** Celery יוצר event loop חדש לכל task, אבל singleton engine מחובר ל-loop הישן. Task שני תמיד נכשל.
- **תוצאה:** כל task שני ב-Celery worker נכשל — חצי מההודעות לא נשלחות.
- **מקור:** `Shipment-bot` — commit `0f30963`
- **דפוס:** async / control flow

#### 17. חצות מפורמטת כ-"24:00" — תזכורות לא נשלחות
- **בעיה:** `hour12: false` ב-`toLocaleTimeString` מחזיר "24:00" בחלק ממימושי Node.js/ICU. תזכורות שנקבעו לחצות לא נשלחו.
- **תוצאה:** משתמשים שהגדירו תזכורת לחצות לא מקבלים אותה.
- **מקור:** `routine` — commit `4d91c5c`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 18. Memory Leak ב-Dedup Map של Push Scheduler
- **בעיה:** `lastSentMap` גדל ללא הגבלה לאורך חיי השרת — אף רשומה לא נמחקה.
- **תוצאה:** דליפת זיכרון איטית שמובילה ל-OOM crash אחרי ימים/שבועות.
- **מקור:** `routine` — commit `4d91c5c`
- **דפוס:** ניהול מצב / concurrency

#### 19. Race Condition בהרשמה — חשבון כפול
- **בעיה:** אין unique constraint על email ב-DB. שתי בקשות הרשמה מקבילות עם אותו אימייל שתיהן עוברות את הבדיקה ויוצרות שני חשבונות.
- **תוצאה:** כפילות חשבונות, בלבול בהתחברות.
- **מקור:** `Markdown-Academy` — commit `4623bdb`
- **דפוס:** סכימת DB

#### 20. לולאת ניתוב — נהג-סדרן נתקע
- **בעיה:** נהג שהוא גם סדרן לא יכל לחזור לתפריט נהג — "חזרה לתפריט" זיהה אותו כסדרן והחזיר לתפריט סדרן בלולאה אינסופית.
- **תוצאה:** נהגים-סדרנים נתקעים ולא יכולים לעבוד כנהגים.
- **מקור:** `Shipment-bot` — commit `ea648a0`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 21. Race Condition ביצירת ילד — כפילות ב-Onboarding
- **בעיה:** back-navigation ב-onboarding יצר ילד חדש בכל פעם כי אין בדיקה אם כבר נוצר.
- **תוצאה:** ילדים כפולים בחשבון — בלבול בנתונים.
- **מקור:** `routine` — commit `b8b67a1`
- **דפוס:** ניהול מצב / concurrency

#### 22. Stale Closure — toggleActivity לוכד childId ישן
- **בעיה:** `activeChildId` חסר ב-dependency array של `toggleActivity` callback. אחרי החלפת ילד, הפעולה משפיעה על הילד הקודם.
- **תוצאה:** שינויים בלוח זמנים נשמרים לילד הלא-נכון.
- **מקור:** `routine` — commit `259c2a3`
- **דפוס:** ניהול מצב / concurrency

#### 23. NaN/Inf עוברים ולידציה
- **בעיה:** `AmountValidator` לא בדק NaN/Inf. `NaN < 0` מחזיר False, `NaN > max` מחזיר False — עובר את כל הבדיקות.
- **תוצאה:** סכומים לא-חוקיים (NaN) נכנסים לארנק.
- **מקור:** `Shipment-bot` — commit `97bf0bc`
- **דפוס:** ולידציית קלט

#### 24. דליפת courier_id ב-API Response
- **בעיה:** `InsufficientCreditError.to_dict()` השתמש ב-`self.message` שהכיל "Insufficient credit for courier {id}" — חשף מזהה פנימי.
- **תוצאה:** דליפת מידע פנימי ללקוחות.
- **מקור:** `Shipment-bot` — commit `59a5e3c`
- **דפוס:** בטיחות נתונים

#### 25. profile.php URLs מתמוטטים לURL אחד
- **בעיה:** נרמול URL הסיר את כל ה-query params — כולל `?id=` ב-profile.php. כל המשתמשים ללא vanity URL קיבלו אותו URL מנורמל.
- **תוצאה:** חסימת משתמש אחד חוסמת את כולם עם profile.php.
- **מקור:** `Facebook-Leads-New` — commit `6a6ec51`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 26. Blocking Sync Calls חוסמים Event Loop
- **בעיה:** `send_lead()` ו-`classify_batch()` הם sync ורצו ישירות ב-async event loop — חוסמים את כל העיבוד.
- **תוצאה:** הבוט לא מגיב בזמן סיווג/שליחה — timeout בהודעות Telegram.
- **מקור:** `Facebook-Leads-New` — commit `35ac718`
- **דפוס:** async / control flow

#### 27. לולאת איפוס אדמין — Admin נתקע ב-Reset אינסופי
- **בעיה:** בדיקת stale state סיננה `user.role != UserRole.SENDER` — אדמינים עם SENDER.* state נכנסו ללולאת איפוס.
- **תוצאה:** אדמינים לא יכולים לפעול בבוט.
- **מקור:** `Shipment-bot` — commit `9bd99c1`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 28. None Context Keys נאבדים — חזרה לאדמין שבורה
- **בעיה:** Dict comprehension סינן מפתחות עם `None` (`if admin_ctx.get(k) is not None`). `original_approval_status=None` היה ערך לגיטימי שנמחק.
- **תוצאה:** מעבר חזרה לתפקיד אדמין שבור — state לא עקבי.
- **מקור:** `Shipment-bot` — commit `9bd99c1`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 29. Race Condition ב-Reminder Mutations
- **בעיה:** `handleMorningTimeChange` שלח רק את השעה בלי `enabled` flag. בקשות שמגיעות בסדר שונה דורסות אחת את השנייה.
- **תוצאה:** תזכורת מושבתת כשמשנים שעה, או שעה חוזרת לברירת מחדל.
- **מקור:** `routine` — commit `01c61ac`
- **דפוס:** ניהול מצב / concurrency

#### 30. entity_id Integer קטן מדי ל-Telegram IDs
- **בעיה:** `entity_id` ב-audit log היה `Integer` — Telegram IDs יכולים להיות גדולים מ-2^31.
- **תוצאה:** overflow ב-audit log — שגיאות DB בעת לוגינג.
- **מקור:** `Shipment-bot` — commit `b16b99f`
- **דפוס:** סכימת DB

#### 31. Deadlock בטעינת עורך Monaco
- **בעיה:** Monaco Editor נתקע בטעינה — deadlock בין initialization callbacks.
- **תוצאה:** העורך לא נטען — המשתמש רואה מסך ריק.
- **מקור:** `Markdown-Academy` — commit `a6bcde6`
- **דפוס:** DOM / UI

#### 32. Race Condition בזריעת שיעורים — כפילויות
- **בעיה:** Server startup קרא ל-seed כמה פעמים במקביל ללא guard. שיעורים נוצרו בכפילות.
- **תוצאה:** שיעורים כפולים מופיעים בממשק.
- **מקור:** `Markdown-Academy` — commit `7c955f1`
- **דפוס:** ניהול מצב / concurrency

#### 33. Stale refreshToken אחרי Verification
- **בעיה:** אחרי email verification, רק `token` התעדכן ב-localStorage — `refreshToken` נשאר מהרגע ההרשמה ולא תקף.
- **תוצאה:** Token refresh נכשל — המשתמש מתנתק אחרי פקיעת ה-access token.
- **מקור:** `Web` — commit `2e5c480`
- **דפוס:** ניהול מצב / concurrency

#### 34. CSS Reset דורס Tailwind Spacing
- **בעיה:** Global CSS reset עם `* { margin: 0; padding: 0; }` דרס Tailwind utility classes כמו `space-y-6`.
- **תוצאה:** טופס ההרשמה מוצג ללא רווחים — שדות דבוקים.
- **מקור:** `Web` — commit `c586691`
- **דפוס:** DOM / UI

#### 35. Token Balance גלובלי במקום per-child
- **בעיה:** Token balance חושב מטבלת `users` גלובלית — לא per-child. כל הילדים חלקו אותו balance.
- **תוצאה:** הילדים רואים אסימונים של אחיהם, economy שבור.
- **מקור:** `routine` — commit `259c2a3`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 36. Repo Selection לא נשמרת אחרי יצירה מ-ZIP
- **בעיה:** שמירת הריפו הנבחר הייתה מותנית בהצלחת DB write, אבל ה-session בזיכרון לא עודכן בכל מקרה. מצבים ישנים ב-context לא נוקו.
- **תוצאה:** משתמש יוצר ריפו מ-ZIP ואז פעולות הולכות לריפו הישן.
- **מקור:** `CodeBot` — commit `552cc20`
- **דפוס:** ניהול מצב / concurrency

---

### LOW

#### 37. Hash Normalization — פוסטים כפולים
- **בעיה:** Timestamps, Unicode PUA characters, ו-"עוד" suffixes גרמו ל-hash שונה לאותו תוכן.
- **תוצאה:** אותו פוסט נשלח ללקוח כמה פעמים.
- **מקור:** `Facebook-Leads-New` — commits `f10d3c4`, `be0cf4e`, `5e4d214`, `221f7e2`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 38. Scan Status תמיד "waiting" בפאנל
- **בעיה:** `scan_progress` חי במודול נפרד — ה-panel module לא ראה את המצב העדכני.
- **תוצאה:** הפאנל תמיד מציג "ממתין" גם כשסריקה רצה.
- **מקור:** `Facebook-Leads-New` — commits `454e530`, `4cb5c37`
- **דפוס:** ניהול מצב / concurrency

#### 39. Missing import — הודעת סיום סריקה לא נשלחת
- **בעיה:** `send_message` לא ייובא ב-`run_cycle` — scan completion message לעולם לא נשלח.
- **תוצאה:** הלקוח לא מקבל הודעה שהסריקה הסתיימה.
- **מקור:** `Facebook-Leads-New` — commit `0b4e6b6`
- **דפוס:** API design

#### 40. Escape שגוי ב-regex — `\b` בתוך Python triple-quote
- **בעיה:** `\b` (word boundary) בתוך Python triple-quoted string הוא backspace. ה-regex לא עבד כמצופה.
- **תוצאה:** סינון URLs שבור — פרופילים לא מזוהים נכון.
- **מקור:** `Facebook-Leads-New` — commit `fadc0dc`
- **דפוס:** ולידציית קלט

#### 41. Timestamp Regex תופס מספרי שנה
- **בעיה:** Regex לזיהוי timestamps תפס גם "2024" כמספר שעות/דקות — partial match.
- **תוצאה:** שנה ב-text נמחקת מה-hash — שינוי ב-dedup behavior.
- **מקור:** `Facebook-Leads-New` — commit `0bed0ba`
- **דפוס:** ולידציית קלט

#### 42. Negative Timedelta ב-Health Display
- **בעיה:** חישוב זמן מאז סריקה אחרונה יכל להחזיר timedelta שלילי (clock skew).
- **תוצאה:** תצוגה שבורה בפאנל — "סריקה אחרונה: לפני -3 דקות".
- **מקור:** `Facebook-Leads-New` — commit `9b15e0b`
- **דפוס:** ולידציית קלט

#### 43. joinedload + with_for_update — SQLAlchemy conflict
- **בעיה:** שילוב `joinedload` עם `with_for_update` גורם ל-SQLAlchemy warning/error.
- **תוצאה:** Warning logs מיותרים, פוטנציאל לבעיות lock.
- **מקור:** `Shipment-bot` — commit `4352bac`
- **דפוס:** async / control flow

#### 44. State Leak בין טסטים
- **בעיה:** Mock של in-memory state לא אופס בין טסטים — טסט אחד משפיע על השני.
- **תוצאה:** טסטים שעוברים/נכשלים בצורה לא דטרמיניסטית.
- **מקור:** `Shipment-bot` — commit `ba20192`
- **דפוס:** ניהול מצב / concurrency

#### 45. Pagination ללא Tiebreaker
- **בעיה:** מיון pagination ב-audit log ללא tiebreaker — רשומות עם אותו timestamp מחליפות סדר בין עמודים.
- **תוצאה:** רשומות חוזרות או נעלמות בדפדוף.
- **מקור:** `Shipment-bot` — commit `c0c1b74`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 46. Time Validation Regex שבור
- **בעיה:** Regex לבדיקת שעה לא דחה שעות/דקות לא חוקיות (כמו 25:00).
- **תוצאה:** שעות לא חוקיות נשמרות — scheduler מתנהג בצורה לא צפויה.
- **מקור:** `routine` — commit `769e26b`
- **דפוס:** ולידציית קלט

#### 47. IF NOT EXISTS לא נתמך ב-MySQL ADD COLUMN
- **בעיה:** Migration script השתמש ב-`ADD COLUMN IF NOT EXISTS` — syntax לא נתמך ב-MySQL.
- **תוצאה:** Migration נכשלת ב-deploy.
- **מקור:** `routine` — commit `80c1fc4`
- **דפוס:** סכימת DB

#### 48. VAPID_EMAIL ללא mailto: prefix
- **בעיה:** Web Push spec דורש `mailto:` prefix ב-VAPID email. ללא זה, `setVapidDetails` זורק exception.
- **תוצאה:** Push notifications לא עובדות.
- **מקור:** `routine` — commit `e5c26ad`
- **דפוס:** ולידציית קלט

#### 49. Malformed VAPID Keys מפילים שרת
- **בעיה:** `webpush.setVapidDetails` עם מפתחות פגומים זרק exception לא נתפס — הפיל את כל השרת.
- **תוצאה:** שרת crash ב-startup עם VAPID keys לא תקינים.
- **מקור:** `routine` — commit `2571c91`
- **דפוס:** ולידציית קלט

#### 50. Clipboard API Failure לא מטופל
- **בעיה:** כפתור העתקה קרא ל-`navigator.clipboard.writeText` ללא try-catch. ב-HTTP (לא HTTPS) ה-API לא זמין.
- **תוצאה:** לחיצה על "העתק" זורקת שגיאה ולא עושה כלום.
- **מקור:** `Markdown-Academy` — commit `b97d3f5`
- **דפוס:** DOM / UI

#### 51. Tooltip נסגר מיד בלחיצה
- **בעיה:** Tooltip component סגר את עצמו מיד בלחיצה בגלל event propagation.
- **תוצאה:** משתמש לא יכול לקרוא את הטיפ.
- **מקור:** `Markdown-Academy` — commit `315154e`
- **דפוס:** DOM / UI

#### 52. Dark Theme לא חל על CodeMirror
- **בעיה:** חסר `{ dark: true }` ב-darkTheme של CodeMirror — העורך נשאר בהיר.
- **תוצאה:** עורך בהיר על רקע כהה — לא קריא.
- **מקור:** `Markdown-Academy` — commit `2d73a4e`
- **דפוס:** DOM / UI

#### 53. Duplicate Migration + SSL Failure
- **בעיה:** קובץ migration כפול גרם לשגיאת build. בנוסף, חסרה הגדרת SSL לחיבור MySQL.
- **תוצאה:** Build נכשל ב-deploy.
- **מקור:** `Markdown-Academy` — commit `230e0c1`
- **דפוס:** סכימת DB

#### 54. cursorOffset שגוי ב-Snippets
- **בעיה:** Snippets הגדירו `cursorOffset` שגוי — הסמן נחת במקום הלא נכון אחרי הוספה.
- **תוצאה:** חוויית משתמש גרועה — צריך לזוז ידנית למקום הנכון.
- **מקור:** `Markdown-Academy` — commits `b40bfd0`, `08f18b3`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 55. Mermaid Race Condition ב-Preview
- **בעיה:** Mermaid rendering רץ לפני שה-DOM מוכן, או רץ על elements שכבר rendered.
- **תוצאה:** תרשימים לא מוצגים או מוצגים שבורים.
- **מקור:** `Markdown-Academy` — commits `7d31ccd`, `151fdc8`
- **דפוס:** ניהול מצב / concurrency

#### 56. Blocked Users Filter אחרי force_send
- **בעיה:** סינון מפרסמים חסומים קרה אחרי `force_send` — מפרסם חסום עם מילת מפתח force_send עדיין נשלח.
- **תוצאה:** לידים ממפרסמים חסומים נשלחים ללקוח.
- **מקור:** `Facebook-Leads-New` — commit `24ad356`
- **דפוס:** לוגיקה עסקית / מקרי קצה

#### 57. 500 Error נחשב כ-Invalid Credentials
- **בעיה:** כל שגיאת 500 מהשרת הציגה "שם משתמש או סיסמה שגויים" — גם כש-DB נפל.
- **תוצאה:** משתמשים חושבים שהסיסמה שגויה כשהבעיה בשרת.
- **מקור:** `Web` — commit `5196657`
- **דפוס:** API design

#### 58. Blob URL Memory Leak
- **בעיה:** Profile picture blob URL לא שוחרר ב-unmount — `URL.revokeObjectURL` לא נקרא.
- **תוצאה:** דליפת זיכרון קטנה בכל העלאת תמונה.
- **מקור:** `Web` — commit `f5cbaf9`
- **דפוס:** ניהול מצב / concurrency

#### 59. ER_NO_SUCH_TABLE ב-Fresh Deploy
- **בעיה:** Migration script ניסה ALTER TABLE על טבלה שלא קיימת ב-deploy ראשון.
- **תוצאה:** Migration נכשלת ב-deploy ראשוני.
- **מקור:** `routine` — commit `7a4e879`
- **דפוס:** סכימת DB

#### 60. Dedup Pending Persistence — LTM Memories
- **בעיה:** Dedup של זיכרונות ב-LTM לא נשמר — server restart מאבד את ה-dedup state.
- **תוצאה:** זיכרונות כפולים אחרי restart.
- **מקור:** `claude-code-config` — commit `3c48a9f`
- **דפוס:** ניהול מצב / concurrency

#### 61. Phone Normalization חסר בחיפוש
- **בעיה:** חיפוש משתמש לפי טלפון לא נרמל את הקלט — "0521234567" לא מצא "+972521234567".
- **תוצאה:** משתמש לא נמצא למרות שקיים.
- **מקור:** `Shipment-bot` — commit `9700ec6`
- **דפוס:** ולידציית קלט

#### 62. Aggregation Sort Memory Limit
- **בעיה:** MongoDB aggregation pipeline ללא `allowDiskUse` — מגבלת 100MB RAM על sort.
- **תוצאה:** שגיאה בשאילתות על datasets גדולים.
- **מקור:** `CodeBot` — commit `4824ad9`
- **דפוס:** DB consistency

---

## דפוסים שקלאוד קוד פספס (ותוקנו בדרך אחרת)

מניתוח ה-commits עולה שרוב התיקונים (>90%) בוצעו על ידי Claude Code עצמו (Author: Claude). עם זאת, ניתן לזהות דפוס ברור:

1. **באגים שנוצרו ותוקנו באותו סשן/PR** — שרשרות של fix commits בזה אחר זה מעידות שהקוד הראשוני שנכתב היה שגוי. דוגמאות:
   - OAuth ב-Amazon-bot: 8 commits של "fix OAuth" ברצף — כל אחד מתקן את הקודם
   - Health monitoring ב-Shipment-bot: 10+ commits של תיקונים ברצף
   - Scan status ב-Facebook-Leads: תוקן פעמיים כי התיקון הראשון לא עבד

2. **באגי async שחוזרים** — Missing await, blocking sync calls, event loop blocking — חוזרים בכל הפרויקטים. Claude Code לא מזהה אותם בעקביות.

3. **באגי concurrency** — Race conditions הם הדפוס הנפוץ ביותר (24%). Claude Code נוטה לכתוב קוד שעובד ב-single-thread אבל נשבר תחת concurrency.

---

## המלצות ל-CLAUDE.md

### כלל 1: בדוק await על כל קריאה לפונקציה async
> לפני push, חפש בכל הקבצים שהשתנו קריאות לפונקציות async. ודא שכל קריאה עטופה ב-`await`. coroutine object ללא await הוא תמיד truthy — זה באג שקט שיכול לשבור הכול.

### כלל 2: Race conditions — check-then-act חייב להיות אטומי
> אל תפריד בין בדיקת תנאי לביצוע פעולה. אם יש lock/mutex, הבדיקה חייבת להיות בתוכו. במיוחד: daily limits, dedup checks, state transitions. השתמש ב-`UPDATE ... WHERE status = 'X'` + `rowcount` במקום SELECT+UPDATE.

### כלל 3: אל תחשוף מידע פנימי ב-API responses
> לפני כל שינוי ב-error handling או exception classes, ודא ש-`to_dict()` / response body לא מכילים: internal IDs, password hashes, stack traces, מזהי DB, או הודעות שגיאה באנגלית טכנית. החזר הודעה גנרית בעברית למשתמש.

### כלל 4: ולידציית קלט מספרי — בדוק NaN, Inf, ו-edge cases
> בכל validator מספרי, בדוק קודם `math.isnan()` ו-`math.isinf()` (Python) או `Number.isNaN()` ו-`!Number.isFinite()` (JS). NaN comparisons תמיד מחזירות False — ה-NaN יעבור כל בדיקת טווח.

### כלל 5: SQLAlchemy async — אל תיגע ב-attributes אחרי commit/close
> אחרי `db.commit()`, כל ה-attributes של model objects דורשים re-fetch. חלץ ערכים פרימיטיביים (IDs, strings) לפני ה-commit, ואז בצע `db.execute(select(...))` מחדש בתוך הלולאה. זה מונע MissingGreenlet errors.
