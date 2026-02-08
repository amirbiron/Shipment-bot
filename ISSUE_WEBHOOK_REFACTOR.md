# refactor: רפקטור מבני ל-telegram webhook - מניעת קטגוריות באגים חוזרות

## רקע

במהלך יישום שלב 3 (issue #78), הבאגבוט מצא **18+ באגים** שחוזרים על אותם 3 דפוסים בדיוק.
הסיבה: `telegram_webhook` היא **פונקציה אחת של 517 שורות** עם nested ifs, לוקאפ תחנות כפול, ו-keyword matching מפוזר.

## הבעיה — 3 קטגוריות באגים שחוזרות

### 1. לוקאפ תחנה משוכפל (5 מקומות)
`get_station_by_owner` נקרא 3 פעמים, `get_dispatcher_station` 2 פעמים.
כל מקום צריך fallback, וכשמתקנים במקום אחד שוכחים מקום אחר.

**באגים שנבעו מזה:** לולאה אינסופית ל-station owner ללא תחנה, fallback חסר ב-dispatcher button click, fallback חסר ב-DISPATCHER.* state, קריסת `MultipleResultsFound`.

### 2. keyword matching מפוזר ללא guards
כל כפתור נבדק ב-`if "keyword" in text` נפרד. קל לשכוח role guard או multi-step flow guard.

**מצב נוכחי:**
| keyword | guard |
|---------|-------|
| "הצטרפות למנוי" | `role == SENDER` ✅ |
| "משלוח מהיר" | `role == SENDER` ✅ |
| "תחנה" | `role == SENDER` ✅ |
| "פנייה לניהול" | `role == SENDER` ✅ |
| "שלוח" / "חבילה" | **ללא guard** ⚠️ |

**באגים שנבעו מזה:** "תחנה" תפס כתובות, "חזרה לתפריט" תפס ניווט סדרן, "משלוח מהיר" תפס כל התפקידים, "פנייה לניהול" תפס כל התפקידים.

### 3. ניתוב תפקידים ב-if/elif שטוח
כל תפקיד מטופל ב-if/elif chain ענק. קל לשכוח fallback, לשכפל קוד, או ליצור fall-through.

**באגים שנבעו מזה:** `else` גנרי שתפס תפקידים לא צפויים, ADMIN הפעיל "unknown role" warning, station owner without station נפל ל-SENDER check.

---

## הפתרון — רפקטור מבני

### שלב 1: חילוץ handler לכל תפקיד

במקום 517 שורות בפונקציה אחת:

```python
async def _handle_station_owner(user, db, state_manager, text, photo_file_id) -> tuple | None:
    """ניתוב הודעות בעל תחנה — lookup תחנה פעם אחת"""
    station_service = StationService(db)
    station = await station_service.get_station_by_owner(user.id)
    if not station:
        # הורדת תפקיד וחזרה ל-sender
        ...
    handler = StationOwnerStateHandler(db, station.id)
    return await handler.handle_message(user, text, photo_file_id)

async def _handle_dispatcher_flow(user, db, state_manager, text, photo_file_id) -> tuple | None:
    """ניתוב הודעות סדרן — lookup תחנה פעם אחת"""
    station_service = StationService(db)
    station = await station_service.get_dispatcher_station(user.id)
    if not station:
        # fallback אחד ויחיד
        ...
```

**יתרון:** לוקאפ תחנה פעם אחת, fallback במקום אחד. אי אפשר לשכוח fallback.

### שלב 2: טבלת ניתוב לכפתורים

במקום `if "keyword" in text` מפוזר:

```python
_SENDER_BUTTON_ROUTES: list[tuple[str, Callable]] = [
    ("הצטרפות למנוי", _handle_courier_signup),
    ("העלאת משלוח מהיר", _handle_fast_shipment),
    ("הצטרפות כתחנה", _handle_station_signup),
    ("פנייה לניהול", _handle_admin_contact),
]

# ניתוב מרכזי — guard אחד ויחיד
if not _is_in_multi_step_flow and user.role == UserRole.SENDER:
    for keyword, handler_fn in _SENDER_BUTTON_ROUTES:
        if keyword in text:
            return await handler_fn(...)
```

**יתרון:** guard אחד לכל הכפתורים, אי אפשר לשכוח role check. הוספת כפתור חדש = שורה אחת בטבלה.

### שלב 3: telegram_webhook הופך ל-dispatcher פשוט (~80 שורות)

```python
async def telegram_webhook(update, background_tasks, db):
    # 1. פרסור update -> user, text, photo
    # 2. /start, # -> _route_to_role_menu()
    # 3. כפתורי שיווק -> טבלת ניתוב
    # 4. ניתוב לפי תפקיד -> _handle_<role>()
    # 5. default -> welcome
```

מ-517 שורות ל-~80 שורות dispatcher + פונקציות ממוקדות.

---

## תוצאה צפויה

| קטגוריית באג | לפני | אחרי |
|-------------|------|------|
| fallback חסר לתחנה | 6 באגים | lookup פעם אחת — בלתי אפשרי |
| keyword ללא guard | 4 באגים | guard אחד מרכזי — בלתי אפשרי |
| role routing שגוי | 4 באגים | handler נפרד לכל role — בלתי אפשרי |

## קבצים מושפעים
- `app/api/webhooks/telegram.py` — רפקטור מרכזי
- `tests/test_webhook_routing.py` — עדכון בדיקות

## הערות
- אין שינוי בהתנהגות — רפקטור פנימי בלבד
- כל 183 הבדיקות הקיימות חייבות לעבור ללא שינוי
