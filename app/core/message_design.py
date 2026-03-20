"""
עיצוב הודעות בוט — כרטיסי מידע עם מסגרות ותבניות עץ.

מודול זה מספק פונקציות עזר לבניית הודעות מעוצבות
בסגנון כרטיסים עם תווי מסגרת (box-drawing characters)
ותבניות עץ (├ └ ─) ליצירת היררכיה ויזואלית.

הודעות נשלחות עם parse_mode=HTML, ולכן תווי המסגרת
מוצגים כ-plain text ומאפשרים עיצוב עשיר בטלגרם ובוואטסאפ.
"""

from html import escape


# ==================== תווי מסגרת ====================

# אורך ברירת מחדל לקו מסגרת
_LINE_LEN = 26
_LINE_LEN_WIDE = 30


def _line(length: int = _LINE_LEN) -> str:
    """קו מפריד ══════"""
    return "═" * length


def card_header(emoji: str, title: str, subtitle: str = "", wide: bool = False) -> str:
    """
    כותרת כרטיס עם מסגרת.

    >>> card_header("🚚", "משלוח חדש", "#S-00891")
    ╔══════════════════════════╗
    🚚 משלוח חדש | #S-00891
    ╚══════════════════════════╝
    """
    length = _LINE_LEN_WIDE if wide else _LINE_LEN
    line = _line(length)
    title_part = f"{emoji} {title}"
    if subtitle:
        title_part += f" | {subtitle}"
    return f"╔{line}╗\n{title_part}\n╚{line}╝"


def section_header(emoji: str, title: str, wide: bool = False) -> str:
    """
    כותרת סקציה עם קו מפריד.

    >>> section_header("📊", "סטטוס תחנה — עכשיו")
    ══════════════════════════
    📊 סטטוס תחנה — עכשיו
    ══════════════════════════
    """
    length = _LINE_LEN_WIDE if wide else _LINE_LEN
    line = _line(length)
    return f"{line}\n{emoji} {title}\n{line}"


def separator(wide: bool = False) -> str:
    """קו מפריד פשוט ══════"""
    length = _LINE_LEN_WIDE if wide else _LINE_LEN
    return _line(length)


# ==================== שדות עץ ====================


def tree_field(label: str, value: str, last: bool = False) -> str:
    """
    שדה בתבנית עץ.

    last=False: ├ שם: אמיר חיים
    last=True:  └ אזור: תל אביב
    """
    branch = "└" if last else "├"
    safe_value = escape(str(value))
    return f"{branch} {label}: {safe_value}"


def tree_field_icon(icon: str, label: str, value: str, last: bool = False) -> str:
    """
    שדה בתבנית עץ עם אייקון.

    last=False: ├ 📌 איסוף: בלקינד 1, ת״א
    last=True:  └ 🎯 יעד: בלקינד 2, ת״א
    """
    branch = "└" if last else "├"
    safe_value = escape(str(value))
    return f"{branch} {icon} {label}: {safe_value}"


def tree_fields(fields: list[tuple[str, str]]) -> str:
    """
    רשימת שדות בתבנית עץ (האחרון מקבל └ אוטומטית).

    >>> tree_fields([("שם", "אמיר"), ("גיל", "39"), ("אזור", "ת״א")])
    ├ שם: אמיר
    ├ גיל: 39
    └ אזור: ת״א
    """
    if not fields:
        return ""
    lines = []
    for i, (label, value) in enumerate(fields):
        is_last = i == len(fields) - 1
        lines.append(tree_field(label, value, last=is_last))
    return "\n".join(lines)


def tree_fields_icon(fields: list[tuple[str, str, str]]) -> str:
    """
    רשימת שדות בתבנית עץ עם אייקונים (icon, label, value).

    >>> tree_fields_icon([("📌", "איסוף", "ת״א"), ("🎯", "יעד", "חיפה")])
    ├ 📌 איסוף: ת״א
    └ 🎯 יעד: חיפה
    """
    if not fields:
        return ""
    lines = []
    for i, (icon, label, value) in enumerate(fields):
        is_last = i == len(fields) - 1
        lines.append(tree_field_icon(icon, label, value, last=is_last))
    return "\n".join(lines)


