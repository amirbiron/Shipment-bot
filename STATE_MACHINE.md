# State Machine - מכונת מצבים

## סקירה כללית

המערכת משתמשת במכונת מצבים (State Machine) לניהול זרימת השיחה עם משתמשים.
כל משתמש נמצא במצב מסוים, וכל הודעה נכנסת גורמת למעבר למצב חדש.

**מקור האמת:** `app/state_machine/states.py` — כל ה-enums וה-transition dictionaries מוגדרים שם.

**ייצור אוטומטי:** ניתן לייצר מחדש את הדיאגרמות באמצעות:
```bash
python scripts/generate_state_diagrams.py
```

---

## מצבי שולח (SenderState)

```mermaid
stateDiagram-v2
    INITIAL : התחלה
    SENDER_DELIVERY_CONFIRM : אישור משלוח
    SENDER_DELIVERY_DESCRIPTION : תיאור משלוח
    SENDER_DELIVERY_DROPOFF_APARTMENT : דירה יעד
    SENDER_DELIVERY_DROPOFF_CITY : עיר יעד
    SENDER_DELIVERY_DROPOFF_NUMBER : מספר בית יעד
    SENDER_DELIVERY_DROPOFF_STREET : רחוב יעד
    SENDER_DELIVERY_LOCATION : סוג משלוח
    SENDER_DELIVERY_PICKUP_APARTMENT : דירה איסוף
    SENDER_DELIVERY_PICKUP_CITY : עיר איסוף
    SENDER_DELIVERY_PICKUP_NUMBER : מספר בית איסוף
    SENDER_DELIVERY_PICKUP_STREET : רחוב איסוף
    SENDER_DELIVERY_PRICE : מחיר
    SENDER_DELIVERY_TIME : בחירת שעה
    SENDER_DELIVERY_URGENCY : דחיפות
    SENDER_MENU : תפריט ראשי
    SENDER_NEW : משתמש חדש
    SENDER_REGISTER_COLLECT_NAME : איסוף שם
    SENDER_REGISTER_COLLECT_PHONE : איסוף טלפון
    SENDER_VIEW_DELIVERIES : צפייה במשלוחים

    [*] --> INITIAL
    [*] --> SENDER_NEW

    INITIAL --> SENDER_NEW
    INITIAL --> SENDER_REGISTER_COLLECT_NAME
    SENDER_NEW --> SENDER_REGISTER_COLLECT_NAME
    SENDER_REGISTER_COLLECT_NAME --> SENDER_REGISTER_COLLECT_PHONE
    SENDER_REGISTER_COLLECT_NAME --> SENDER_MENU
    SENDER_REGISTER_COLLECT_PHONE --> SENDER_MENU
    SENDER_MENU --> SENDER_DELIVERY_PICKUP_CITY
    SENDER_MENU --> SENDER_VIEW_DELIVERIES
    SENDER_DELIVERY_PICKUP_CITY --> SENDER_DELIVERY_PICKUP_STREET
    SENDER_DELIVERY_PICKUP_STREET --> SENDER_DELIVERY_PICKUP_NUMBER
    SENDER_DELIVERY_PICKUP_NUMBER --> SENDER_DELIVERY_PICKUP_APARTMENT
    SENDER_DELIVERY_PICKUP_APARTMENT --> SENDER_DELIVERY_LOCATION
    SENDER_DELIVERY_LOCATION --> SENDER_DELIVERY_DROPOFF_CITY
    SENDER_DELIVERY_DROPOFF_CITY --> SENDER_DELIVERY_DROPOFF_STREET
    SENDER_DELIVERY_DROPOFF_STREET --> SENDER_DELIVERY_DROPOFF_NUMBER
    SENDER_DELIVERY_DROPOFF_NUMBER --> SENDER_DELIVERY_DROPOFF_APARTMENT
    SENDER_DELIVERY_DROPOFF_APARTMENT --> SENDER_DELIVERY_URGENCY
    SENDER_DELIVERY_DROPOFF_APARTMENT --> SENDER_MENU
    SENDER_DELIVERY_URGENCY --> SENDER_DELIVERY_TIME
    SENDER_DELIVERY_URGENCY --> SENDER_DELIVERY_DESCRIPTION
    SENDER_DELIVERY_TIME --> SENDER_DELIVERY_PRICE
    SENDER_DELIVERY_PRICE --> SENDER_DELIVERY_DESCRIPTION
    SENDER_DELIVERY_DESCRIPTION --> SENDER_DELIVERY_CONFIRM
    SENDER_DELIVERY_CONFIRM --> SENDER_MENU
    SENDER_VIEW_DELIVERIES --> SENDER_MENU
```

