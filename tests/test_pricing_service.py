"""
בדיקות יחידה ל-PricingService — חישוב מחירים מומלצים למסלולים
"""
import pytest

from app.domain.services.pricing_service import PricingService, PriceEstimate


@pytest.mark.unit
class TestIsPricingCommand:
    """בדיקת זיהוי פקודת מחירון"""

    def test_valid_command(self):
        assert PricingService.is_pricing_command("מחירון בב ים") is True

    def test_valid_command_with_spaces(self):
        assert PricingService.is_pricing_command("  מחירון בב ים  ") is True

    def test_not_pricing_command(self):
        assert PricingService.is_pricing_command("שלום") is False

    def test_partial_match(self):
        # "מחירון" ללא רווח אחריו
        assert PricingService.is_pricing_command("מחירון") is False


@pytest.mark.unit
class TestParsePricingCommand:
    """בדיקת פרסור פקודת מחירון"""

    def test_two_word_cities(self):
        """שני קיצורים — מוצא ויעד"""
        result = PricingService.parse_pricing_command("מחירון בב ים")
        assert result is not None
        origin, dest = result
        # בב = בני ברק, ים = ירושלים (תלוי ב-CityAbbreviationService)
        assert origin is not None
        assert dest is not None

    def test_too_few_args(self):
        """פחות משתי מילים — צריך להחזיר None"""
        assert PricingService.parse_pricing_command("מחירון בב") is None

    def test_not_pricing_command(self):
        """לא פקודת מחירון"""
        assert PricingService.parse_pricing_command("שלום עולם") is None

    def test_full_city_names(self):
        """שמות מלאים"""
        result = PricingService.parse_pricing_command("מחירון תל אביב ירושלים")
        assert result is not None


@pytest.mark.unit
class TestGetPriceEstimate:
    """בדיקת שליפת מחיר"""

    def test_known_route(self):
        """מסלול ידוע — צריך להחזיר טווח מחירים"""
        estimate = PricingService.get_price_estimate("תל אביב", "ירושלים")
        assert estimate is not None
        assert estimate.min_price == 120
        assert estimate.max_price == 180

    def test_reverse_route(self):
        """מסלול הפוך — צריך להחזיר את אותו מחיר"""
        estimate = PricingService.get_price_estimate("ירושלים", "תל אביב")
        assert estimate is not None
        assert estimate.min_price == 120

    def test_unknown_route(self):
        """מסלול לא ידוע — מחזיר None"""
        estimate = PricingService.get_price_estimate("עיר לא קיימת", "עיר אחרת")
        assert estimate is None

    def test_beitar_jerusalem(self):
        """ביתר עילית ↔ ירושלים"""
        estimate = PricingService.get_price_estimate("ביתר עילית", "ירושלים")
        assert estimate is not None
        assert estimate.min_price == 50
        assert estimate.max_price == 80

    def test_eilat_tel_aviv(self):
        """אילת ↔ תל אביב — מסלול יקר"""
        estimate = PricingService.get_price_estimate("אילת", "תל אביב")
        assert estimate is not None
        assert estimate.min_price == 400


@pytest.mark.unit
class TestFormatPriceResponse:
    """בדיקת פורמט תגובה"""

    def test_format_known_price(self):
        """פורמט תקין לתגובת מחירון"""
        estimate = PriceEstimate(
            origin="תל אביב",
            destination="ירושלים",
            min_price=120,
            max_price=180,
        )
        response = PricingService.format_price_response(estimate)

        assert "תל אביב" in response
        assert "ירושלים" in response
        assert "120" in response
        assert "180" in response
        assert "מחירון" in response

    def test_format_escapes_html(self):
        """פורמט מבצע escape ל-HTML"""
        estimate = PriceEstimate(
            origin="עיר<script>",
            destination="יעד&test",
            min_price=100,
            max_price=200,
        )
        response = PricingService.format_price_response(estimate)

        assert "<script>" not in response
        assert "&amp;" in response

    def test_format_not_found(self):
        """פורמט הודעת 'לא נמצא'"""
        response = PricingService.format_not_found_response("עיר א", "עיר ב")

        assert "לא נמצא" in response
        assert "עיר א" in response
        assert "עיר ב" in response
