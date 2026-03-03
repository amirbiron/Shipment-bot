# אפיון iDriver - חלוקה לסשנים (#233)

> חלוקה לסשני מימוש מסודרים לפי סדר תלויות.
> כל סשן הוא PR עצמאי שניתן לבדוק ולמרג' בנפרד.

---

## סשן 1: תשתית — מודלים, מיגרציה ו-State Machine

### מטרה
להקים את כל שכבת הנתונים והתשתית עליה ייבנו כל הסשנים הבאים.

### משימות

1. **הוספת `UserRole.DRIVER`** ב-`app/db/models/user.py`
   - הוספה ל-enum `UserRole`
   - עדכון כל ה-`if role ==` בקודבייס (telegram.py, whatsapp.py) עם branch מפורש ל-`DRIVER`

2. **מודל `DriverProfile`** (טבלה חדשה `driver_profiles`)
   - `user_id` (FK → users.id, unique)
   - `birth_date` (Date)
   - `vehicle_description` (String) — "סיינה 2025 חדישה"
   - `vehicle_category` (Enum) — פרטי 4 / מיני 6 / מיני עד 8 / מיניוואן 6 / 7 מק' / 8 מק' / מעל 8
   - `dress_code` (Enum) — חסיד שחור לבן / חרדי שחור לבן / חרדי מודרני / דתי אלגנט / דתי מעורב / חילוני
   - `verification_status` (Enum) — unverified / pending / approved / rejected
   - `verification_selfie_file_id` (Text, nullable)
   - `verification_id_file_id` (Text, nullable)
   - `rejection_reason` (Text, nullable)
   - `subscription_start` / `subscription_end` (DateTime, nullable)
   - `is_trial` (Boolean, default=True)
   - `created_at`, `updated_at`

3. **מודל `DriverSearchSettings`** (טבלה `driver_search_settings`)
   - `user_id` (FK → users.id, unique)
   - `vehicle_type_filter` (Enum) — 7 אפשרויות (כפי שבאפיון)
   - `trip_type_filter` (Enum) — 5 אפשרויות
   - `show_deliveries` (Boolean, default=True)
   - `upcoming_timeframe` (Enum) — hour / two_hours / five_hours / all
   - `future_only` (Boolean, default=False)
   - `future_start_time` (Time, nullable) — שעה שממנה לחפש (כשfuture_only=True)

4. **מודל `DriverSearch`** (טבלה `driver_searches`)
   - `id` (PK)
   - `user_id` (FK → users.id, index)
   - `origin_city` (String, nullable) — עיר מוצא
   - `destination_city` (String) — עיר יעד
   - `is_area_search` (Boolean, default=False) — חיפוש אזורי
   - `location_lat` / `location_lng` (Float, nullable) — מיקום לחיפוש
   - `status` (Enum) — active / paused / deleted
   - `created_at`, `last_active_at`
   - אינדקס: `(user_id, status)`
   - constraint: מקסימום 9 חיפושים פעילים למשתמש

5. **מודל `DriverSession`** (טבלה `driver_sessions`)
   - `user_id` (FK → users.id, unique)
   - `started_at` (DateTime) — תחילת סשן
   - `last_message_at` (DateTime) — הודעה אחרונה
   - `is_active` (Boolean, default=True)
   - `reminder_sent` (Boolean, default=False) — האם נשלחה תזכורת ניתוק

6. **enum `DriverState`** ב-`app/state_machine/states.py`
   ```
   DRIVER_INITIAL
   DRIVER_NEW

   # רישום (שלבים 1-4)
   DRIVER_REGISTER_NAME
   DRIVER_REGISTER_BIRTH_DATE
   DRIVER_REGISTER_VEHICLE
   DRIVER_REGISTER_DRESS_CODE

   # אימות חרדי (שלב 5)
   DRIVER_VERIFY_SELFIE
   DRIVER_VERIFY_PENDING

   # תפריט ראשי
   DRIVER_MENU

   # הגדרות חיפוש
   DRIVER_SETTINGS_VEHICLE_TYPE
   DRIVER_SETTINGS_TRIP_TYPE
   DRIVER_SETTINGS_DELIVERIES
   DRIVER_SETTINGS_TIMEFRAME
   DRIVER_SETTINGS_FUTURE_ONLY
   DRIVER_SETTINGS_FUTURE_TIME

   # ניהול חיפושים
   DRIVER_SEARCH_ACTIVE
   DRIVER_VIEW_SEARCHES

   # מנויים
   DRIVER_SUBSCRIPTION_MENU
   DRIVER_SUBSCRIPTION_PURCHASE
   ```

