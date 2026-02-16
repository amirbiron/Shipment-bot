"""
ייצור דיאגרמות Mermaid מ-state machine enums ו-transitions.

שימוש:
    python scripts/generate_state_diagrams.py                     # הדפסה למסך
    python scripts/generate_state_diagrams.py --update-claude-md  # עדכון CLAUDE.md
    python scripts/generate_state_diagrams.py --check             # בדיקה שהדיאגרמות מסונכרנות (ל-CI)
"""
import argparse
import re
import sys
from pathlib import Path
from typing import Any

# הוספת root לנתיב כדי לאפשר ייבוא
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.state_machine.states import (
    SenderState,
    SENDER_TRANSITIONS,
    CourierState,
    COURIER_TRANSITIONS,
    DispatcherState,
    DISPATCHER_TRANSITIONS,
    StationOwnerState,
    STATION_OWNER_TRANSITIONS,
)

# תוויות עבריות לכל state - ממופות ידנית לקריאות
SENDER_LABELS: dict[str, str] = {
    SenderState.INITIAL.value: "התחלה",
    SenderState.NEW.value: "משתמש חדש",
    SenderState.REGISTER_COLLECT_NAME.value: "איסוף שם",
    SenderState.REGISTER_COLLECT_PHONE.value: "איסוף טלפון",
    SenderState.MENU.value: "תפריט ראשי",
    SenderState.PICKUP_CITY.value: "עיר איסוף",
    SenderState.PICKUP_STREET.value: "רחוב איסוף",
    SenderState.PICKUP_NUMBER.value: "מספר בית איסוף",
    SenderState.PICKUP_APARTMENT.value: "דירה איסוף",
    SenderState.DROPOFF_CITY.value: "עיר יעד",
    SenderState.DROPOFF_STREET.value: "רחוב יעד",
    SenderState.DROPOFF_NUMBER.value: "מספר בית יעד",
    SenderState.DROPOFF_APARTMENT.value: "דירה יעד",
    SenderState.DELIVERY_LOCATION.value: "סוג משלוח",
    SenderState.DELIVERY_URGENCY.value: "דחיפות",
    SenderState.DELIVERY_TIME.value: "בחירת שעה",
    SenderState.DELIVERY_PRICE.value: "מחיר",
    SenderState.DELIVERY_DESCRIPTION.value: "תיאור משלוח",
    SenderState.DELIVERY_CONFIRM.value: "אישור משלוח",
    SenderState.VIEW_DELIVERIES.value: "צפייה במשלוחים",
}

COURIER_LABELS: dict[str, str] = {
    CourierState.INITIAL.value: "התחלה",
    CourierState.NEW.value: "שליח חדש",
    CourierState.REGISTER_COLLECT_NAME.value: "איסוף שם",
    CourierState.REGISTER_COLLECT_DOCUMENT.value: "העלאת תעודה",
    CourierState.REGISTER_COLLECT_SELFIE.value: "צילום סלפי",
    CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value: "סוג רכב",
    CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value: "צילום רכב",
    CourierState.REGISTER_TERMS.value: "אישור תנאים",
    CourierState.PENDING_APPROVAL.value: "ממתין לאישור",
    CourierState.MENU.value: "תפריט ראשי",
    CourierState.VIEW_AVAILABLE.value: "משלוחים זמינים",
    CourierState.CAPTURE_CONFIRM.value: "אישור תפיסה",
    CourierState.VIEW_ACTIVE.value: "משלוחים פעילים",
    CourierState.MARK_PICKED_UP.value: "סימון איסוף",
    CourierState.MARK_DELIVERED.value: "סימון מסירה",
    CourierState.VIEW_WALLET.value: "ארנק",
    CourierState.DEPOSIT_REQUEST.value: "בקשת הפקדה",
    CourierState.DEPOSIT_UPLOAD.value: "העלאת אישור",
    CourierState.CHANGE_AREA.value: "שינוי אזור",
    CourierState.VIEW_HISTORY.value: "היסטוריה",
    CourierState.SUPPORT.value: "תמיכה",
}