# ==================== כרטיסי מידע מוכנים ====================


def shipment_card(
    pickup: str,
    dropoff: str,
    fee: float | str,
    token: str = "",
    capture_instruction: str = "",
    description: str = "",
    delivery_time: str = "",
    location_type: str = "",
    customer_price: str = "",
) -> str:
    """
    כרטיס משלוח — להצגה לשליחים וסדרנים.

    ╔══════════════════════════╗
    🚚 משלוח חדש | #S-00891
    ╚══════════════════════════╝

    📍 מסלול
    ├ 📌 איסוף: ...
    └ 🎯 יעד: ...

    💰 עמלה: 20 ₪
    ══════════════════════════
    לתפיסה הקש /capture S-00891
    ══════════════════════════
    """
    subtitle = f"#{token}" if token else ""
    lines = [card_header("🚚", "משלוח חדש", subtitle)]
    lines.append("")

    # מסלול
    route_fields = [("📌", "איסוף", pickup), ("🎯", "יעד", dropoff)]
    lines.append("📍 מסלול")
    lines.append(tree_fields_icon(route_fields))

    # פרטים נוספים
    lines.append("")
    if location_type:
        lines.append(f"🗺️ סוג: {escape(location_type)}")
    if delivery_time:
        lines.append(f"⏰ זמן: {escape(str(delivery_time))}")
    if description:
        lines.append(f"📦 תיאור: {escape(description)}")
    if customer_price:
        lines.append(f"💰 מחיר מוצע: {escape(str(customer_price))} ₪")
    if fee:
        lines.append(f"💰 עמלה: {escape(str(fee))} ₪")

    if capture_instruction:
        lines.append(separator())
        lines.append(capture_instruction)
        lines.append(separator())

    return "\n".join(lines)


def shipment_summary_card(
    pickup: str,
    dropoff: str,
    description: str = "",
    delivery_time: str = "",
    location_type: str = "",
    customer_price: str = "",
    fee: str = "",
) -> str:
    """
    כרטיס סיכום משלוח — לפני אישור שליחה.

    ╔══════════════════════════╗
    📋 סיכום המשלוח
    ╚══════════════════════════╝

    📍 מסלול
    ├ 📌 איסוף: ...
    └ 🎯 יעד: ...

    ...
    לאשר את המשלוח?
    """
    lines = [card_header("📋", "סיכום המשלוח")]
    lines.append("")

    route_fields = [("📌", "איסוף", pickup), ("🎯", "יעד", dropoff)]
    lines.append("📍 מסלול")
    lines.append(tree_fields_icon(route_fields))
    lines.append("")

    if location_type:
        lines.append(f"🗺️ סוג: {escape(location_type)}")
    if delivery_time:
        lines.append(f"⏰ זמן: {escape(str(delivery_time))}")
    if description:
        lines.append(f"📦 תיאור: {escape(description)}")
    if customer_price:
        lines.append(f"💰 מחיר מוצע: {escape(str(customer_price))} ₪")
    if fee:
        lines.append(f"💰 מחיר: {escape(str(fee))} ₪")

    lines.append("")
    lines.append("לאשר את המשלוח?")

    return "\n".join(lines)


def courier_menu_card(
    name: str,
    balance: str = "0.00",
    service_area: str = "לא הוגדר",
) -> str:
    """
    כרטיס תפריט שליח.

    ╔══════════════════════════╗
    📋 תפריט שליח
    ╚══════════════════════════╝

    שלום אמיר! 👋

    💰 מצב הארנק: 0.00 ₪
    📍 האזור שלך: ...

    בחר פעולה:
    """
    lines = [card_header("📋", "תפריט שליח")]
    lines.append("")
    lines.append(f"שלום {escape(name)}! 👋")
    lines.append("")
    lines.append(f"💰 <b>מצב הארנק:</b> {escape(balance)} ₪")
    lines.append(f"📍 <b>האזור שלך:</b> {escape(service_area)}")
    lines.append("")
    lines.append("בחר פעולה:")

    return "\n".join(lines)


