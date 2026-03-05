"""
סכמות ולידציה לנהג (iDriver) — Pydantic models

משמש לולידציית קלט בעת רישום, עדכון הגדרות ויצירת חיפושים.
"""
from datetime import date, time
from pydantic import BaseModel, field_validator, model_validator

from app.core.validation import NameValidator, TextSanitizer
from app.db.models.driver_profile import VehicleCategory, DressCode
from app.db.models.driver_search_settings import TripTypeFilter, UpcomingTimeframe


class DriverProfileCreate(BaseModel):
    """סכמת יצירת פרופיל נהג (רישום)"""

    name: str
    birth_date: date
    vehicle_description: str
    vehicle_category: str
    dress_code: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        is_valid, error = NameValidator.validate(v)
        if not is_valid:
            raise ValueError(error)
        return TextSanitizer.sanitize(v.strip(), max_length=NameValidator.MAX_LENGTH)

    @field_validator("birth_date")
    @classmethod
    def validate_age(cls, v: date) -> date:
        today = date.today()
        # חישוב גיל מדויק
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < 16:
            raise ValueError("גיל מינימלי להרשמה הוא 16")
        if age > 99:
            raise ValueError("גיל מקסימלי הוא 99")
        return v

    @field_validator("vehicle_description")
    @classmethod
    def validate_vehicle_description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("תיאור רכב הוא שדה חובה")
        if len(v) > 200:
            raise ValueError("תיאור רכב לא יכול לחרוג מ-200 תווים")
        is_safe, pattern = TextSanitizer.check_for_injection(v)
        if not is_safe:
            raise ValueError("תיאור רכב מכיל תוכן לא תקין")
        return TextSanitizer.sanitize(v, max_length=200)

    @field_validator("vehicle_category")
    @classmethod
    def validate_vehicle_category(cls, v: str) -> str:
        valid_values = {e.value for e in VehicleCategory}
        if v not in valid_values:
            raise ValueError(f"קטגוריית רכב לא תקינה. ערכים אפשריים: {', '.join(valid_values)}")
        return v

    @field_validator("dress_code")
    @classmethod
    def validate_dress_code(cls, v: str) -> str:
        valid_values = {e.value for e in DressCode}
        if v not in valid_values:
            raise ValueError(f"קוד לבוש לא תקין. ערכים אפשריים: {', '.join(valid_values)}")
        return v


class DriverSearchSettingsUpdate(BaseModel):
    """סכמת עדכון הגדרות חיפוש נהג"""

    vehicle_type_filter: str | None = None
    trip_type_filter: str | None = None
    show_deliveries: bool | None = None
    upcoming_timeframe: str | None = None
    future_only_enabled: bool | None = None
    future_only_start_time: time | None = None

    @field_validator("vehicle_type_filter")
    @classmethod
    def validate_vehicle_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        valid_values = {e.value for e in VehicleCategory}
        if v not in valid_values:
            raise ValueError(f"סוג רכב לא תקין. ערכים אפשריים: {', '.join(valid_values)}")
        return v

    @field_validator("trip_type_filter")
    @classmethod
    def validate_trip_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        valid_values = {e.value for e in TripTypeFilter}
        if v not in valid_values:
            raise ValueError(f"סוג נסיעה לא תקין. ערכים אפשריים: {', '.join(valid_values)}")
        return v

    @field_validator("upcoming_timeframe")
    @classmethod
    def validate_timeframe(cls, v: str | None) -> str | None:
        if v is None:
            return None
        valid_values = {e.value for e in UpcomingTimeframe}
        if v not in valid_values:
            raise ValueError(f"מסגרת זמן לא תקינה. ערכים אפשריים: {', '.join(valid_values)}")
        return v

    @model_validator(mode="after")
    def validate_future_only_requires_all_timeframe(self) -> "DriverSearchSettingsUpdate":
        """חוק עסקי: future_only_enabled=True רק אם upcoming_timeframe='all'.
        בודק רק כששני השדות סופקו באותו עדכון.
        הערה: ולידציית future_only_start_time מבוצעת ב-validate_against_existing
        כי בעדכון חלקי הערך עשוי להיות קיים ב-DB."""
        if self.future_only_enabled is True and self.upcoming_timeframe is not None:
            if self.upcoming_timeframe != UpcomingTimeframe.ALL.value:
                raise ValueError(
                    "מצב 'עתידי בלבד' זמין רק כאשר מסגרת הזמן היא 'הכל'"
                )
        return self

    def validate_against_existing(
        self,
        existing_future_only_enabled: bool,
        existing_upcoming_timeframe: str,
        existing_future_only_start_time: time | None = None,
    ) -> None:
        """ולידציה צולבת מול ערכים קיימים ב-DB — חובה לקרוא לפני apply של עדכון חלקי.

        בודק את התוצאה הסופית (שדה חדש + שדה ישן) מול החוקים העסקיים:
        1. future_only_enabled=True רק אם upcoming_timeframe='all'
        2. future_only_enabled=True חייב לכלול future_only_start_time
        """
        # דילוג כשאף שדה רלוונטי לא עודכן — מונע חסימת עדכונים לא קשורים
        if (
            self.future_only_enabled is None
            and self.upcoming_timeframe is None
            and self.future_only_start_time is None
        ):
            return

        # חשב את הערכים הסופיים לאחר מיזוג העדכון
        final_future_only = (
            self.future_only_enabled
            if self.future_only_enabled is not None
            else existing_future_only_enabled
        )
        final_timeframe = (
            self.upcoming_timeframe
            if self.upcoming_timeframe is not None
            else existing_upcoming_timeframe
        )
        final_start_time = (
            self.future_only_start_time
            if self.future_only_start_time is not None
            else existing_future_only_start_time
        )

        if final_future_only is True:
            if final_timeframe != UpcomingTimeframe.ALL.value:
                raise ValueError(
                    "מצב 'עתידי בלבד' זמין רק כאשר מסגרת הזמן היא 'הכל'"
                )
            if final_start_time is None:
                raise ValueError(
                    "חובה לציין שעת התחלה כאשר מצב 'עתידי בלבד' מופעל"
                )