7. **מיגרציה** — סקריפט DDL עם כל הטבלאות החדשות

8. **ניתוב בסיסי** — עדכון `_route_to_role_menu()` ב-telegram.py ו-whatsapp.py עם branch ל-`DRIVER`

### תלויות
אין — זה הסשן הבסיסי.

### קבצים מושפעים
- `app/db/models/user.py` — UserRole enum
- `app/db/models/driver_profile.py` — **חדש**
- `app/db/models/driver_search.py` — **חדש**
- `app/db/models/driver_search_settings.py` — **חדש**
- `app/db/models/driver_session.py` — **חדש**
- `app/state_machine/states.py` — DriverState enum
- `app/db/migrations.py` — מיגרציה חדשה
- `app/api/webhooks/telegram.py` — ניתוב בסיסי
- `app/api/webhooks/whatsapp.py` — ניתוב בסיסי
- `tests/test_driver_models.py` — **חדש**

---

## סשן 2: זרימת רישום (שלבים 1–4)

### מטרה
לממש את כל שלבי הרישום של נהג חדש, מהודעת ברוכים הבאים ועד בחירת זרם (dress code).

### משימות

1. **הודעת ברוכים הבאים** — כשמשתמש חדש עם role=DRIVER שולח `/start`:
   - הצגת הודעת קבלת פנים עם תיאור השירות
   - כפתור "הרשמה" להתחלת תהליך

2. **שלב 1: שם מלא**
   - בקשת שם מלא מהמשתמש
   - ולידציה עם `NameValidator`
   - שמירה ב-`User.full_name`

3. **שלב 2: תאריך לידה**
   - בקשת תאריך בפורמט dd/mm/yyyy
   - ולידציה (פורמט + טווח הגיוני — גיל 16-99)
   - חישוב גיל ושמירה ב-`DriverProfile.birth_date`

4. **שלב 3: אישור + רכב**
   - הצגת סיכום: שם + גיל
   - בקשת סוג רכב + שנה (טקסט חופשי: "סיינה 2025 חדישה")
   - שמירה ב-`DriverProfile.vehicle_description`

5. **שלב 4: בחירת זרם (Dress Code)**
   - הצגת 6 אפשרויות ככפתורים + "ביטול וחזרה"
   - שמירה ב-`DriverProfile.dress_code`
   - **אם הזרם חרדי** (חסיד / חרדי שחור לבן / חרדי מודרני) → מעבר לסשן 3 (אימות)
   - **אם לא** → מעבר ישיר לתפריט ראשי

6. **Handler חדש: `DriverStateHandler`** ב-`app/state_machine/driver_handler.py`
   - מימוש של states: `DRIVER_NEW` → `REGISTER_NAME` → `REGISTER_BIRTH_DATE` → `REGISTER_VEHICLE` → `REGISTER_DRESS_CODE`

7. **שירות `DriverRegistrationService`** ב-`app/domain/services/driver_registration_service.py`
   - `start_registration(user_id)` → יצירת DriverProfile ריק
   - `save_name(user_id, name)`
   - `save_birth_date(user_id, date_str)` — כולל ולידציה ופרסור
   - `save_vehicle(user_id, vehicle_desc)`
   - `save_dress_code(user_id, dress_code)` — כולל קביעה אם צריך אימות
   - `requires_verification(dress_code)` → bool

### תלויות
סשן 1 (מודלים + state enum)