def courier_wallet_card(
    balance: str = "0.00",
    credit_limit: str = "0.00",
    remaining: str = "0.00",
) -> str:
    """
    כרטיס ארנק שליח.

    ╔══════════════════════════╗
    💰 פרטי הארנק
    ╚══════════════════════════╝

    🟢 סטטוס: פעיל

    💵 יתרה נוכחית: ...
    📊 מסגרת אשראי: ...
    🎯 נותר עד לחסימה: ...

    לטעינת הארנק, לחץ על 'הפקדה'.
    """
    lines = [card_header("💰", "פרטי הארנק")]
    lines.append("")
    lines.append("🟢 סטטוס: פעיל")
    lines.append("")
    lines.append(f"💵 יתרה נוכחית: <b>{escape(balance)} ₪</b>")
    lines.append(f"📊 מסגרת אשראי: {escape(credit_limit)} ₪")
    lines.append(f"🎯 נותר עד לחסימה: {escape(remaining)} ₪")
    lines.append("")
    lines.append("לטעינת הארנק, לחץ על 'הפקדה'.")

    return "\n".join(lines)


def station_panel_card(
    station_name: str,
    balance: str = "0.00",
) -> str:
    """
    כרטיס פאנל ניהול תחנה.

    ╔══════════════════════════════╗
    🏢 פאנל ניהול | תחנה מרכזית
    ╚══════════════════════════════╝

    💰 יתרת ארנק: 240.00 ₪

    בחר פעולה:
    """
    lines = [card_header("🏢", "פאנל ניהול", escape(station_name), wide=True)]
    lines.append("")
    lines.append(f"💰 יתרת ארנק: {escape(balance)} ₪")
    lines.append("")
    lines.append("בחר פעולה:")

    return "\n".join(lines)


def station_wallet_card(
    balance: str = "0.00",
    commission_rate: str = "0",
    ledger_lines: list[str] | None = None,
) -> str:
    """
    כרטיס ארנק תחנה.

    ╔══════════════════════════════╗
    💰 ארנק תחנה
    ╚══════════════════════════════╝

    💵 יתרה: 240.00 ₪
    📊 שיעור עמלה: 10%

    ══════════════════════════════
    📋 תנועות אחרונות
    ══════════════════════════════
    ├ +20.00 ₪ | עמלה ממשלוח
    └ -5.00 ₪ | חיוב ידני
    """
    lines = [card_header("💰", "ארנק תחנה", wide=True)]
    lines.append("")
    lines.append(f"💵 יתרה: <b>{escape(balance)} ₪</b>")
    lines.append(f"📊 שיעור עמלה: {escape(commission_rate)}%")
    lines.append("")

    if ledger_lines:
        lines.append(section_header("📋", "תנועות אחרונות", wide=True))
        for i, entry in enumerate(ledger_lines):
            is_last = i == len(ledger_lines) - 1
            branch = "└" if is_last else "├"
            lines.append(f"{branch} {entry}")
    else:
        lines.append("אין תנועות עדיין.")

    return "\n".join(lines)


def dispatcher_menu_card(station_name: str) -> str:
    """
    כרטיס תפריט סדרן.

    ╔══════════════════════════════╗
    🏪 תפריט סדרן | תחנה מרכזית
    ╚══════════════════════════════╝

    בחר פעולה:
    """
    lines = [card_header("🏪", "תפריט סדרן", escape(station_name), wide=True)]
    lines.append("")
    lines.append("בחר פעולה:")

    return "\n".join(lines)


def active_deliveries_card(
    deliveries_text: str,
    title: str = "משלוחים פעילים",
    emoji: str = "📦",
) -> str:
    """
    כרטיס רשימת משלוחים.

    ╔══════════════════════════╗
    📦 משלוחים פעילים
    ╚══════════════════════════╝

    #123 | 🟡 פתוח
    ├ 📌 איסוף: ...
    ├ 🎯 יעד: ...
    └ 💰 20 ₪
    """
    lines = [card_header(emoji, title)]
    lines.append("")
    lines.append(deliveries_text)

    return "\n".join(lines)