DISPATCHER_LABELS: dict[str, str] = {
    DispatcherState.MENU.value: "תפריט סדרן",
    DispatcherState.ADD_SHIPMENT_PICKUP_CITY.value: "עיר איסוף",
    DispatcherState.ADD_SHIPMENT_PICKUP_STREET.value: "רחוב איסוף",
    DispatcherState.ADD_SHIPMENT_PICKUP_NUMBER.value: "מספר בית איסוף",
    DispatcherState.ADD_SHIPMENT_DROPOFF_CITY.value: "עיר יעד",
    DispatcherState.ADD_SHIPMENT_DROPOFF_STREET.value: "רחוב יעד",
    DispatcherState.ADD_SHIPMENT_DROPOFF_NUMBER.value: "מספר בית יעד",
    DispatcherState.ADD_SHIPMENT_DESCRIPTION.value: "תיאור משלוח",
    DispatcherState.ADD_SHIPMENT_FEE.value: "עמלה",
    DispatcherState.ADD_SHIPMENT_CONFIRM.value: "אישור משלוח",
    DispatcherState.VIEW_ACTIVE_SHIPMENTS.value: "משלוחים פעילים",
    DispatcherState.VIEW_SHIPMENT_HISTORY.value: "היסטוריית משלוחים",
    DispatcherState.MANUAL_CHARGE_DRIVER_NAME.value: "שם נהג",
    DispatcherState.MANUAL_CHARGE_AMOUNT.value: "סכום חיוב",
    DispatcherState.MANUAL_CHARGE_DESCRIPTION.value: "תיאור חיוב",
    DispatcherState.MANUAL_CHARGE_CONFIRM.value: "אישור חיוב",
}

STATION_OWNER_LABELS: dict[str, str] = {
    StationOwnerState.MENU.value: "תפריט תחנה",
    StationOwnerState.MANAGE_OWNERS.value: "ניהול בעלים",
    StationOwnerState.ADD_OWNER_PHONE.value: "טלפון בעלים חדש",
    StationOwnerState.REMOVE_OWNER_SELECT.value: "בחירת בעלים להסרה",
    StationOwnerState.CONFIRM_REMOVE_OWNER.value: "אישור הסרת בעלים",
    StationOwnerState.MANAGE_DISPATCHERS.value: "ניהול סדרנים",
    StationOwnerState.ADD_DISPATCHER_PHONE.value: "טלפון סדרן חדש",
    StationOwnerState.REMOVE_DISPATCHER_SELECT.value: "בחירת סדרן להסרה",
    StationOwnerState.CONFIRM_REMOVE_DISPATCHER.value: "אישור הסרת סדרן",
    StationOwnerState.VIEW_WALLET.value: "ארנק תחנה",
    StationOwnerState.SET_COMMISSION_RATE.value: "שינוי אחוז עמלה",
    StationOwnerState.COLLECTION_REPORT.value: "דוח גבייה",
    StationOwnerState.VIEW_BLACKLIST.value: "רשימה שחורה",
    StationOwnerState.ADD_BLACKLIST_PHONE.value: "טלפון לחסימה",
    StationOwnerState.ADD_BLACKLIST_REASON.value: "סיבת חסימה",
    StationOwnerState.REMOVE_BLACKLIST_SELECT.value: "הסרה מרשימה שחורה",
    StationOwnerState.CONFIRM_REMOVE_BLACKLIST.value: "אישור הסרה מרשימה שחורה",
    StationOwnerState.GROUP_SETTINGS.value: "הגדרות קבוצות",
    StationOwnerState.SET_PUBLIC_GROUP.value: "קבוצה ציבורית",
    StationOwnerState.SET_PRIVATE_GROUP.value: "קבוצה פרטית",
    # סעיף 8: הגדרות תחנה מורחבות
    StationOwnerState.STATION_SETTINGS.value: "הגדרות תחנה",
    StationOwnerState.EDIT_STATION_NAME.value: "עריכת שם תחנה",
    StationOwnerState.EDIT_STATION_DESCRIPTION.value: "עריכת תיאור",
    StationOwnerState.EDIT_OPERATING_HOURS.value: "שעות פעילות",
    StationOwnerState.EDIT_SERVICE_AREAS.value: "אזורי שירות",
}


