"""
בדיקות לסקריפט ייצור דיאגרמות Mermaid ממכונות המצבים.
"""
import pytest
import sys
from pathlib import Path

# הוספת root לנתיב
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.generate_state_diagrams import (
    generate_mermaid_from_transitions,
    generate_delivery_status_diagram,
    generate_approval_status_diagram,
    generate_all_diagrams,
    format_diagrams_as_markdown,
    check_claude_md,
    _sanitize_id,
    SENDER_LABELS,
    COURIER_LABELS,
    DISPATCHER_LABELS,
    STATION_OWNER_LABELS,
)
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


class TestSanitizeId:
    """בדיקות ל-_sanitize_id"""

    @pytest.mark.unit
    def test_replaces_dots_with_underscores(self) -> None:
        assert _sanitize_id("SENDER.MENU") == "SENDER_MENU"

    @pytest.mark.unit
    def test_handles_multiple_dots(self) -> None:
        assert _sanitize_id("SENDER.DELIVERY.PICKUP_CITY") == "SENDER_DELIVERY_PICKUP_CITY"

    @pytest.mark.unit
    def test_handles_no_dots(self) -> None:
        assert _sanitize_id("INITIAL") == "INITIAL"


class TestSenderDiagram:
    """בדיקות לדיאגרמת שולח"""

    @pytest.mark.unit
    def test_contains_all_sender_states(self) -> None:
        """כל ה-states של שולח חייבים להופיע בדיאגרמה"""
        mermaid = generate_mermaid_from_transitions(
            SENDER_TRANSITIONS, SENDER_LABELS
        )
        # בודקים states שנמצאים ב-transitions (לא legacy)
        for state in SENDER_TRANSITIONS:
            sanitized = _sanitize_id(state.value)
            assert sanitized in mermaid, f"חסר state: {state.value}"

    @pytest.mark.unit
    def test_contains_all_sender_transitions(self) -> None:
        """כל המעברים חייבים להופיע בדיאגרמה"""
        mermaid = generate_mermaid_from_transitions(
            SENDER_TRANSITIONS, SENDER_LABELS
        )
        for source, targets in SENDER_TRANSITIONS.items():
            source_id = _sanitize_id(source.value)
            for target in targets:
                target_id = _sanitize_id(target.value)
                transition = f"{source_id} --> {target_id}"
                assert transition in mermaid, f"חסר מעבר: {transition}"

    @pytest.mark.unit
    def test_starts_with_state_diagram_v2(self) -> None:
        mermaid = generate_mermaid_from_transitions(
            SENDER_TRANSITIONS, SENDER_LABELS
        )
        assert mermaid.startswith("stateDiagram-v2")

    @pytest.mark.unit
    def test_has_initial_state_arrow(self) -> None:
        mermaid = generate_mermaid_from_transitions(
            SENDER_TRANSITIONS, SENDER_LABELS
        )
        assert "[*] --> INITIAL" in mermaid

    @pytest.mark.unit
    def test_has_hebrew_labels(self) -> None:
        """בדיקה שכל ה-states מקבלים תוויות בעברית"""
        mermaid = generate_mermaid_from_transitions(
            SENDER_TRANSITIONS, SENDER_LABELS
        )
        assert "תפריט ראשי" in mermaid
        assert "עיר איסוף" in mermaid
        assert "אישור משלוח" in mermaid

    @pytest.mark.unit
    def test_all_sender_states_have_labels(self) -> None:
        """כל state ב-transitions חייב שתהיה לו תווית"""
        all_states = set()
        for source, targets in SENDER_TRANSITIONS.items():
            all_states.add(source.value)
            for target in targets:
                all_states.add(target.value)
        for state_value in all_states:
            assert state_value in SENDER_LABELS, f"חסרה תווית ל-{state_value}"