### קבצים מושפעים
- `app/state_machine/driver_handler.py` — **חדש**
- `app/domain/services/driver_registration_service.py` — **חדש**
- `app/api/webhooks/telegram.py` — חיבור ל-handler
- `app/api/webhooks/whatsapp.py` — חיבור ל-handler
- `tests/test_driver_registration.py` — **חדש**

---

## סשן 3: אימות חרדי (שלב 5) + אישור אדמין

### מטרה
לממש את זרימת האימות לנהגים שבחרו זרם חרדי, כולל צד האדמין.

### משימות

1. **הצגת כרטיס נהג (לא מאומת)**
   - פורמט: `"🕵🏼 • שם מלא - {name} | 🔞 גיל - {age} | 🏎 רכב - {vehicle} | 🦓 זרם: {stream} - לא מאומת"`
   - הודעה: "➖ הנך לא מזוהה במערכת ➖"

2. **בקשת סלפי לאימות**
   - כפתור "אימות" → בקשה לשלוח צילום סלפי עדכני
   - ולידציה שזו תמונה (לא מסמך/טקסט)
   - שמירה ב-`DriverProfile.verification_selfie_file_id`
   - כפתור "נסה שנית" במקרה של טעות

3. **שליחה לאדמין לאישור**
   - שליחת הסלפי + תעודת זהות (אם נדרש) לאדמין מערכת
   - כפתורי inline לאדמין: "אשר נהג" / "דחה נהג"
   - שימוש בדפוס הקיים של `courier_approval_service` / `admin_notification_service`

4. **אישור ע"י אדמין**
   - `verification_status` = `approved`
   - הודעה לנהג: "האימות שלך אושר! ברוך הבא 🎉"
   - מעבר ל-`DRIVER_MENU`

5. **דחייה ע"י אדמין**
   - `verification_status` = `rejected`
   - בקשת סיבת דחייה מהאדמין (טקסט חופשי)
   - הודעה לנהג: "האימות שלך נדחה ע"י המנהלים. סיבה: {reason}"
   - אפשרות לנסות שוב

6. **שירות `DriverVerificationService`**
   - `submit_verification(user_id, selfie_file_id)`
   - `approve_driver(user_id, admin_id)`
   - `reject_driver(user_id, admin_id, reason)`
   - `notify_driver_result(user_id, approved, reason?)`

### תלויות
סשן 2 (רישום)

### קבצים מושפעים
- `app/state_machine/driver_handler.py` — states VERIFY_SELFIE, VERIFY_PENDING
- `app/domain/services/driver_verification_service.py` — **חדש**
- `app/domain/services/admin_notification_service.py` — הוספת התראות לאימות נהגים
- `app/api/webhooks/telegram.py` — callback לאישור/דחיית אדמין
- `tests/test_driver_verification.py` — **חדש**

---

## סשן 4: תפריט ראשי + הגדרות חיפוש

### מטרה
לממש את התפריט הראשי של הנהג ואת כל תת-התפריט של הגדרות חיפוש.

### משימות

1. **תפריט ראשי** (תגובה לפקודה "תפריט" / "ת")
   - ברכה: `"▪️ בוקר טוב/צהריים טובים/ערב טוב {שם נהג} ▪️"`
   - סטטוס מנוי: `"מנוי פעיל עד {date}"` או `"שבוע ניסיון"`
   - מצב חיפוש: 🌞 מחובר/מנותק + 📍 יעדים פעילים
   - הגדרות נוכחיות: 🚙 רכב | 🛣 סוג נסיעה | 💌 משלוחים | 🕐 טווח זמן | 📅 עתידי
   - הנחיות טקסט: "רשום מדריך" / "רשום מילון" / "רשום הרשמה"
   - כפתורים: הגדרות חיפוש | ניהול | פרמיום | רכישת מנוי | הוראות שימוש

2. **תת-תפריט הגדרות חיפוש** (🛠)
   - הצגת סטטוס נוכחי לכל פרמטר