def delivery_list_item(
    delivery_id: int,
    status_text: str,
    pickup: str,
    dropoff: str,
    fee: float,
) -> str:
    """
    פריט בודד ברשימת משלוחים.

    #{id} | 🟡 פתוח
    ├ 📌 איסוף: ...
    ├ 🎯 יעד: ...
    └ 💰 20 ₪
    """
    lines = [
        f"#{delivery_id} | {status_text}",
        tree_field_icon("📌", "איסוף", pickup[:30]),
        tree_field_icon("🎯", "יעד", dropoff[:30]),
        tree_field_icon("💰", "", f"{fee:.0f} ₪", last=True),
    ]
    return "\n".join(lines)


def capture_notification_card(
    delivery_id: int,
    pickup: str,
    dropoff: str,
) -> str:
    """
    כרטיס התראת תפיסה.

    ╔══════════════════════════╗
    ✅ המשלוח נתפס!
    ╚══════════════════════════╝

    📍 מסלול
    ├ 📌 איסוף: ...
    └ 🎯 יעד: ...
    """
    lines = [card_header("✅", f"המשלוח #{delivery_id} נתפס!")]
    lines.append("")
    lines.append("📍 מסלול")
    route_fields = [("📌", "איסוף", pickup), ("🎯", "יעד", dropoff)]
    lines.append(tree_fields_icon(route_fields))

    return "\n".join(lines)


def success_delivery_card(
    pickup: str,
    dropoff: str,
    delivery_time: str = "",
    description: str = "",
    customer_price: str = "",
) -> str:
    """
    כרטיס משלוח שנוצר בהצלחה.

    ╔══════════════════════════╗
    🎉 המשלוח נוצר בהצלחה!
    ╚══════════════════════════╝

    📍 מסלול
    ├ 📌 מ: ...
    └ 🎯 אל: ...

    ...
    השליחים יקבלו התראה בקרוב.
    מה תרצו לעשות עכשיו?
    """
    lines = [card_header("🎉", "המשלוח נוצר בהצלחה!")]
    lines.append("")

    route_fields = [("📌", "מ", pickup), ("🎯", "אל", dropoff)]
    lines.append("📍 מסלול")
    lines.append(tree_fields_icon(route_fields))

    if delivery_time:
        lines.append(f"⏰ זמן: {escape(str(delivery_time))}")
    if description:
        lines.append(f"📦 תיאור: {escape(description)}")
    if customer_price:
        lines.append(f"💰 מחיר: {escape(str(customer_price))} ₪")

    lines.append("")
    lines.append("השליחים יקבלו התראה בקרוב.")
    lines.append("מה תרצו לעשות עכשיו?")

    return "\n".join(lines)


def driver_menu_card(
    greeting: str,
    name: str,
    subscription_line: str,
    vehicle_label: str,
    trip_label: str,
    deliveries_label: str,
    timeframe_label: str,
    future_label: str,
) -> str:
    """
    כרטיס תפריט נהג.

    ╔══════════════════════════╗
    👤 תפריט נהג
    ╚══════════════════════════╝

    ▪️ בוקר טוב אמיר ▪️
    📊 מנוי: פעיל

    ══════════════════════════
    ⚙️ הגדרות נוכחיות
    ══════════════════════════
    ├ 🚙 סוג רכב: ...
    ├ 🛣 סוג נסיעה: ...
    ├ 💌 הצגת משלוחים: ...
    ├ 🕐 עתידיות קרובות: ...
    └ 📅 חיפוש עתידי: ...

    📋 בחר אפשרות מהתפריט:
    """
    lines = [card_header("👤", "תפריט נהג")]
    lines.append("")
    lines.append(f"▪️ {escape(greeting)} {escape(name)} ▪️")
    lines.append(subscription_line)
    lines.append("")

    lines.append(section_header("⚙️", "הגדרות נוכחיות"))
    settings_fields = [
        ("🚙", "סוג רכב", vehicle_label),
        ("🛣", "סוג נסיעה", trip_label),
        ("💌", "הצגת משלוחים", deliveries_label),
        ("🕐", "עתידיות קרובות", timeframe_label),
        ("📅", "חיפוש עתידי", future_label),
    ]
    lines.append(tree_fields_icon(settings_fields))
    lines.append("")
    lines.append("📋 בחר אפשרות מהתפריט:")

    return "\n".join(lines)