class TestCourierDiagram:
    """בדיקות לדיאגרמת שליח"""

    @pytest.mark.unit
    def test_contains_all_courier_states(self) -> None:
        mermaid = generate_mermaid_from_transitions(
            COURIER_TRANSITIONS, COURIER_LABELS
        )
        for state in COURIER_TRANSITIONS:
            sanitized = _sanitize_id(state.value)
            assert sanitized in mermaid, f"חסר state: {state.value}"

    @pytest.mark.unit
    def test_contains_all_courier_transitions(self) -> None:
        mermaid = generate_mermaid_from_transitions(
            COURIER_TRANSITIONS, COURIER_LABELS
        )
        for source, targets in COURIER_TRANSITIONS.items():
            source_id = _sanitize_id(source.value)
            for target in targets:
                target_id = _sanitize_id(target.value)
                transition = f"{source_id} --> {target_id}"
                assert transition in mermaid, f"חסר מעבר: {transition}"

    @pytest.mark.unit
    def test_registration_flow_is_linear(self) -> None:
        """בדיקה שזרימת הרישום היא ליניארית"""
        mermaid = generate_mermaid_from_transitions(
            COURIER_TRANSITIONS, COURIER_LABELS
        )
        assert "COURIER_REGISTER_COLLECT_NAME --> COURIER_REGISTER_COLLECT_DOCUMENT" in mermaid
        assert "COURIER_REGISTER_COLLECT_DOCUMENT --> COURIER_REGISTER_COLLECT_SELFIE" in mermaid
        assert "COURIER_REGISTER_TERMS --> COURIER_PENDING_APPROVAL" in mermaid

    @pytest.mark.unit
    def test_all_courier_states_have_labels(self) -> None:
        all_states = set()
        for source, targets in COURIER_TRANSITIONS.items():
            all_states.add(source.value)
            for target in targets:
                all_states.add(target.value)
        for state_value in all_states:
            assert state_value in COURIER_LABELS, f"חסרה תווית ל-{state_value}"


class TestDispatcherDiagram:
    """בדיקות לדיאגרמת סדרן"""

    @pytest.mark.unit
    def test_contains_all_dispatcher_states(self) -> None:
        mermaid = generate_mermaid_from_transitions(
            DISPATCHER_TRANSITIONS, DISPATCHER_LABELS
        )
        for state in DISPATCHER_TRANSITIONS:
            sanitized = _sanitize_id(state.value)
            assert sanitized in mermaid, f"חסר state: {state.value}"

    @pytest.mark.unit
    def test_contains_all_dispatcher_transitions(self) -> None:
        mermaid = generate_mermaid_from_transitions(
            DISPATCHER_TRANSITIONS, DISPATCHER_LABELS
        )
        for source, targets in DISPATCHER_TRANSITIONS.items():
            source_id = _sanitize_id(source.value)
            for target in targets:
                target_id = _sanitize_id(target.value)
                transition = f"{source_id} --> {target_id}"
                assert transition in mermaid, f"חסר מעבר: {transition}"

    @pytest.mark.unit
    def test_all_dispatcher_states_have_labels(self) -> None:
        all_states = set()
        for source, targets in DISPATCHER_TRANSITIONS.items():
            all_states.add(source.value)
            for target in targets:
                all_states.add(target.value)
        for state_value in all_states:
            assert state_value in DISPATCHER_LABELS, f"חסרה תווית ל-{state_value}"


class TestStationOwnerDiagram:
    """בדיקות לדיאגרמת בעל תחנה"""

    @pytest.mark.unit
    def test_contains_all_station_states(self) -> None:
        mermaid = generate_mermaid_from_transitions(
            STATION_OWNER_TRANSITIONS, STATION_OWNER_LABELS
        )
        for state in STATION_OWNER_TRANSITIONS:
            sanitized = _sanitize_id(state.value)
            assert sanitized in mermaid, f"חסר state: {state.value}"

    @pytest.mark.unit
    def test_contains_all_station_transitions(self) -> None:
        mermaid = generate_mermaid_from_transitions(
            STATION_OWNER_TRANSITIONS, STATION_OWNER_LABELS
        )
        for source, targets in STATION_OWNER_TRANSITIONS.items():
            source_id = _sanitize_id(source.value)
            for target in targets:
                target_id = _sanitize_id(target.value)
                transition = f"{source_id} --> {target_id}"
                assert transition in mermaid, f"חסר מעבר: {transition}"

    @pytest.mark.unit
    def test_all_station_states_have_labels(self) -> None:
        all_states = set()
        for source, targets in STATION_OWNER_TRANSITIONS.items():
            all_states.add(source.value)
            for target in targets:
                all_states.add(target.value)
        for state_value in all_states:
            assert state_value in STATION_OWNER_LABELS, f"חסרה תווית ל-{state_value}"