def _sanitize_id(state_value: str) -> str:
    """המרת ערך state למזהה תקין ב-Mermaid (ללא נקודות)."""
    return state_value.replace(".", "_")


def generate_mermaid_from_transitions(
    transitions: dict[Any, list[Any]],
    labels: dict[str, str],
) -> str:
    """
    ייצור דיאגרמת stateDiagram-v2 מ-transition dictionary.

    Args:
        transitions: מילון מעברים {state: [target_states]}
        labels: מילון תוויות {state_value: "תווית בעברית"}
    """
    lines: list[str] = []
    lines.append("stateDiagram-v2")

    # איסוף כל ה-states
    all_states: set[str] = set()
    for source, targets in transitions.items():
        all_states.add(source.value)
        for target in targets:
            all_states.add(target.value)

    # הגדרת תוויות לכל state
    for state_value in sorted(all_states):
        sid = _sanitize_id(state_value)
        label = labels.get(state_value, state_value)
        lines.append(f"    {sid} : {label}")

    lines.append("")

    # מציאת state התחלתי (INITIAL או הראשון)
    initial_states = [s for s in all_states if "INITIAL" in s or s.endswith(".NEW")]
    for init_state in sorted(initial_states):
        sid = _sanitize_id(init_state)
        lines.append(f"    [*] --> {sid}")

    lines.append("")

    # הגדרת מעברים
    for source, targets in transitions.items():
        source_id = _sanitize_id(source.value)
        for target in targets:
            target_id = _sanitize_id(target.value)
            lines.append(f"    {source_id} --> {target_id}")

    return "\n".join(lines)


def generate_delivery_status_diagram() -> str:
    """דיאגרמת DeliveryStatus — מבוססת על הלוגיקה בקוד."""
    return """stateDiagram-v2
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
    cancelled --> [*]"""


def generate_approval_status_diagram() -> str:
    """דיאגרמת ApprovalStatus — זרימת אישור שליח."""
    return """stateDiagram-v2
    pending : ממתין לאישור
    approved : מאושר
    rejected : נדחה
    blocked : חסום

    [*] --> pending : השלמת רישום KYC
    pending --> approved : אדמין אישר
    pending --> rejected : אדמין דחה (עם הערת דחייה)
    approved --> blocked : חסימת שליח
    rejected --> pending : הגשה מחדש"""


def generate_all_diagrams() -> dict[str, str]:
    """ייצור כל הדיאגרמות ומחזיר מילון {שם: mermaid_string}."""
    diagrams: dict[str, str] = {}

    diagrams["שולח (SenderState)"] = generate_mermaid_from_transitions(
        SENDER_TRANSITIONS, SENDER_LABELS,
    )
    diagrams["שליח (CourierState)"] = generate_mermaid_from_transitions(
        COURIER_TRANSITIONS, COURIER_LABELS,
    )
    diagrams["סדרן (DispatcherState)"] = generate_mermaid_from_transitions(
        DISPATCHER_TRANSITIONS, DISPATCHER_LABELS,
    )
    diagrams["בעל תחנה (StationOwnerState)"] = generate_mermaid_from_transitions(
        STATION_OWNER_TRANSITIONS, STATION_OWNER_LABELS,
    )
    diagrams["סטטוס משלוח (DeliveryStatus)"] = generate_delivery_status_diagram()
    diagrams["סטטוס אישור שליח (ApprovalStatus)"] = generate_approval_status_diagram()

    return diagrams


