"""
Tests for Input Validation Utilities
"""
import pytest
from app.core.validation import (
    PhoneNumberValidator,
    AddressValidator,
    NameValidator,
    AmountValidator,
    TextSanitizer,
    ValidationPatterns
)


class TestPhoneNumberValidator:
    """Tests for phone number validation"""

    @pytest.mark.unit
    @pytest.mark.parametrize("phone,expected", [
        # Valid Israeli numbers
        ("0501234567", True),
        ("050-123-4567", True),
        ("050 123 4567", True),
        ("+972501234567", True),
        ("+972-50-123-4567", True),
        ("972501234567", True),
        # Valid landline numbers
        ("031234567", True),
        ("03-123-4567", True),
        # Invalid numbers
        ("123", False),
        ("abcdefghij", False),
        ("", False),
        ("0501234", False),  # Too short
        ("050123456789012", False),  # Too long
    ])
    def test_validate_israeli_phone(self, phone: str, expected: bool):
        """Test Israeli phone number validation"""
        assert PhoneNumberValidator.validate(phone) == expected

    @pytest.mark.unit
    def test_normalize_phone(self):
        """Test phone number normalization"""
        # Israeli format to international
        assert PhoneNumberValidator.normalize("0501234567") == "+972501234567"
        assert PhoneNumberValidator.normalize("050-123-4567") == "+972501234567"

        # Already international
        assert PhoneNumberValidator.normalize("+972501234567") == "+972501234567"

    @pytest.mark.unit
    def test_mask_phone(self):
        """Test phone number masking for privacy"""
        assert PhoneNumberValidator.mask("+972501234567") == "+97250123****"
        assert PhoneNumberValidator.mask("123") == "****"


class TestAddressValidator:
    """Tests for address validation"""

    @pytest.mark.unit
    @pytest.mark.parametrize("address,valid", [
        ("רחוב הרצל 1, תל אביב", True),
        ("Herzl Street 1, Tel Aviv", True),
        ("רחוב בן יהודה 50", True),
        ("1234", False),  # Too short
        ("", False),  # Empty
        ("x" * 201, False),  # Too long
    ])
    def test_validate_address(self, address: str, valid: bool):
        """Test address validation"""
        is_valid, error = AddressValidator.validate(address)
        assert is_valid == valid

    @pytest.mark.unit
    def test_normalize_address(self):
        """Test address normalization"""
        # Normalize whitespace
        assert AddressValidator.normalize("  רחוב   הרצל   1  ") == "רחוב הרצל 1"

        # Normalize abbreviations
        assert "רחוב" in AddressValidator.normalize("רח' הרצל 1")


class TestNameValidator:
    """Tests for name validation"""

    @pytest.mark.unit
    @pytest.mark.parametrize("name,valid", [
        ("יוסי כהן", True),
        ("John Doe", True),
        ("עמית לוי-כהן", True),
        ("A", False),  # Too short
        ("", False),  # Empty
        ("x" * 101, False),  # Too long
    ])
    def test_validate_name(self, name: str, valid: bool):
        """Test name validation"""
        is_valid, error = NameValidator.validate(name)
        assert is_valid == valid


class TestAmountValidator:
    """Tests for monetary amount validation"""

    @pytest.mark.unit
    @pytest.mark.parametrize("amount,min_val,max_val,valid", [
        (10.0, 0.0, 100.0, True),
        (0.0, 0.0, 100.0, True),
        (100.0, 0.0, 100.0, True),
        (-1.0, 0.0, 100.0, False),  # Below min
        (101.0, 0.0, 100.0, False),  # Above max
        (10.123, 0.0, 100.0, False),  # Too many decimals
    ])
    def test_validate_amount(self, amount: float, min_val: float, max_val: float, valid: bool):
        """Test amount validation"""
        is_valid, error = AmountValidator.validate(amount, min_val, max_val)
        assert is_valid == valid


class TestTextSanitizer:
    """Tests for text sanitization"""

    @pytest.mark.unit
    def test_sanitize_preserves_special_chars(self):
        """Test that sanitize preserves legitimate special characters (no HTML escaping at storage)"""
        # Names with apostrophes should be preserved
        assert TextSanitizer.sanitize("O'Brien") == "O'Brien"
        assert TextSanitizer.sanitize("Tom & Jerry") == "Tom & Jerry"
        # Hebrew text should be preserved
        assert TextSanitizer.sanitize("שלום עולם") == "שלום עולם"

    @pytest.mark.unit
    def test_sanitize_for_html_escapes(self):
        """Test HTML escaping for display time"""
        result = TextSanitizer.sanitize_for_html("<script>alert('xss')</script>")
        assert "&lt;script&gt;" in result
        assert "<script>" not in result
        # Apostrophes should be escaped for HTML
        assert "&#x27;" in TextSanitizer.sanitize_for_html("O'Brien")

    @pytest.mark.unit
    def test_sanitize_enforces_max_length(self):
        """Test max length enforcement"""
        long_text = "x" * 2000
        result = TextSanitizer.sanitize(long_text, max_length=100)
        assert len(result) == 100

    @pytest.mark.unit
    @pytest.mark.parametrize("injection_text", [
        "'; DROP TABLE users; --",
        "' OR 1=1 --",
        "1; SELECT * FROM users",
        "UNION SELECT password FROM users",
        "test'); DELETE(",
    ])
    def test_check_for_injection_sql(self, injection_text: str):
        """Test SQL injection detection"""
        is_safe, pattern = TextSanitizer.check_for_injection(injection_text)
        assert not is_safe
        assert "SQL" in pattern

    @pytest.mark.unit
    def test_check_for_injection_xss(self):
        """Test XSS injection detection"""
        is_safe, pattern = TextSanitizer.check_for_injection("<script>alert('xss')</script>")
        assert not is_safe
        assert "XSS" in pattern

    @pytest.mark.unit
    @pytest.mark.parametrize("safe_text", [
        "Hello World שלום עולם",
        "123 Union Street, Tel Aviv",
        "Please update me when delivered",
        "Drop off at the corner",
        "Select the best option",
        "Create a new delivery",
    ])
    def test_check_for_injection_safe(self, safe_text: str):
        """Test safe text passes - including words that look like SQL keywords"""
        is_safe, pattern = TextSanitizer.check_for_injection(safe_text)
        assert is_safe
        assert pattern is None

    @pytest.mark.unit
    def test_remove_control_characters(self):
        """Test control character removal"""
        text = "Hello\x00World\x0BTest"
        result = TextSanitizer.remove_control_characters(text)
        assert "\x00" not in result
        assert "HelloWorldTest" == result