class DriverSearchCreate(BaseModel):
    """סכמת יצירת חיפוש נהג"""

    origin_city: str
    destination_city: str
    is_area_search: bool = False
    latitude: float | None = None
    longitude: float | None = None

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, v: float | None) -> float | None:
        if v is not None and not (-90 <= v <= 90):
            raise ValueError("latitude חייב להיות בטווח 90- עד 90")
        return v

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, v: float | None) -> float | None:
        if v is not None and not (-180 <= v <= 180):
            raise ValueError("longitude חייב להיות בטווח 180- עד 180")
        return v

    @field_validator("origin_city")
    @classmethod
    def validate_origin_city(cls, v: str) -> str:
        """עיר מוצא — מותרת כמחרוזת ריקה (חיפוש ליעד בלבד)"""
        v = v.strip()
        if not v:
            return ""
        if len(v) > 100:
            raise ValueError("שם עיר לא יכול לחרוג מ-100 תווים")
        is_safe, pattern = TextSanitizer.check_for_injection(v)
        if not is_safe:
            raise ValueError("שם עיר מכיל תוכן לא תקין")
        return TextSanitizer.sanitize(v, max_length=100)

    @field_validator("destination_city")
    @classmethod
    def validate_destination_city(cls, v: str) -> str:
        """עיר יעד — שדה חובה"""
        v = v.strip()
        if not v:
            raise ValueError("שם עיר יעד הוא שדה חובה")
        if len(v) > 100:
            raise ValueError("שם עיר לא יכול לחרוג מ-100 תווים")
        is_safe, pattern = TextSanitizer.check_for_injection(v)
        if not is_safe:
            raise ValueError("שם עיר מכיל תוכן לא תקין")
        return TextSanitizer.sanitize(v, max_length=100)

    @model_validator(mode="after")
    def validate_area_search_coordinates(self) -> "DriverSearchCreate":
        """חיפוש מסלול (לא אזורי) אסור עם קואורדינטות.
        חיפוש אזורי עם קואורדינטות — תקין (GPS). חיפוש אזורי ללא — גם תקין (טקסט).
        קואורדינטה חלקית (אחת בלי השנייה) — תמיד שגיאה."""
        has_lat = self.latitude is not None
        has_lng = self.longitude is not None
        if has_lat != has_lng:
            raise ValueError("חובה לספק גם latitude וגם longitude, או אף אחד מהם")
        if not self.is_area_search:
            if has_lat or has_lng:
                raise ValueError("חיפוש מסלול לא יכול לכלול קואורדינטות")
        return self
