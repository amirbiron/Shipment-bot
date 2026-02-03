"""
Delivery API Routes
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator, field_serializer, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.capture_service import CaptureService, CaptureError
from app.core.logging import get_logger
from app.core.validation import (
    PhoneNumberValidator,
    AddressValidator,
    NameValidator,
    AmountValidator,
    TextSanitizer
)
from app.core.exceptions import ValidationException

logger = get_logger(__name__)

router = APIRouter()


class DeliveryCreate(BaseModel):
    """Schema for creating a new delivery with validation"""
    sender_id: int
    pickup_address: str
    dropoff_address: str
    pickup_contact_name: str | None = None
    pickup_contact_phone: str | None = None
    pickup_notes: str | None = None
    dropoff_contact_name: str | None = None
    dropoff_contact_phone: str | None = None
    dropoff_notes: str | None = None
    fee: float = 10.0

    @field_validator("pickup_address", "dropoff_address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        """Validate and normalize address"""
        is_valid, error = AddressValidator.validate(v)
        if not is_valid:
            raise ValueError(error)
        return AddressValidator.normalize(v)

    @field_validator("pickup_contact_phone", "dropoff_contact_phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        """Validate and normalize phone number"""
        if v is None:
            return None
        if not PhoneNumberValidator.validate(v):
            raise ValueError("Invalid phone number format")
        return PhoneNumberValidator.normalize(v)

    @field_validator("pickup_contact_name", "dropoff_contact_name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        """Validate contact name"""
        if v is None:
            return None
        is_valid, error = NameValidator.validate(v)
        if not is_valid:
            raise ValueError(error)
        return TextSanitizer.sanitize(v.strip(), max_length=100)

    @field_validator("pickup_notes", "dropoff_notes")
    @classmethod
    def sanitize_notes(cls, v: str | None) -> str | None:
        """Sanitize notes text"""
        if v is None:
            return None
        is_safe, pattern = TextSanitizer.check_for_injection(v)
        if not is_safe:
            raise ValueError(f"Invalid characters in notes")
        return TextSanitizer.sanitize(v, max_length=500)

    @field_validator("fee")
    @classmethod
    def validate_fee(cls, v: float) -> float:
        """Validate delivery fee"""
        is_valid, error = AmountValidator.validate(v, min_value=0.0, max_value=10000.0)
        if not is_valid:
            raise ValueError(error)
        return round(v, 2)


class DeliveryResponse(BaseModel):
    """Response schema for delivery data"""
    id: int
    sender_id: int
    pickup_address: str
    dropoff_address: str
    status: str
    courier_id: int | None
    fee: float

    model_config = {"from_attributes": True}

    @field_serializer("status")
    def serialize_status(self, v: str) -> str:
        # בטסטים מצפים לסטטוס בפורמט UPPERCASE (לדוגמה: OPEN)
        try:
            # אם הגיע Enum (למשל DeliveryStatus), לקחת את ה-value
            raw = getattr(v, "value", v)
        except Exception:
            raw = v
        return str(raw).upper()


class CaptureRequest(BaseModel):
    """Request schema for capturing a delivery"""
    courier_id: int


class CaptureResponse(BaseModel):
    """Response schema for capture operation"""
    success: bool
    message: str
    delivery: DeliveryResponse


class DeliverResponse(BaseModel):
    """Response schema for deliver operation"""
    success: bool
    delivery: DeliveryResponse


class CancelResponse(BaseModel):
    """Response schema for cancel operation"""
    success: bool
    message: str


@router.post(
    "/",
    response_model=DeliveryResponse,
    summary="יצירת משלוח חדש",
    description="יצירת בקשת משלוח חדשה עם כתובות איסוף ומסירה.",
    responses={
        200: {"description": "המשלוח נוצר בהצלחה"},
        422: {"description": "שגיאת ולידציה בנתוני הבקשה"},
    },
)
async def create_delivery(
    delivery_data: DeliveryCreate,
    db: AsyncSession = Depends(get_db)
) -> DeliveryResponse:
    """
    יצירת בקשת משלוח חדשה.

    - **sender_id**: מזהה השולח
    - **pickup_address**: כתובת איסוף מלאה
    - **dropoff_address**: כתובת מסירה מלאה
    - **fee**: עמלת משלוח (ברירת מחדל: 10.0)
    """
    logger.info(
        "Creating new delivery",
        extra_data={"sender_id": delivery_data.sender_id}
    )
    service = DeliveryService(db)
    delivery = await service.create_delivery(**delivery_data.model_dump())
    return delivery


@router.get(
    "/open",
    response_model=List[DeliveryResponse],
    summary="קבלת רשימת משלוחים פתוחים",
    description="מחזיר רשימה של כל המשלוחים עם סטטוס OPEN שטרם נתפסו.",
)
async def get_open_deliveries(
    db: AsyncSession = Depends(get_db)
) -> List[DeliveryResponse]:
    """Get all open deliveries available for capture."""
    service = DeliveryService(db)
    deliveries = await service.get_open_deliveries()
    return deliveries


@router.get(
    "/{delivery_id}",
    response_model=DeliveryResponse,
    summary="קבלת משלוח לפי מזהה",
    description="מחזיר מידע מפורט על משלוח ספציפי.",
    responses={
        200: {"description": "המשלוח נמצא"},
        404: {"description": "המשלוח לא נמצא"},
    },
)
async def get_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db)
) -> DeliveryResponse:
    """
    קבלת משלוח לפי מזהה.

    - **delivery_id**: מזהה ייחודי של המשלוח
    """
    service = DeliveryService(db)
    delivery = await service.get_delivery(delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return delivery


@router.post(
    "/{delivery_id}/capture",
    response_model=CaptureResponse,
    summary="תפיסת משלוח (שיבוץ שליח)",
    description="שיבוץ שליח למשלוח. פעולה אטומית הכוללת בדיקת אשראי וניכוי עמלה.",
    responses={
        200: {"description": "המשלוח נתפס בהצלחה"},
        400: {"description": "לא ניתן לתפוס משלוח (כבר נתפס, אין מספיק אשראי וכו')"},
        404: {"description": "המשלוח לא נמצא"},
        500: {"description": "שגיאת שרת בזמן תפיסת משלוח"},
    },
)
async def capture_delivery(
    delivery_id: int,
    capture_data: CaptureRequest,
    db: AsyncSession = Depends(get_db)
) -> CaptureResponse:
    """
    תפיסת משלוח עבור שליח.

    פעולה אטומית הכוללת:
    - בדיקת אשראי/יתרה זמינה לשליח
    - ניכוי עמלת משלוח מארנק השליח
    - שיבוץ השליח למשלוח

    - **delivery_id**: המשלוח לתפיסה
    - **courier_id**: מזהה השליח שתופס את המשלוח
    """
    logger.info(
        "Capture delivery request",
        extra_data={"delivery_id": delivery_id, "courier_id": capture_data.courier_id}
    )
    service = CaptureService(db)
    try:
        success, message, delivery = await service.capture_delivery(
            delivery_id=delivery_id,
            courier_id=capture_data.courier_id
        )
        if not success:
            raise HTTPException(status_code=400, detail=message)
        return CaptureResponse(
            success=True,
            message=message,
            delivery=DeliveryResponse.model_validate(delivery)
        )
    except CaptureError as e:
        logger.error(
            "Capture error",
            extra_data={"delivery_id": delivery_id, "error": str(e)}
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{delivery_id}/deliver",
    response_model=DeliverResponse,
    summary="סימון משלוח כנמסר",
    description="סימון משלוח שנתפס כמשלוח שנמסר/הושלם על ידי השליח.",
    responses={
        200: {"description": "המשלוח סומן כנמסר"},
        400: {"description": "לא ניתן לסמן כנמסר (סטטוס לא תקין)"},
    },
)
async def mark_delivered(
    delivery_id: int,
    db: AsyncSession = Depends(get_db)
) -> DeliverResponse:
    """
    סימון משלוח כהושלם.

    - **delivery_id**: מזהה המשלוח לסימון כנמסר
    """
    logger.info("Mark delivered request", extra_data={"delivery_id": delivery_id})
    service = DeliveryService(db)
    delivery = await service.mark_delivered(delivery_id)
    if not delivery:
        raise HTTPException(status_code=400, detail="Cannot mark as delivered")
    return DeliverResponse(
        success=True,
        delivery=DeliveryResponse.model_validate(delivery)
    )


@router.delete(
    "/{delivery_id}",
    response_model=CancelResponse,
    summary="ביטול משלוח",
    description="ביטול משלוח פתוח שטרם נתפס.",
    responses={
        200: {"description": "המשלוח בוטל בהצלחה"},
        400: {"description": "לא ניתן לבטל משלוח (כבר נתפס או נמסר)"},
    },
)
async def cancel_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db)
) -> CancelResponse:
    """
    ביטול משלוח.

    ניתן לבטל רק משלוחים פתוחים (OPEN) שטרם נתפסו.

    - **delivery_id**: מזהה המשלוח לביטול
    """
    logger.info("Cancel delivery request", extra_data={"delivery_id": delivery_id})
    service = DeliveryService(db)
    delivery = await service.cancel_delivery(delivery_id)
    if not delivery:
        raise HTTPException(status_code=400, detail="Cannot cancel delivery")
    return CancelResponse(success=True, message="Delivery cancelled")
