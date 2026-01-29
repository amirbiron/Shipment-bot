# State Machine - מכונת מצבים

## סקירה כללית

המערכת משתמשת במכונת מצבים (State Machine) לניהול זרימת השיחה עם משתמשים.
כל משתמש נמצא במצב מסוים, וכל הודעה נכנסת גורמת למעבר למצב חדש.

## מצבי שולח (Sender States)

```
                    ┌─────────────┐
                    │   INITIAL   │
                    └──────┬──────┘
                           │ הודעה ראשונה
                           ▼
              ┌────────────────────────┐
              │ REGISTER.COLLECT_NAME  │
              └───────────┬────────────┘
                          │ הזנת שם
                          ▼
                    ┌──────────┐
         ┌─────────▶│   MENU   │◀─────────┐
         │          └────┬─────┘          │
         │               │                │
         │    ┌──────────┴──────────┐     │
         │    ▼                     ▼     │
         │ "משלוח חדש"        "המשלוחים שלי"
         │    │                     │     │
         │    ▼                     │     │
┌────────┴────────────────┐        │     │
│ DELIVERY.COLLECT_PICKUP │        │     │
└───────────┬─────────────┘        │     │
            │ כתובת איסוף          │     │
            ▼                      │     │
┌─────────────────────────────┐    │     │
│ DELIVERY.COLLECT_DROPOFF_MODE│    │     │
└───────────┬─────────────────┘    │     │
            │ בחירת מצב            │     │
            ▼                      │     │
┌──────────────────────────────┐   │     │
│ DELIVERY.COLLECT_DROPOFF_ADDR│   │     │
└───────────┬──────────────────┘   │     │
            │ כתובת יעד            │     │
            ▼                      │     │
    ┌───────────────┐              │     │
    │ DELIVERY.CONFIRM│             │     │
    └───────┬───────┘              │     │
            │                      │     │
     ┌──────┴──────┐               │     │
     ▼             ▼               │     │
  "אישור"       "ביטול"           │     │
     │             │               │     │
     │             └───────────────┘     │
     │                                   │
     └───────────────────────────────────┘
```

### תיאור מצבי שולח

| מצב | תיאור | פעולה הבאה |
|-----|-------|-----------|
| `INITIAL` | מצב התחלתי | בקשת שם |
| `REGISTER.COLLECT_NAME` | איסוף שם משתמש | מעבר לתפריט |
| `MENU` | תפריט ראשי | בחירת פעולה |
| `DELIVERY.COLLECT_PICKUP` | איסוף כתובת מוצא | בחירת מצב יעד |
| `DELIVERY.COLLECT_DROPOFF_MODE` | בחירת אופן הזנת יעד | איסוף כתובת |
| `DELIVERY.COLLECT_DROPOFF_ADDRESS` | איסוף כתובת יעד | אישור |
| `DELIVERY.CONFIRM` | אישור פרטי משלוח | יצירה/ביטול |

## מצבי שליח (Courier States)

```
                    ┌─────────────┐
                    │   INITIAL   │
                    └──────┬──────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ REGISTER.COLLECT_NAME  │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │ REGISTER.COLLECT_PHONE │
              └───────────┬────────────┘
                          │
                          ▼
                    ┌──────────┐
         ┌─────────▶│   MENU   │◀─────────────┐
         │          └────┬─────┘              │
         │               │                    │
         │    ┌──────────┼──────────┐         │
         │    ▼          ▼          ▼         │
         │ "משלוחים   "הארנק    "תפוס        │
         │  פתוחים"    שלי"     משלוח"       │
         │    │          │          │         │
         │    │          │          ▼         │
         │    │          │  ┌───────────────┐ │
         │    │          │  │CAPTURE.ENTER_ID│ │
         │    │          │  └───────┬───────┘ │
         │    │          │          │         │
         │    │          │          ▼         │
         │    │          │  ┌───────────────┐ │
         │    │          │  │CAPTURE.CONFIRM │ │
         │    │          │  └───────┬───────┘ │
         │    │          │          │         │
         │    └──────────┴──────────┴─────────┘
         │
         └────────────────────────────────────
```

### תיאור מצבי שליח

| מצב | תיאור | פעולה הבאה |
|-----|-------|-----------|
| `INITIAL` | מצב התחלתי | בקשת שם |
| `REGISTER.COLLECT_NAME` | איסוף שם | בקשת טלפון |
| `REGISTER.COLLECT_PHONE` | איסוף טלפון | מעבר לתפריט |
| `MENU` | תפריט ראשי | בחירת פעולה |
| `CAPTURE.ENTER_ID` | הזנת מזהה משלוח | אישור תפיסה |
| `CAPTURE.CONFIRM` | אישור תפיסת משלוח | ביצוע/ביטול |
| `VIEW_WALLET` | צפייה בארנק | חזרה לתפריט |
| `VIEW_DELIVERIES` | צפייה במשלוחים | חזרה לתפריט |

## מעברי מצב (Transitions)

### מעבר תקין
```python
current_state = "SENDER.DELIVERY.COLLECT_PICKUP"
message = "רחוב הרצל 15, תל אביב"

# וולידציה
if len(message) < 5:
    return same_state, "כתובת קצרה מדי"

# שמירה ב-context
context["pickup_address"] = message

# מעבר למצב הבא
new_state = "SENDER.DELIVERY.COLLECT_DROPOFF_MODE"
```

### Context (הקשר)
כל session שומר context עם נתונים זמניים:

```json
{
  "name": "ישראל ישראלי",
  "pickup_address": "רחוב הרצל 15, תל אביב",
  "dropoff_address": null,
  "current_delivery_id": null
}
```

## טיפול בשגיאות

### הודעה לא צפויה
```
מצב נוכחי: MENU
הודעה: "בננה"
תגובה: "לא הבנתי. אנא בחרו אפשרות מהתפריט"
מצב חדש: MENU (ללא שינוי)
```

### Timeout
אם משתמש לא מגיב זמן רב, ה-session נשמר ב-DB וממשיך מאותו מצב בהודעה הבאה.

## קוד לדוגמה

```python
class StateHandler:
    async def handle_message(self, user_id, platform, message):
        # קבלת מצב נוכחי
        current_state = await self.get_state(user_id, platform)
        context = await self.get_context(user_id, platform)

        # קבלת handler מתאים
        handler = self.get_handler(current_state)

        # עיבוד ההודעה
        response, new_state, context_update = await handler(message, context)

        # שמירת מצב חדש
        if new_state != current_state:
            await self.transition_to(user_id, platform, new_state, context_update)

        return response
```

## הרחבת מצבים

להוספת מצב חדש:

1. הגדרת המצב ב-`states.py`:
```python
class SenderState(str, Enum):
    NEW_STATE = "sender.new_state"
```

2. הוספת handler ב-`handlers.py`:
```python
async def _handle_new_state(self, message, context):
    # לוגיקה
    return response, next_state, context_update
```

3. רישום ב-mapping:
```python
handlers = {
    SenderState.NEW_STATE.value: self._handle_new_state,
}
```