### תיאור מצבי שולח

| מצב | תיאור | פעולה הבאה |
|-----|-------|-----------|
| `INITIAL` | מצב התחלתי — הודעה ראשונה | מעבר ל-NEW או רישום |
| `SENDER.NEW` | משתמש חדש שזוהה | איסוף שם |
| `SENDER.REGISTER.COLLECT_NAME` | איסוף שם משתמש | איסוף טלפון או תפריט |
| `SENDER.REGISTER.COLLECT_PHONE` | איסוף מספר טלפון | מעבר לתפריט |
| `SENDER.MENU` | תפריט ראשי | משלוח חדש / צפייה במשלוחים |
| `SENDER.DELIVERY.PICKUP_CITY` | עיר איסוף | רחוב איסוף |
| `SENDER.DELIVERY.PICKUP_STREET` | רחוב איסוף | מספר בית |
| `SENDER.DELIVERY.PICKUP_NUMBER` | מספר בית איסוף | דירה |
| `SENDER.DELIVERY.PICKUP_APARTMENT` | דירה (אופציונלי) | סוג משלוח |
| `SENDER.DELIVERY.LOCATION` | בתוך/מחוץ לעיר | עיר יעד |
| `SENDER.DELIVERY.DROPOFF_CITY` | עיר יעד | רחוב יעד |
| `SENDER.DELIVERY.DROPOFF_STREET` | רחוב יעד | מספר בית |
| `SENDER.DELIVERY.DROPOFF_NUMBER` | מספר בית יעד | דירה |
| `SENDER.DELIVERY.DROPOFF_APARTMENT` | דירה יעד | דחיפות / חזרה לתפריט |
| `SENDER.DELIVERY.URGENCY` | מיידי / מאוחר יותר | שעה או תיאור |
| `SENDER.DELIVERY.TIME` | בחירת שעה (רק עבור "מאוחר") | מחיר |
| `SENDER.DELIVERY.PRICE` | מחיר ללקוח (רק עבור "מאוחר") | תיאור |
| `SENDER.DELIVERY.DESCRIPTION` | תיאור המשלוח | אישור |
| `SENDER.DELIVERY.CONFIRM` | אישור ושליחה | חזרה לתפריט |
| `SENDER.VIEW_DELIVERIES` | צפייה בהיסטוריית משלוחים | חזרה לתפריט |

---

## מצבי שליח (CourierState)

