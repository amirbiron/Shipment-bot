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
    summary="Create a new delivery",
    description="Creates a new delivery request with pickup and dropoff addresses.",
    responses={
        200: {"description": "Delivery created successfully"},
        422: {"description": "Validation error in request data"}
    },
    tags=["Deliveries"]
)
async def create_delivery(
    delivery_data: DeliveryCreate,
    db: AsyncSession = Depends(get_db)
) -> DeliveryResponse:
    """
    Create a new delivery request.

    - **sender_id**: ID of the sender user
    - **pickup_address**: Full address for pickup
    - **dropoff_address**: Full address for delivery
    - **fee**: Delivery fee (default: 10.0)
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
    summary="Get all open deliveries",
    description="Returns a list of all deliveries with OPEN status that haven't been captured yet.",
    tags=["Deliveries"]
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
    summary="Get delivery by ID",
    description="Returns detailed information about a specific delivery.",
    responses={
        200: {"description": "Delivery found"},
        404: {"description": "Delivery not found"}
    },
    tags=["Deliveries"]
)
async def get_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db)
) -> DeliveryResponse:
    """
    Get a specific delivery by its ID.

    - **delivery_id**: The unique identifier of the delivery
    """
    service = DeliveryService(db)
    delivery = await service.get_delivery(delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return delivery


@router.post(
    "/{delivery_id}/capture",
    response_model=CaptureResponse,
    summary="Capture a delivery",
    description="Assign a courier to capture and handle a delivery. This is an atomic operation.",
    responses={
        200: {"description": "Delivery captured successfully"},
        400: {"description": "Cannot capture delivery (already captured, insufficient credit, etc.)"},
        404: {"description": "Delivery not found"},
        500: {"description": "Server error during capture"}
    },
    tags=["Deliveries"]
)
async def capture_delivery(
    delivery_id: int,
    capture_data: CaptureRequest,
    db: AsyncSession = Depends(get_db)
) -> CaptureResponse:
    """
    Capture a delivery for a courier.

    This operation is atomic and includes:
    - Checking courier credit availability
    - Deducting delivery fee from courier wallet
    - Assigning courier to delivery

    - **delivery_id**: The delivery to capture
    - **courier_id**: The courier capturing the delivery
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
    summary="Mark delivery as delivered",
    description="Mark a captured delivery as delivered by the courier.",
    responses={
        200: {"description": "Delivery marked as delivered"},
        400: {"description": "Cannot mark as delivered (invalid status)"}
    },
    tags=["Deliveries"]
)
async def mark_delivered(
    delivery_id: int,
    db: AsyncSession = Depends(get_db)
) -> DeliverResponse:
    """
    Mark a delivery as completed.

    - **delivery_id**: The delivery to mark as delivered
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
    summary="Cancel a delivery",
    description="Cancel an open delivery that hasn't been captured yet.",
    responses={
        200: {"description": "Delivery cancelled successfully"},
        400: {"description": "Cannot cancel delivery (already captured or delivered)"}
    },
    tags=["Deliveries"]
)
async def cancel_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db)
) -> CancelResponse:
    """
    Cancel a delivery.

    Only open deliveries that haven't been captured can be cancelled.

    - **delivery_id**: The delivery to cancel
    """
    logger.info("Cancel delivery request", extra_data={"delivery_id": delivery_id})
    service = DeliveryService(db)
    delivery = await service.cancel_delivery(delivery_id)
    if not delivery:
        raise HTTPException(status_code=400, detail="Cannot cancel delivery")
    return CancelResponse(success=True, message="Delivery cancelled")