class TestDeliveryStatusDiagram:
    """בדיקות לדיאגרמת סטטוס משלוח"""

    @pytest.mark.unit
    def test_contains_all_delivery_statuses(self) -> None:
        mermaid = generate_delivery_status_diagram()
        statuses = ["open", "pending_approval", "captured", "in_progress", "delivered", "cancelled"]
        for status in statuses:
            assert status in mermaid, f"חסר סטטוס: {status}"

    @pytest.mark.unit
    def test_contains_key_transitions(self) -> None:
        mermaid = generate_delivery_status_diagram()
        assert "open --> pending_approval" in mermaid
        assert "open --> captured" in mermaid
        assert "captured --> in_progress" in mermaid
        assert "in_progress --> delivered" in mermaid

    @pytest.mark.unit
    def test_has_hebrew_labels(self) -> None:
        mermaid = generate_delivery_status_diagram()
        assert "פתוח" in mermaid
        assert "נמסר" in mermaid
        assert "בוטל" in mermaid


class TestApprovalStatusDiagram:
    """בדיקות לדיאגרמת סטטוס אישור"""

    @pytest.mark.unit
    def test_contains_all_approval_statuses(self) -> None:
        mermaid = generate_approval_status_diagram()
        statuses = ["pending", "approved", "rejected", "blocked"]
        for status in statuses:
            assert status in mermaid, f"חסר סטטוס: {status}"

    @pytest.mark.unit
    def test_contains_key_transitions(self) -> None:
        mermaid = generate_approval_status_diagram()
        assert "pending --> approved" in mermaid
        assert "pending --> rejected" in mermaid
        assert "approved --> blocked" in mermaid


class TestGenerateAllDiagrams:
    """בדיקות לפונקציית ייצור כל הדיאגרמות"""

    @pytest.mark.unit
    def test_generates_six_diagrams(self) -> None:
        diagrams = generate_all_diagrams()
        assert len(diagrams) == 6

    @pytest.mark.unit
    def test_all_diagrams_are_valid_mermaid(self) -> None:
        diagrams = generate_all_diagrams()
        for name, mermaid in diagrams.items():
            assert "stateDiagram-v2" in mermaid, f"דיאגרמה {name} חסרה stateDiagram-v2"

    @pytest.mark.unit
    def test_diagram_names_are_in_hebrew(self) -> None:
        diagrams = generate_all_diagrams()
        for name in diagrams:
            # בדיקה שיש לפחות תו עברי אחד בשם
            has_hebrew = any("\u0590" <= c <= "\u05FF" for c in name)
            assert has_hebrew, f"שם דיאגרמה {name} חייב לכלול עברית"


class TestFormatAsMarkdown:
    """בדיקות לעיצוב כ-markdown"""

    @pytest.mark.unit
    def test_wraps_in_mermaid_code_blocks(self) -> None:
        diagrams = generate_all_diagrams()
        markdown = format_diagrams_as_markdown(diagrams)
        assert markdown.count("```mermaid") == 6
        assert markdown.count("```\n") == 6

    @pytest.mark.unit
    def test_has_h4_headers(self) -> None:
        diagrams = generate_all_diagrams()
        markdown = format_diagrams_as_markdown(diagrams)
        assert "#### שולח (SenderState)" in markdown
        assert "#### שליח (CourierState)" in markdown
        assert "#### סדרן (DispatcherState)" in markdown
        assert "#### בעל תחנה (StationOwnerState)" in markdown


class TestCheckClaudeMd:
    """בדיקות לפונקציית ולידציה של CLAUDE.md"""

    @pytest.mark.unit
    def test_check_returns_true_when_synced(self) -> None:
        """בדיקה שהסנכרון מזוהה נכון כשהקובץ מעודכן"""
        diagrams = generate_all_diagrams()
        markdown = format_diagrams_as_markdown(diagrams)
        # check_claude_md קורא את CLAUDE.md — אם הוא מעודכן צריך לחזור True
        result = check_claude_md(markdown)
        assert result is True