```mermaid
stateDiagram-v2
    COURIER_CAPTURE_CONFIRM : אישור תפיסה
    COURIER_CHANGE_AREA : שינוי אזור
    COURIER_DEPOSIT_REQUEST : בקשת הפקדה
    COURIER_DEPOSIT_UPLOAD : העלאת אישור
    COURIER_INITIAL : התחלה
    COURIER_MARK_DELIVERED : סימון מסירה
    COURIER_MARK_PICKED_UP : סימון איסוף
    COURIER_MENU : תפריט ראשי
    COURIER_NEW : שליח חדש
    COURIER_PENDING_APPROVAL : ממתין לאישור
    COURIER_REGISTER_COLLECT_DOCUMENT : העלאת תעודה
    COURIER_REGISTER_COLLECT_NAME : איסוף שם
    COURIER_REGISTER_COLLECT_SELFIE : צילום סלפי
    COURIER_REGISTER_COLLECT_VEHICLE_CATEGORY : סוג רכב
    COURIER_REGISTER_COLLECT_VEHICLE_PHOTO : צילום רכב
    COURIER_REGISTER_TERMS : אישור תנאים
    COURIER_SUPPORT : תמיכה
    COURIER_VIEW_ACTIVE : משלוחים פעילים
    COURIER_VIEW_AVAILABLE : משלוחים זמינים
    COURIER_VIEW_HISTORY : היסטוריה
    COURIER_VIEW_WALLET : ארנק

    [*] --> COURIER_INITIAL
    [*] --> COURIER_NEW

    COURIER_INITIAL --> COURIER_REGISTER_COLLECT_NAME
    COURIER_NEW --> COURIER_REGISTER_COLLECT_NAME
    COURIER_REGISTER_COLLECT_NAME --> COURIER_REGISTER_COLLECT_DOCUMENT
    COURIER_REGISTER_COLLECT_DOCUMENT --> COURIER_REGISTER_COLLECT_SELFIE
    COURIER_REGISTER_COLLECT_SELFIE --> COURIER_REGISTER_COLLECT_VEHICLE_CATEGORY
    COURIER_REGISTER_COLLECT_VEHICLE_CATEGORY --> COURIER_REGISTER_COLLECT_VEHICLE_PHOTO
    COURIER_REGISTER_COLLECT_VEHICLE_PHOTO --> COURIER_REGISTER_TERMS
    COURIER_REGISTER_TERMS --> COURIER_PENDING_APPROVAL
    COURIER_PENDING_APPROVAL --> COURIER_MENU
    COURIER_MENU --> COURIER_VIEW_AVAILABLE
    COURIER_MENU --> COURIER_VIEW_ACTIVE
    COURIER_MENU --> COURIER_VIEW_WALLET
    COURIER_MENU --> COURIER_CHANGE_AREA
    COURIER_MENU --> COURIER_VIEW_HISTORY
    COURIER_MENU --> COURIER_SUPPORT
    COURIER_MENU --> COURIER_DEPOSIT_REQUEST
    COURIER_VIEW_AVAILABLE --> COURIER_CAPTURE_CONFIRM
    COURIER_VIEW_AVAILABLE --> COURIER_MENU
    COURIER_CAPTURE_CONFIRM --> COURIER_VIEW_ACTIVE
    COURIER_CAPTURE_CONFIRM --> COURIER_MENU
    COURIER_VIEW_ACTIVE --> COURIER_MARK_PICKED_UP
    COURIER_VIEW_ACTIVE --> COURIER_MENU
    COURIER_MARK_PICKED_UP --> COURIER_MARK_DELIVERED
    COURIER_MARK_PICKED_UP --> COURIER_VIEW_ACTIVE
    COURIER_MARK_DELIVERED --> COURIER_MENU
    COURIER_VIEW_WALLET --> COURIER_DEPOSIT_REQUEST
    COURIER_VIEW_WALLET --> COURIER_MENU
    COURIER_DEPOSIT_REQUEST --> COURIER_DEPOSIT_UPLOAD
    COURIER_DEPOSIT_REQUEST --> COURIER_MENU
    COURIER_DEPOSIT_UPLOAD --> COURIER_VIEW_WALLET
    COURIER_DEPOSIT_UPLOAD --> COURIER_MENU
    COURIER_CHANGE_AREA --> COURIER_MENU
    COURIER_VIEW_HISTORY --> COURIER_MENU
    COURIER_SUPPORT --> COURIER_MENU
```

### תיאור מצבי שליח