3. **בחירת סוג רכב** (7 אפשרויות + ביטול)
   - פרטי 4 מקומות
   - מיני קטן 6 מקומות
   - מיני ומעלה עד 8 מקומות
   - מיניוואן 6 מקומות מרווח
   - 7 מקומות
   - 8 מקומות
   - מעל 8 מקומות
   - שמירה ב-`DriverSearchSettings.vehicle_type_filter`

4. **בחירת סוג נסיעה** (5 אפשרויות + ביטול)
   - מעל 100 ש"ח פנימיות ובינעירוני
   - מתחת ל-100 ש"ח פנימיות וקצרות
   - משלוחים בלבד
   - נהגות בלבד
   - כל סוגי הנסיעות
   - שמירה ב-`DriverSearchSettings.trip_type_filter`

5. **הצגת משלוחים** (כן/לא)
   - שמירה ב-`DriverSearchSettings.show_deliveries`

6. **טווח זמן קרוב** (4 אפשרויות + ביטול)
   - לשעה הקרובה (ברירת מחדל)
   - לשעתיים הקרובות
   - 5 שעות הקרובות
   - הצג הכל
   - שמירה ב-`DriverSearchSettings.upcoming_timeframe`

7. **חיפוש עתידי בלבד**
   - הודעת אזהרה: "שים לב, כרגע אין אפשרות לחפש גם נסיעה מיידית וגם עתידית..."
   - אישור (כן/לא)
   - אם כן → בקשת שעה (פורמט HH:MM)
   - שמירה ב-`DriverSearchSettings.future_only` + `future_start_time`

8. **שירות `DriverMenuService`**
   - `get_main_menu(user_id)` → מחזיר הודעת תפריט + כפתורים
   - `get_settings_menu(user_id)` → תת-תפריט הגדרות
   - `update_vehicle_type(user_id, vehicle_type)`
   - `update_trip_type(user_id, trip_type)`
   - `update_delivery_display(user_id, show)`
   - `update_timeframe(user_id, timeframe)`
   - `update_future_only(user_id, enabled, start_time?)`

### תלויות
סשן 1 (מודלים), סשן 2 (רישום — הנהג צריך להיות רשום)

### קבצים מושפעים
- `app/state_machine/driver_handler.py` — states MENU + SETTINGS_*
- `app/domain/services/driver_menu_service.py` — **חדש**
- `tests/test_driver_menu.py` — **חדש**

---

## סשן 5: חיפוש נסיעות

### מטרה
לממש את ליבת המערכת — חיפוש נסיעות לפי פקודות טקסט.

### משימות

1. **פקודת "פ" — חיפוש לפי יעד**
   - `"פ ים"` — נסיעות לירושלים
   - `"פ בב ים"` — מתל אביב לירושלים
   - פרסור: פיצול לפי רווחים, זיהוי קיצורי ערים
   - יצירת `DriverSearch` עם origin + destination

2. **פקודת "פ א" — חיפוש אזורי**
   - `"פ א ספר"` — אזור שדרות
   - `"פ ים א טבריה"` — מירושלים לאזור טבריה
   - סימון `is_area_search=True`

3. **פקודת "פ מיקום" — חיפוש לפי מיקום**
   - קבלת שיתוף מיקום מטלגרם/וואטסאפ
   - שמירת `location_lat` / `location_lng`

4. **ניהול יעדים**
   - מקסימום 9 יעדים פעילים
   - הצגת יעדים נוכחיים בתפריט
   - הודעת שגיאה אם חורג מ-9

5. **מילון קיצורי ערים**
   - טבלת מיפוי: "בב" → "בני ברק", "ים" → "ירושלים", וכו'
   - אפשרות להרחבה עתידית

6. **הצגת תוצאות חיפוש**
   - סינון לפי הגדרות חיפוש (סוג רכב, סוג נסיעה, משלוחים, טווח זמן)
   - פורמט הצגה של נסיעה (מוצא, יעד, מחיר, זמן)

7. **בקשת נסיעה**
   - אם הנהג חבר בקבוצה → הצגת מכרז ישירות
   - אם לא חבר → הבוט שולח בקשה עם כרטיס נהג

