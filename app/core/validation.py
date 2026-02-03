"""
Input Validation Utilities

Provides comprehensive validation for user inputs including:
- Phone number validation (Israeli format)
- Address validation and sanitization
- Text sanitization for injection prevention
"""
import re
import html
from typing import TypeVar
from pydantic import field_validator, model_validator
from pydantic_core.core_schema import ValidationInfo

# Type variable for generic validators
T = TypeVar("T")


class ValidationPatterns:
    """Regex patterns for validation"""

    # Israeli phone numbers: 05X-XXXXXXX or +972-5X-XXXXXXX
    PHONE_ISRAEL = re.compile(
        r"^(?:"
        r"(?:\+972|972)[-\s]?(?:[23489]|5[0-9])[-\s]?\d{3}[-\s]?\d{4}|"  # +972/972 format
        r"0(?:[23489]|5[0-9])[-\s]?\d{3}[-\s]?\d{4}"  # 05X format
        r")$"
    )

    # International phone (E.164 format)
    PHONE_INTERNATIONAL = re.compile(r"^\+[1-9]\d{6,14}$")

    # Hebrew and English names
    NAME = re.compile(r"^[\u0590-\u05FF\u0041-\u005A\u0061-\u007Aa-zA-Z\s\-\'\.]{2,100}$")

    # Address (Hebrew, English, numbers, common punctuation)
    ADDRESS = re.compile(
        r"^[\u0590-\u05FF\u0041-\u005A\u0061-\u007Aa-zA-Z0-9\s\,\.\-\/\'\"]+$"
    )

    # Dangerous patterns for injection prevention
    # More specific patterns to avoid false positives on legitimate addresses like "Union Street"
    SQL_INJECTION_PATTERNS = [
        # SQL comments
        re.compile(r"--\s*$|/\*|\*/", re.IGNORECASE),
        # Classic SQL injection: ' OR '1'='1 or ' OR 1=1 or " AND "="
        # Pattern: quote + OR/AND + (quoted value OR number) + equals
        re.compile(r"['\"]\s*(OR|AND)\s+['\"]?\w*['\"]?\s*=", re.IGNORECASE),
        # Tautology patterns: OR 1=1, AND 1=1, OR 'a'='a'
        re.compile(r"\b(OR|AND)\s+(\d+\s*=\s*\d+|'[^']*'\s*=\s*'[^']*'|\"[^\"]*\"\s*=\s*\"[^\"]*\")", re.IGNORECASE),
        # Chained SQL commands (e.g., ; DROP TABLE)
        re.compile(r";\s*(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b", re.IGNORECASE),
        # UNION SELECT pattern (common injection)
        re.compile(r"\bUNION\s+(ALL\s+)?SELECT\b", re.IGNORECASE),
        # SQL keywords with parentheses (function-like usage)
        re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\s*\(", re.IGNORECASE),
        # Hex encoding attempts (at least 4 hex chars to avoid false positives)
        re.compile(r"0x[0-9a-fA-F]{4,}"),
        # SQL batching with semicolons and keywords
        re.compile(r";\s*DROP\b", re.IGNORECASE),
    ]

    # Script injection patterns
    XSS_PATTERNS = [
        re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
        re.compile(r"javascript:", re.IGNORECASE),
        # Event handlers must start at word boundary (onclick=, onload=, etc.)
        # Avoids false positives like "condition = fragile"
        re.compile(r"\bon\w+=", re.IGNORECASE),
        re.compile(r"<iframe", re.IGNORECASE),
        re.compile(r"<object", re.IGNORECASE),
        re.compile(r"<embed", re.IGNORECASE),
    ]


class PhoneNumberValidator:
    """Phone number validation and normalization"""

    @staticmethod
    def validate(phone: str, allow_international: bool = True) -> bool:
        """
        Validate phone number format.

        Args:
            phone: Phone number to validate
            allow_international: Allow international format

        Returns:
            True if valid, False otherwise
        """
        if not phone:
            return False

        # Remove spaces and dashes for validation
        cleaned = re.sub(r"[\s\-]", "", phone)

        # Check Israeli format
        if ValidationPatterns.PHONE_ISRAEL.match(cleaned):
            return True

        # Check international format if allowed
        if allow_international and ValidationPatterns.PHONE_INTERNATIONAL.match(cleaned):
            return True

        return False

    @staticmethod
    def normalize(phone: str) -> str:
        """
        Normalize phone number to standard format.
        Converts Israeli numbers to +972 format.

        Args:
            phone: Phone number to normalize

        Returns:
            Normalized phone number
        """
        # Remove all non-digit characters except +
        cleaned = re.sub(r"[^\d+]", "", phone)

        # Convert Israeli format to international
        if cleaned.startswith("0"):
            cleaned = "+972" + cleaned[1:]
        elif cleaned.startswith("972") and not cleaned.startswith("+"):
            cleaned = "+" + cleaned

        return cleaned

    @staticmethod
    def mask(phone: str) -> str:
        """
        Mask phone number for logging (privacy).

        Args:
            phone: Phone number to mask

        Returns:
            Masked phone number (e.g., +972-5X-XXX-**34)
        """
        if len(phone) < 4:
            return "****"
        return phone[:-4] + "****"


class TextSanitizer:
    """Text sanitization for security"""

    @staticmethod
    def sanitize(text: str, max_length: int = 1000) -> str:
        """
        Sanitize text input for safe storage.

        Note: This does NOT HTML escape - that should be done at display time
        using sanitize_for_html(). This function only:
        - Trims whitespace
        - Enforces max length
        - Removes null bytes and control characters

        Args:
            text: Text to sanitize
            max_length: Maximum allowed length

        Returns:
            Sanitized text
        """
        if not text:
            return ""

        # Trim whitespace
        sanitized = text.strip()

        # Enforce max length
        sanitized = sanitized[:max_length]

        # Remove null bytes (security)
        sanitized = sanitized.replace("\x00", "")

        # Collapse multiple spaces into one
        import re
        sanitized = re.sub(r" +", " ", sanitized)

        return sanitized

    @staticmethod
    def sanitize_for_html(text: str) -> str:
        """
        Sanitize text for safe HTML display.

        Args:
            text: Text to sanitize

        Returns:
            HTML-safe text
        """
        if not text:
            return ""

        return html.escape(text)

    @staticmethod
    def check_for_injection(text: str) -> tuple[bool, str | None]:
        """
        Check text for potential injection attacks.

        Args:
            text: Text to check

        Returns:
            Tuple of (is_safe, detected_pattern)
        """
        if not text:
            return True, None

        # Check for SQL injection patterns
        for pattern in ValidationPatterns.SQL_INJECTION_PATTERNS:
            if pattern.search(text):
                return False, "SQL injection pattern detected"

        # Check for XSS patterns
        for pattern in ValidationPatterns.XSS_PATTERNS:
            if pattern.search(text):
                return False, "XSS pattern detected"

        return True, None

    @staticmethod
    def remove_control_characters(text: str) -> str:
        """
        Remove control characters from text.

        Args:
            text: Text to clean

        Returns:
            Cleaned text
        """
        if not text:
            return ""

        # Keep newlines and tabs, remove other control chars
        return "".join(
            char for char in text
            if char >= " " or char in "\n\r\t"
        )


class AddressValidator:
    """Address validation utilities"""

    MIN_LENGTH = 5
    MAX_LENGTH = 200

    @staticmethod
    def validate(address: str) -> tuple[bool, str | None]:
        """
        Validate address format.

        Args:
            address: Address to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not address:
            return False, "Address is required"

        address = address.strip()

        if len(address) < AddressValidator.MIN_LENGTH:
            return False, f"Address too short (minimum {AddressValidator.MIN_LENGTH} characters)"

        if len(address) > AddressValidator.MAX_LENGTH:
            return False, f"Address too long (maximum {AddressValidator.MAX_LENGTH} characters)"

        if not ValidationPatterns.ADDRESS.match(address):
            return False, "Address contains invalid characters"

        # Check for injection
        is_safe, pattern = TextSanitizer.check_for_injection(address)
        if not is_safe:
            return False, f"Invalid address: {pattern}"

        return True, None

    @staticmethod
    def normalize(address: str) -> str:
        """
        Normalize address for consistency.

        Args:
            address: Address to normalize

        Returns:
            Normalized address
        """
        if not address:
            return ""

        # Trim whitespace
        normalized = address.strip()

        # Collapse multiple spaces
        normalized = re.sub(r"\s+", " ", normalized)

        # Normalize common abbreviations
        replacements = {
            "רח' ": "רחוב ",
            "רח'": "רחוב ",
            "ת.ד.": "תא דואר",
            "ת.ד": "תא דואר",
        }
        for old, new in replacements.items():
            normalized = normalized.replace(old, new)

        return normalized


class NameValidator:
    """Name validation utilities"""

    MIN_LENGTH = 2
    MAX_LENGTH = 100

    @staticmethod
    def validate(name: str) -> tuple[bool, str | None]:
        """
        Validate name format.

        Args:
            name: Name to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not name:
            return False, "Name is required"

        name = name.strip()

        if len(name) < NameValidator.MIN_LENGTH:
            return False, f"Name too short (minimum {NameValidator.MIN_LENGTH} characters)"

        if len(name) > NameValidator.MAX_LENGTH:
            return False, f"Name too long (maximum {NameValidator.MAX_LENGTH} characters)"

        if not ValidationPatterns.NAME.match(name):
            return False, "Name contains invalid characters"

        return True, None


class AmountValidator:
    """Monetary amount validation"""

    @staticmethod
    def validate(
        amount: float,
        min_value: float = 0.0,
        max_value: float = 100000.0
    ) -> tuple[bool, str | None]:
        """
        Validate monetary amount.

        Args:
            amount: Amount to validate
            min_value: Minimum allowed value
            max_value: Maximum allowed value

        Returns:
            Tuple of (is_valid, error_message)
        """
        if amount < min_value:
            return False, f"Amount must be at least {min_value}"

        if amount > max_value:
            return False, f"Amount cannot exceed {max_value}"

        # Check for reasonable decimal places (2 for currency)
        # שימוש בהשוואה עם tolerance כי floating point לא מדויק
        # לדוגמה: 0.1 + 0.2 = 0.30000000000000004
        if abs(round(amount, 2) - amount) > 1e-9:
            return False, "Amount cannot have more than 2 decimal places"

        return True, None


# Pydantic field validators for reuse
def phone_validator(v: str | None) -> str | None:
    """Pydantic field validator for phone numbers"""
    if v is None:
        return None
    if not PhoneNumberValidator.validate(v):
        raise ValueError("Invalid phone number format")
    return PhoneNumberValidator.normalize(v)


def address_validator(v: str | None) -> str | None:
    """Pydantic field validator for addresses"""
    if v is None:
        return None
    is_valid, error = AddressValidator.validate(v)
    if not is_valid:
        raise ValueError(error)
    return AddressValidator.normalize(v)


def name_validator(v: str | None) -> str | None:
    """Pydantic field validator for names"""
    if v is None:
        return None
    is_valid, error = NameValidator.validate(v)
    if not is_valid:
        raise ValueError(error)
    return TextSanitizer.sanitize(v.strip(), max_length=NameValidator.MAX_LENGTH)


def sanitized_text_validator(v: str | None, max_length: int = 1000) -> str | None:
    """Pydantic field validator for sanitized text"""
    if v is None:
        return None
    is_safe, pattern = TextSanitizer.check_for_injection(v)
    if not is_safe:
        raise ValueError(f"Invalid input: {pattern}")
    return TextSanitizer.sanitize(v, max_length)


def convert_html_to_whatsapp(text: str) -> str:
    """
    ממיר תגי HTML לפורמט וואטסאפ.

    וואטסאפ משתמש בפורמט שונה מ-HTML:
    - Bold: *text* (במקום <b>text</b>)
    - Italic: _text_ (במקום <i>text</i>)
    - Strikethrough: ~text~ (במקום <s>text</s>)
    - Monospace: `text` (במקום <code>text</code>)

    Args:
        text: טקסט עם תגי HTML

    Returns:
        טקסט מומר לפורמט וואטסאפ
    """
    if not text:
        return ""

    # המרת תגי bold
    result = re.sub(r"<b>(.*?)</b>", r"*\1*", text, flags=re.DOTALL)
    result = re.sub(r"<strong>(.*?)</strong>", r"*\1*", result, flags=re.DOTALL)

    # המרת תגי italic
    result = re.sub(r"<i>(.*?)</i>", r"_\1_", result, flags=re.DOTALL)
    result = re.sub(r"<em>(.*?)</em>", r"_\1_", result, flags=re.DOTALL)

    # המרת תגי strikethrough
    result = re.sub(r"<s>(.*?)</s>", r"~\1~", result, flags=re.DOTALL)
    result = re.sub(r"<strike>(.*?)</strike>", r"~\1~", result, flags=re.DOTALL)
    result = re.sub(r"<del>(.*?)</del>", r"~\1~", result, flags=re.DOTALL)

    # המרת תגי code
    result = re.sub(r"<code>(.*?)</code>", r"`\1`", result, flags=re.DOTALL)
    result = re.sub(r"<pre>(.*?)</pre>", r"```\1```", result, flags=re.DOTALL)

    # הסרת תגי HTML נוספים שלא נתמכים (כמו <a>, <br> וכו')
    result = re.sub(r"<br\s*/?>", "\n", result, flags=re.IGNORECASE)
    result = re.sub(r"<[^>]+>", "", result)

    return result