| מצב | תיאור | פעולה הבאה |
|-----|-------|-----------|
| `COURIER.INITIAL` | מצב התחלתי | רישום |
| `COURIER.NEW` | שליח חדש | איסוף שם |
| `COURIER.REGISTER.COLLECT_NAME` | איסוף שם מלא | העלאת תעודה |
| `COURIER.REGISTER.COLLECT_DOCUMENT` | העלאת צילום תעודת זהות | סלפי |
| `COURIER.REGISTER.COLLECT_SELFIE` | צילום סלפי לאימות | סוג רכב |
| `COURIER.REGISTER.COLLECT_VEHICLE_CATEGORY` | בחירת סוג רכב | צילום רכב |
| `COURIER.REGISTER.COLLECT_VEHICLE_PHOTO` | צילום רכב | תנאים |
| `COURIER.REGISTER.TERMS` | אישור תנאי שימוש | ממתין לאישור |
| `COURIER.PENDING_APPROVAL` | ממתין לאישור אדמין | תפריט (לאחר אישור) |
| `COURIER.MENU` | תפריט ראשי | בחירת פעולה |
| `COURIER.VIEW_AVAILABLE` | צפייה במשלוחים זמינים | תפיסה / חזרה |
| `COURIER.CAPTURE_CONFIRM` | אישור תפיסת משלוח | משלוחים פעילים / חזרה |
| `COURIER.VIEW_ACTIVE` | צפייה במשלוחים פעילים | סימון איסוף / חזרה |
| `COURIER.MARK_PICKED_UP` | סימון שנאסף | סימון מסירה / חזרה |
| `COURIER.MARK_DELIVERED` | סימון שנמסר | חזרה לתפריט |
| `COURIER.VIEW_WALLET` | צפייה בארנק | הפקדה / חזרה |
| `COURIER.DEPOSIT_REQUEST` | בקשת הפקדה | העלאת אישור / חזרה |
| `COURIER.DEPOSIT_UPLOAD` | העלאת אישור הפקדה | ארנק / חזרה |
| `COURIER.CHANGE_AREA` | שינוי אזור פעילות | חזרה לתפריט |
| `COURIER.VIEW_HISTORY` | צפייה בהיסטוריה | חזרה לתפריט |
| `COURIER.SUPPORT` | פנייה לתמיכה | חזרה לתפריט |

---

## מצבי סדרן (DispatcherState)

```mermaid
stateDiagram-v2
    DISPATCHER_ADD_SHIPMENT_CONFIRM : אישור משלוח
    DISPATCHER_ADD_SHIPMENT_DESCRIPTION : תיאור משלוח
    DISPATCHER_ADD_SHIPMENT_DROPOFF_CITY : עיר יעד
    DISPATCHER_ADD_SHIPMENT_DROPOFF_NUMBER : מספר בית יעד
    DISPATCHER_ADD_SHIPMENT_DROPOFF_STREET : רחוב יעד
    DISPATCHER_ADD_SHIPMENT_FEE : עמלה
    DISPATCHER_ADD_SHIPMENT_PICKUP_CITY : עיר איסוף
    DISPATCHER_ADD_SHIPMENT_PICKUP_NUMBER : מספר בית איסוף
    DISPATCHER_ADD_SHIPMENT_PICKUP_STREET : רחוב איסוף
    DISPATCHER_MANUAL_CHARGE_AMOUNT : סכום חיוב
    DISPATCHER_MANUAL_CHARGE_CONFIRM : אישור חיוב
    DISPATCHER_MANUAL_CHARGE_DESCRIPTION : תיאור חיוב
    DISPATCHER_MANUAL_CHARGE_DRIVER_NAME : שם נהג
    DISPATCHER_MENU : תפריט סדרן
    DISPATCHER_VIEW_ACTIVE_SHIPMENTS : משלוחים פעילים
    DISPATCHER_VIEW_SHIPMENT_HISTORY : היסטוריית משלוחים

    DISPATCHER_MENU --> DISPATCHER_ADD_SHIPMENT_PICKUP_CITY
    DISPATCHER_MENU --> DISPATCHER_VIEW_ACTIVE_SHIPMENTS
    DISPATCHER_MENU --> DISPATCHER_VIEW_SHIPMENT_HISTORY
    DISPATCHER_MENU --> DISPATCHER_MANUAL_CHARGE_DRIVER_NAME
    DISPATCHER_ADD_SHIPMENT_PICKUP_CITY --> DISPATCHER_ADD_SHIPMENT_PICKUP_STREET
    DISPATCHER_ADD_SHIPMENT_PICKUP_STREET --> DISPATCHER_ADD_SHIPMENT_PICKUP_NUMBER
    DISPATCHER_ADD_SHIPMENT_PICKUP_NUMBER --> DISPATCHER_ADD_SHIPMENT_DROPOFF_CITY
    DISPATCHER_ADD_SHIPMENT_DROPOFF_CITY --> DISPATCHER_ADD_SHIPMENT_DROPOFF_STREET
    DISPATCHER_ADD_SHIPMENT_DROPOFF_STREET --> DISPATCHER_ADD_SHIPMENT_DROPOFF_NUMBER
    DISPATCHER_ADD_SHIPMENT_DROPOFF_NUMBER --> DISPATCHER_ADD_SHIPMENT_DESCRIPTION
    DISPATCHER_ADD_SHIPMENT_DESCRIPTION --> DISPATCHER_ADD_SHIPMENT_FEE
    DISPATCHER_ADD_SHIPMENT_FEE --> DISPATCHER_ADD_SHIPMENT_CONFIRM
    DISPATCHER_ADD_SHIPMENT_CONFIRM --> DISPATCHER_MENU
    DISPATCHER_VIEW_ACTIVE_SHIPMENTS --> DISPATCHER_MENU
    DISPATCHER_VIEW_SHIPMENT_HISTORY --> DISPATCHER_MENU
    DISPATCHER_MANUAL_CHARGE_DRIVER_NAME --> DISPATCHER_MANUAL_CHARGE_AMOUNT
    DISPATCHER_MANUAL_CHARGE_DRIVER_NAME --> DISPATCHER_MENU
    DISPATCHER_MANUAL_CHARGE_AMOUNT --> DISPATCHER_MANUAL_CHARGE_DESCRIPTION
    DISPATCHER_MANUAL_CHARGE_AMOUNT --> DISPATCHER_MENU
    DISPATCHER_MANUAL_CHARGE_DESCRIPTION --> DISPATCHER_MANUAL_CHARGE_CONFIRM
    DISPATCHER_MANUAL_CHARGE_DESCRIPTION --> DISPATCHER_MENU
    DISPATCHER_MANUAL_CHARGE_CONFIRM --> DISPATCHER_MENU
```