8. **שירות `DriverSearchService`**
   - `create_search(user_id, origin?, destination, is_area?)` → DriverSearch
   - `create_location_search(user_id, lat, lng)`
   - `get_active_searches(user_id)` → list
   - `apply_filters(searches, settings)` → filtered results
   - `request_ride(user_id, ride_id)` → בקשה

9. **שירות `CityAbbreviationService`**
   - `resolve(abbreviation)` → full city name
   - `parse_search_command(text)` → (origin?, destination, is_area?)

### תלויות
סשן 1 (מודלים), סשן 4 (הגדרות חיפוש)

### קבצים מושפעים
- `app/state_machine/driver_handler.py` — חיפוש states
- `app/domain/services/driver_search_service.py` — **חדש**
- `app/domain/services/city_abbreviation_service.py` — **חדש**
- `app/api/webhooks/telegram.py` — זיהוי פקודות "פ", "פ א", "פ מיקום"
- `tests/test_driver_search.py` — **חדש**

---

## סשן 6: ניהול חיפושים + סשן 24 שעות

### מטרה
לממש פקודות ניהול חיפושים ולוגיקת ניתוק אוטומטי.

### משימות

1. **פקודת "ע" — השהיית חיפושים**
   - שינוי סטטוס כל החיפושים הפעילים ל-`paused`
   - הודעה: "החיפושים הושהו"

2. **פקודת "ה" — חידוש חיפושים**
   - שינוי סטטוס כל החיפושים המושהים ל-`active`
   - הודעה: "החיפושים חודשו"

3. **פקודת "מ" — מחיקת חיפוש**
   - הצגת רשימת חיפושים פעילים
   - בחירה למחיקה (כפתורים)
   - שינוי סטטוס ל-`deleted`

4. **פקודת "ממ" — מחיקת כל החיפושים**
   - אישור ("האם אתה בטוח?")
   - מחיקת כל החיפושים

5. **לוגיקת סשן 24 שעות**
   - עדכון `last_message_at` בכל הודעה מנהג פעיל
   - Celery periodic task: כל דקה, בדוק סשנים שהגיעו ל-23:58
   - שליחת תזכורת: "👤 נהג יקר - אנחנו מבצעים ניתוק יזום כל 24 שעות, על מנת להמשיך עם החיפוש אנא הקלד את המילה - תפריט"
   - בדיקה נוספת: סשנים שחלפו 24 שעות → ניתוק (paused all searches)
   - אם הנהג שולח "תפריט" → חידוש סשן חדש

6. **שירות `DriverSessionService`**
   - `start_session(user_id)` → DriverSession
   - `touch_session(user_id)` → עדכון last_message_at
   - `check_expiring_sessions()` → list of sessions to warn
   - `disconnect_expired_sessions()` → list of disconnected
   - `is_session_active(user_id)` → bool

7. **Celery task**
   - `check_driver_sessions` — periodic task כל דקה

### תלויות
סשן 5 (חיפוש — צריך חיפושים פעילים כדי לנהל)

### קבצים מושפעים
- `app/state_machine/driver_handler.py` — פקודות ע/ה/מ/ממ
- `app/domain/services/driver_session_service.py` — **חדש**
- `app/workers/tasks.py` — task חדש
- `app/api/webhooks/telegram.py` — זיהוי פקודות חד-אותיות
- `tests/test_driver_session.py` — **חדש**
- `tests/test_driver_search_management.py` — **חדש**

---

## סשן 7: פרסום נסיעות + מחירון

### מטרה
לאפשר לנהגים לפרסם נסיעות חינם ולבדוק מחירון.

### משימות

1. **פרסום נסיעה חופשית**
   - זיהוי פורמט: `"בב ים 5 מק 150 ש״ח"` (מוצא, יעד, מקומות, מחיר)
   - פרסור הפקודה ושליפת פרטים
   - הפצה אוטומטית לקבוצות רלוונטיות (לפי מוצא + יעד)
   - אישור לנהג שהנסיעה פורסמה

