"""
Delivery API Routes
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.domain.services.delivery_service import DeliveryService
from app.domain.services.capture_service import CaptureService, CaptureError

router = APIRouter()


class DeliveryCreate(BaseModel):
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


class DeliveryResponse(BaseModel):
    id: int
    sender_id: int
    pickup_address: str
    dropoff_address: str
    status: str
    courier_id: int | None
    fee: float

    class Config:
        from_attributes = True


class CaptureRequest(BaseModel):
    courier_id: int


@router.post("/", response_model=DeliveryResponse)
async def create_delivery(
    delivery_data: DeliveryCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new delivery"""
    service = DeliveryService(db)
    delivery = await service.create_delivery(**delivery_data.model_dump())
    return delivery


@router.get("/open", response_model=List[DeliveryResponse])
async def get_open_deliveries(db: AsyncSession = Depends(get_db)):
    """Get all open deliveries"""
    service = DeliveryService(db)
    deliveries = await service.get_open_deliveries()
    return deliveries


@router.get("/{delivery_id}", response_model=DeliveryResponse)
async def get_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get delivery by ID"""
    service = DeliveryService(db)
    delivery = await service.get_delivery(delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return delivery


@router.post("/{delivery_id}/capture")
async def capture_delivery(
    delivery_id: int,
    capture_data: CaptureRequest,
    db: AsyncSession = Depends(get_db)
):
    """Capture a delivery (atomic operation)"""
    service = CaptureService(db)
    try:
        success, message, delivery = await service.capture_delivery(
            delivery_id=delivery_id,
            courier_id=capture_data.courier_id
        )
        if not success:
            raise HTTPException(status_code=400, detail=message)
        return {"success": True, "message": message, "delivery": DeliveryResponse.model_validate(delivery)}
    except CaptureError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{delivery_id}/deliver")
async def mark_delivered(
    delivery_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Mark delivery as delivered"""
    service = DeliveryService(db)
    delivery = await service.mark_delivered(delivery_id)
    if not delivery:
        raise HTTPException(status_code=400, detail="Cannot mark as delivered")
    return {"success": True, "delivery": DeliveryResponse.model_validate(delivery)}


@router.delete("/{delivery_id}")
async def cancel_delivery(
    delivery_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Cancel a delivery"""
    service = DeliveryService(db)
    delivery = await service.cancel_delivery(delivery_id)
    if not delivery:
        raise HTTPException(status_code=400, detail="Cannot cancel delivery")
    return {"success": True, "message": "Delivery cancelled"}