### תיאור מצבי סדרן

> סדרן הוא שליח מאושר עם הרשאות ניהול ברמת תחנה. הוא רואה את תפריט השליח + תפריט סדרן ייעודי.

| מצב | תיאור | פעולה הבאה |
|-----|-------|-----------|
| `DISPATCHER.MENU` | תפריט סדרן ראשי | הוספת משלוח / צפייה / חיוב ידני |
| `DISPATCHER.ADD_SHIPMENT.PICKUP_CITY` | עיר איסוף | רחוב איסוף |
| `DISPATCHER.ADD_SHIPMENT.PICKUP_STREET` | רחוב איסוף | מספר בית |
| `DISPATCHER.ADD_SHIPMENT.PICKUP_NUMBER` | מספר בית | עיר יעד |
| `DISPATCHER.ADD_SHIPMENT.DROPOFF_CITY` | עיר יעד | רחוב יעד |
| `DISPATCHER.ADD_SHIPMENT.DROPOFF_STREET` | רחוב יעד | מספר בית יעד |
| `DISPATCHER.ADD_SHIPMENT.DROPOFF_NUMBER` | מספר בית יעד | תיאור |
| `DISPATCHER.ADD_SHIPMENT.DESCRIPTION` | תיאור המשלוח | עמלה |
| `DISPATCHER.ADD_SHIPMENT.FEE` | עמלת שליח | אישור |
| `DISPATCHER.ADD_SHIPMENT.CONFIRM` | אישור ושליחה | חזרה לתפריט |
| `DISPATCHER.VIEW_ACTIVE_SHIPMENTS` | משלוחים פעילים | חזרה לתפריט |
| `DISPATCHER.VIEW_SHIPMENT_HISTORY` | היסטוריית משלוחים | חזרה לתפריט |
| `DISPATCHER.MANUAL_CHARGE.DRIVER_NAME` | שם נהג לחיוב | סכום / חזרה |
| `DISPATCHER.MANUAL_CHARGE.AMOUNT` | סכום חיוב | תיאור / חזרה |
| `DISPATCHER.MANUAL_CHARGE.DESCRIPTION` | תיאור החיוב | אישור / חזרה |
| `DISPATCHER.MANUAL_CHARGE.CONFIRM` | אישור חיוב | חזרה לתפריט |