2. **שירות הפצה לקבוצות**
   - זיהוי קבוצות רלוונטיות לפי אזור
   - שליחת הודעת נסיעה מפורמטת לכל קבוצה
   - שימוש בתבנית הודעה אחידה

3. **פקודת "מחירון"**
   - `"מחירון בב ים"` — מוצא ליעד
   - חישוב / שליפת מחיר מומלץ
   - הצגה לנהג

4. **שירות `RidePostingService`**
   - `parse_ride_posting(text)` → (origin, destination, seats, price)
   - `post_ride(user_id, origin, dest, seats, price)` → הפצה לקבוצות
   - `get_relevant_groups(origin, destination)` → list of group_ids

5. **שירות `PricingService`**
   - `get_price_estimate(origin, destination)` → price range

### תלויות
סשן 1 (מודלים), סשן 5 (שירות קיצורי ערים)

### קבצים מושפעים
- `app/domain/services/ride_posting_service.py` — **חדש**
- `app/domain/services/pricing_service.py` — **חדש**
- `app/api/webhooks/telegram.py` — זיהוי פורמט פרסום + פקודת מחירון
- `tests/test_ride_posting.py` — **חדש**
- `tests/test_pricing.py` — **חדש**

---

## סשן 8: מנויים ופרמיום

### מטרה
לממש מערכת מנויים עם שבוע ניסיון חינם ורכישת מנוי.

### משימות

1. **שבוע ניסיון חינם**
   - הפעלה אוטומטית עם סיום רישום
   - `DriverProfile.is_trial = True`
   - `subscription_end = now + 7 days`
   - הודעה בתפריט: "שבוע ניסיון — נותרו X ימים"

2. **תפריט מנויים**
   - הצגת סטטוס מנוי נוכחי
   - אפשרויות רכישה (חודש / מספר חודשים)
   - מחירון מנויים

3. **רכישת מנוי**
   - בחירת תקופה
   - מנגנון תשלום (לינק חיצוני / bit / PayBox — לפי החלטה)
   - עדכון `subscription_end`
   - `is_trial = False`

4. **בדיקת תוקף מנוי**
   - בכל חיפוש — בדיקה שהמנוי פעיל
   - הודעה אם המנוי פג: "המנוי שלך פג תוקף. רכוש מנוי כדי להמשיך לחפש"
   - הגבלת חיפוש לנהגים עם מנוי פעיל בלבד

5. **Celery task — תזכורת פקיעה**
   - יום לפני פקיעת מנוי → הודעת תזכורת
   - ביום הפקיעה → הודעה שהמנוי פג

6. **שירות `DriverSubscriptionService`**
   - `activate_trial(user_id)`
   - `purchase_subscription(user_id, months)`
   - `is_subscription_active(user_id)` → bool
   - `get_subscription_status(user_id)` → status dict
   - `check_expiring_subscriptions()` → list for reminders

### תלויות
סשן 2 (רישום — DriverProfile קיים)

### קבצים מושפעים
- `app/state_machine/driver_handler.py` — states SUBSCRIPTION_*
- `app/domain/services/driver_subscription_service.py` — **חדש**
- `app/workers/tasks.py` — tasks לתזכורות מנוי
- `tests/test_driver_subscription.py` — **חדש**

---

## סשן 9: תפקידי תחנה וסדרן (iDriver)

### מטרה
להתאים את מערכת התחנות והסדרנים הקיימת לעבוד גם עם נהגי iDriver.

### משימות

1. **מנהל תחנה (Station Manager)**
   - שימוש בתשתית הקיימת של `StationOwnerState`
   - הוספת אפשרות לנהל נהגי iDriver (נוסף על שליחים)
   - הגדרת סדרנים מתוך נהגי התחנה

2. **סדרן (Dispatcher) — תפריט סדרן**
   - שימוש בתשתית הקיימת של `DispatcherState`
   - הוספת העלאת נסיעה/משלוח למערכת ההפצה
   - ניהול מכרזים — הצעה לנהגים רלוונטיים
   - מעקב תשלומים