def format_diagrams_as_markdown(diagrams: dict[str, str]) -> str:
    """עיצוב הדיאגרמות כ-markdown עם בלוקי mermaid."""
    sections: list[str] = []
    for name, mermaid_code in diagrams.items():
        sections.append(f"#### {name}\n")
        sections.append(f"```mermaid\n{mermaid_code}\n```\n")
    return "\n".join(sections)


def update_claude_md(markdown_content: str) -> None:
    """עדכון CLAUDE.md עם הדיאגרמות בסעיף ארכיטקטורה."""
    claude_md_path = Path(__file__).resolve().parent.parent / "CLAUDE.md"
    content = claude_md_path.read_text(encoding="utf-8")

    # סמנים להחלפה
    start_marker = "<!-- STATE_DIAGRAMS_START -->"
    end_marker = "<!-- STATE_DIAGRAMS_END -->"

    new_section = f"{start_marker}\n\n### דיאגרמות מכונת מצבים\n\n{markdown_content}\n{end_marker}"

    if start_marker in content:
        # החלפת סעיף קיים
        pattern = re.compile(
            re.escape(start_marker) + r".*?" + re.escape(end_marker),
            re.DOTALL,
        )
        content = pattern.sub(new_section, content)
    else:
        # הוספה אחרי בלוק הארכיטקטורה (אחרי ``` הסוגר)
        arch_pattern = re.compile(
            r"(## ארכיטקטורה\n```\n.*?\n```)\n",
            re.DOTALL,
        )
        match = arch_pattern.search(content)
        if match:
            insert_pos = match.end()
            content = (
                content[:insert_pos]
                + "\n"
                + new_section
                + "\n"
                + content[insert_pos:]
            )
        else:
            # fallback — הוספה לסוף הקובץ
            content += "\n\n" + new_section + "\n"

    claude_md_path.write_text(content, encoding="utf-8")
    print(f"עודכן: {claude_md_path}")


def check_claude_md(markdown_content: str) -> bool:
    """
    בדיקה שהדיאגרמות ב-CLAUDE.md מסונכרנות עם הקוד.

    מחזיר True אם הכל מסונכרן, False אם יש הבדלים.
    """
    claude_md_path = Path(__file__).resolve().parent.parent / "CLAUDE.md"
    content = claude_md_path.read_text(encoding="utf-8")

    start_marker = "<!-- STATE_DIAGRAMS_START -->"
    end_marker = "<!-- STATE_DIAGRAMS_END -->"

    expected_section = f"{start_marker}\n\n### דיאגרמות מכונת מצבים\n\n{markdown_content}\n{end_marker}"

    if start_marker not in content:
        print("שגיאה: לא נמצאו סמני דיאגרמות ב-CLAUDE.md")
        return False

    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        print("שגיאה: לא נמצא בלוק דיאגרמות ב-CLAUDE.md")
        return False

    current_section = match.group(0)
    if current_section == expected_section:
        print("הדיאגרמות מסונכרנות עם הקוד ✓")
        return True

    print("שגיאה: הדיאגרמות ב-CLAUDE.md אינן מסונכרנות עם הקוד!")
    print("הרץ: python scripts/generate_state_diagrams.py --update-claude-md")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ייצור דיאגרמות Mermaid ממכונות המצבים"
    )
    parser.add_argument(
        "--update-claude-md",
        action="store_true",
        help="עדכון אוטומטי של CLAUDE.md עם הדיאגרמות",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="בדיקה שהדיאגרמות ב-CLAUDE.md מסונכרנות עם הקוד (ל-CI)",
    )
    args = parser.parse_args()

    diagrams = generate_all_diagrams()
    markdown = format_diagrams_as_markdown(diagrams)

    if args.check:
        is_synced = check_claude_md(markdown)
        sys.exit(0 if is_synced else 1)
    elif args.update_claude_md:
        update_claude_md(markdown)
    else:
        print(markdown)


if __name__ == "__main__":
    main()