---

## מצבי בעל תחנה (StationOwnerState)

```mermaid
stateDiagram-v2
    STATION_ADD_BLACKLIST_PHONE : טלפון לחסימה
    STATION_ADD_BLACKLIST_REASON : סיבת חסימה
    STATION_ADD_DISPATCHER_PHONE : טלפון סדרן חדש
    STATION_COLLECTION_REPORT : דוח גבייה
    STATION_GROUP_SETTINGS : הגדרות קבוצות
    STATION_MANAGE_DISPATCHERS : ניהול סדרנים
    STATION_MENU : תפריט תחנה
    STATION_REMOVE_BLACKLIST_SELECT : הסרה מרשימה שחורה
    STATION_REMOVE_DISPATCHER_SELECT : בחירת סדרן להסרה
    STATION_SET_PRIVATE_GROUP : קבוצה פרטית
    STATION_SET_PUBLIC_GROUP : קבוצה ציבורית
    STATION_VIEW_BLACKLIST : רשימה שחורה
    STATION_VIEW_WALLET : ארנק תחנה

    STATION_MENU --> STATION_MANAGE_DISPATCHERS
    STATION_MENU --> STATION_VIEW_WALLET
    STATION_MENU --> STATION_COLLECTION_REPORT
    STATION_MENU --> STATION_VIEW_BLACKLIST
    STATION_MENU --> STATION_GROUP_SETTINGS
    STATION_MANAGE_DISPATCHERS --> STATION_ADD_DISPATCHER_PHONE
    STATION_MANAGE_DISPATCHERS --> STATION_REMOVE_DISPATCHER_SELECT
    STATION_MANAGE_DISPATCHERS --> STATION_MENU
    STATION_ADD_DISPATCHER_PHONE --> STATION_MANAGE_DISPATCHERS
    STATION_ADD_DISPATCHER_PHONE --> STATION_MENU
    STATION_REMOVE_DISPATCHER_SELECT --> STATION_MANAGE_DISPATCHERS
    STATION_REMOVE_DISPATCHER_SELECT --> STATION_MENU
    STATION_VIEW_WALLET --> STATION_MENU
    STATION_COLLECTION_REPORT --> STATION_MENU
    STATION_VIEW_BLACKLIST --> STATION_ADD_BLACKLIST_PHONE
    STATION_VIEW_BLACKLIST --> STATION_REMOVE_BLACKLIST_SELECT
    STATION_VIEW_BLACKLIST --> STATION_MENU
    STATION_ADD_BLACKLIST_PHONE --> STATION_ADD_BLACKLIST_REASON
    STATION_ADD_BLACKLIST_PHONE --> STATION_VIEW_BLACKLIST
    STATION_ADD_BLACKLIST_REASON --> STATION_VIEW_BLACKLIST
    STATION_ADD_BLACKLIST_REASON --> STATION_MENU
    STATION_REMOVE_BLACKLIST_SELECT --> STATION_VIEW_BLACKLIST
    STATION_REMOVE_BLACKLIST_SELECT --> STATION_MENU
    STATION_GROUP_SETTINGS --> STATION_SET_PUBLIC_GROUP
    STATION_GROUP_SETTINGS --> STATION_SET_PRIVATE_GROUP
    STATION_GROUP_SETTINGS --> STATION_MENU
    STATION_SET_PUBLIC_GROUP --> STATION_GROUP_SETTINGS
    STATION_SET_PUBLIC_GROUP --> STATION_MENU
    STATION_SET_PRIVATE_GROUP --> STATION_GROUP_SETTINGS
    STATION_SET_PRIVATE_GROUP --> STATION_MENU
```

### תיאור מצבי בעל תחנה