3. **אינטגרציה עם חיפוש**
   - נסיעות שמפרסם סדרן → מופיעות בתוצאות חיפוש של נהגים
   - לפי סינון (אזור, סוג רכב, סוג נסיעה)

4. **הרשאות**
   - בדיקה שהסדרן מורשה לתחנה הספציפית
   - בדיקה שמנהל התחנה הוא אכן בעלים

### תלויות
סשנים 1-5 (כל התשתית + חיפוש)

### קבצים מושפעים
- `app/state_machine/station_owner_handler.py` — התאמות
- `app/state_machine/dispatcher_handler.py` — התאמות
- `app/domain/services/station_service.py` — התאמות
- `tests/test_driver_station_integration.py` — **חדש**

---

## סשן 10: אינטגרציה WhatsApp, בדיקות מקיפות ותיעוד

### מטרה
להשלים תמיכה דו-פלטפורמית, בדיקות מקיפות ועדכון תיעוד.

### משימות

1. **WhatsApp handler parity**
   - כל הזרימות שנבנו בטלגרם — מימוש מקביל בוואטסאפ
   - fallback לקבוצות (keyboard=None + הנחיות טקסטואליות)
   - סינון מספרי טלפון (tg: / @g.us)

2. **בדיקות E2E**
   - זרימת רישום מלאה (שלבים 1-5)
   - חיפוש + הגדרות + ניהול חיפושים
   - סשן 24 שעות (mock של זמן)
   - פרסום נסיעה
   - מנויים

3. **בדיקות Edge Cases**
   - רישום כפול
   - חיפוש בלי מנוי פעיל
   - 9+ יעדים
   - פרסור פקודות שגויות
   - concurrency — שני נהגים על אותה נסיעה

4. **עדכון CLAUDE.md**
   - הוספת `DriverState` לדיאגרמות state machine
   - עדכון מבנה קבצים

5. **עדכון מסמכי ארכיטקטורה**
   - DATABASE.md — טבלאות חדשות
   - STATE_MACHINE.md — מצבי iDriver

### תלויות
כל הסשנים הקודמים

### קבצים מושפעים
- `app/api/webhooks/whatsapp.py`
- `app/api/webhooks/whatsapp_cloud.py`
- `CLAUDE.md`
- `DATABASE.md`
- `STATE_MACHINE.md`
- `tests/test_driver_e2e.py` — **חדש**
- `tests/test_driver_whatsapp.py` — **חדש**

---

## תרשים תלויות בין סשנים

```
סשן 1 (תשתית)
├── סשן 2 (רישום 1-4)
│   ├── סשן 3 (אימות חרדי)
│   └── סשן 8 (מנויים)
├── סשן 4 (תפריט + הגדרות)
│   └── סשן 5 (חיפוש)
│       ├── סשן 6 (ניהול חיפושים + סשן 24ש)
│       └── סשן 9 (תחנה + סדרן)
├── סשן 7 (פרסום + מחירון) ← תלוי גם בסשן 5
└── סשן 10 (אינטגרציה + בדיקות) ← תלוי בכולם
```

## סדר מימוש מומלץ

| סדר | סשן | תלוי ב- | הערות |
|-----|------|---------|-------|
| 1 | סשן 1 — תשתית | - | **חייב להיות ראשון** |
| 2 | סשן 2 — רישום | סשן 1 | הבסיס לכל אינטראקציה |
| 3 | סשן 3 — אימות | סשן 2 | משלים את הרישום |
| 4 | סשן 4 — תפריט + הגדרות | סשן 1 | ניתן במקביל לסשן 3 |
| 5 | סשן 5 — חיפוש | סשן 4 | הפיצ'ר המרכזי |
| 6 | סשן 8 — מנויים | סשן 2 | ניתן במקביל לסשן 5 |
| 7 | סשן 6 — ניהול חיפושים | סשן 5 | |
| 8 | סשן 7 — פרסום + מחירון | סשן 5 | ניתן במקביל לסשן 6 |
| 9 | סשן 9 — תחנה + סדרן | סשן 5 | |
| 10 | סשן 10 — אינטגרציה | הכל | **חייב להיות אחרון** |