| מצב | תיאור | פעולה הבאה |
|-----|-------|-----------|
| `STATION.MENU` | תפריט תחנה ראשי | ניהול סדרנים / ארנק / דוח / רשימה שחורה / הגדרות |
| `STATION.MANAGE_DISPATCHERS` | ניהול סדרנים | הוספה / הסרה / חזרה |
| `STATION.ADD_DISPATCHER.PHONE` | הזנת טלפון סדרן חדש | ניהול סדרנים / חזרה |
| `STATION.REMOVE_DISPATCHER.SELECT` | בחירת סדרן להסרה | ניהול סדרנים / חזרה |
| `STATION.VIEW_WALLET` | ארנק תחנה | חזרה לתפריט |
| `STATION.COLLECTION_REPORT` | דוח גבייה | חזרה לתפריט |
| `STATION.VIEW_BLACKLIST` | רשימה שחורה | הוספה / הסרה / חזרה |
| `STATION.ADD_BLACKLIST.PHONE` | טלפון לחסימה | סיבה / חזרה |
| `STATION.ADD_BLACKLIST.REASON` | סיבת חסימה | רשימה שחורה / חזרה |
| `STATION.REMOVE_BLACKLIST.SELECT` | הסרה מרשימה שחורה | רשימה שחורה / חזרה |
| `STATION.GROUP_SETTINGS` | הגדרות קבוצות | ציבורית / פרטית / חזרה |
| `STATION.SET_PUBLIC_GROUP` | הגדרת קבוצה ציבורית | הגדרות / חזרה |
| `STATION.SET_PRIVATE_GROUP` | הגדרת קבוצה פרטית | הגדרות / חזרה |

---

## סטטוס משלוח (DeliveryStatus)

```mermaid
stateDiagram-v2
    open : פתוח
    pending_approval : ממתין לאישור סדרן
    captured : נתפס
    in_progress : בדרך
    delivered : נמסר
    cancelled : בוטל

    [*] --> open
    open --> pending_approval : שיוך לתחנה
    open --> captured : תפיסה ישירה
    open --> cancelled : ביטול
    pending_approval --> captured : סדרן אישר
    pending_approval --> cancelled : סדרן דחה
    captured --> in_progress : שליח אסף
    in_progress --> delivered : שליח מסר
    delivered --> [*]
    cancelled --> [*]
```

---

## סטטוס אישור שליח (ApprovalStatus)

```mermaid
stateDiagram-v2
    pending : ממתין לאישור
    approved : מאושר
    rejected : נדחה
    blocked : חסום

    [*] --> pending : השלמת רישום KYC
    pending --> approved : אדמין אישר
    pending --> rejected : אדמין דחה (עם הערת דחייה)
    approved --> blocked : חסימת שליח
    rejected --> pending : הגשה מחדש
```

---

## מעברי מצב (Transitions)

### מעבר תקין
```python
current_state = "SENDER.DELIVERY.PICKUP_CITY"
message = "תל אביב"

# שמירה ב-context
context["pickup_city"] = message

# מעבר למצב הבא
new_state = "SENDER.DELIVERY.PICKUP_STREET"
```

### Context (הקשר)
כל session שומר context עם נתונים זמניים:

```json
{
  "name": "ישראל ישראלי",
  "pickup_city": "תל אביב",
  "pickup_street": "דיזנגוף",
  "pickup_number": "50",
  "pickup_apartment": null,
  "dropoff_city": null,
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
    NEW_STATE = "SENDER.NEW_STATE"
```

2. הוספה למילון מעברים:
```python
SENDER_TRANSITIONS = {
    ...
    SenderState.PREVIOUS_STATE: [SenderState.NEW_STATE],
    SenderState.NEW_STATE: [SenderState.MENU],
}
```

3. הוספת handler ב-`handlers.py`:
```python
async def _handle_new_state(self, message, context):
    # לוגיקה
    return response, next_state, context_update
```

4. רישום ב-mapping:
```python
handlers = {
    SenderState.NEW_STATE.value: self._handle_new_state,
}
```

5. עדכון הדיאגרמות:
```bash
python scripts/generate_state_diagrams.py --update-claude-md
```
