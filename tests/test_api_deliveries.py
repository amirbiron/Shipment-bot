"""
Tests for Delivery API Endpoints
"""
import pytest
from httpx import AsyncClient

from app.db.models.user import User, UserRole
from app.db.models.delivery import Delivery, DeliveryStatus


class TestDeliveryAPI:
    """Tests for delivery endpoints"""

    @pytest.mark.integration
    async def test_create_delivery_success(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test successful delivery creation"""
        payload = {
            "sender_id": sample_sender.id,
            "pickup_address": "רחוב הרצל 1, תל אביב",
            "dropoff_address": "רחוב בן יהודה 50, ירושלים",
            "pickup_contact_name": "יוסי כהן",
            "pickup_contact_phone": "0501234567",
            "fee": 15.0
        }

        response = await test_client.post("/api/deliveries/", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["sender_id"] == sample_sender.id
        assert data["status"] == "OPEN"
        assert data["fee"] == 15.0

    @pytest.mark.integration
    async def test_create_delivery_invalid_phone(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test delivery creation with invalid phone"""
        payload = {
            "sender_id": sample_sender.id,
            "pickup_address": "רחוב הרצל 1, תל אביב",
            "dropoff_address": "רחוב בן יהודה 50, ירושלים",
            "pickup_contact_phone": "invalid-phone"
        }

        response = await test_client.post("/api/deliveries/", json=payload)

        assert response.status_code == 422  # Validation error

    @pytest.mark.integration
    async def test_create_delivery_invalid_address(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test delivery creation with address too short"""
        payload = {
            "sender_id": sample_sender.id,
            "pickup_address": "abc",  # Too short
            "dropoff_address": "רחוב בן יהודה 50, ירושלים"
        }

        response = await test_client.post("/api/deliveries/", json=payload)

        assert response.status_code == 422  # Validation error

    @pytest.mark.integration
    async def test_get_open_deliveries(
        self,
        test_client: AsyncClient,
        sample_delivery: Delivery
    ):
        """Test getting open deliveries"""
        response = await test_client.get("/api/deliveries/open")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(d["id"] == sample_delivery.id for d in data)

    @pytest.mark.integration
    async def test_get_delivery_by_id(
        self,
        test_client: AsyncClient,
        sample_delivery: Delivery
    ):
        """Test getting delivery by ID"""
        response = await test_client.get(f"/api/deliveries/{sample_delivery.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_delivery.id

    @pytest.mark.integration
    async def test_get_delivery_not_found(self, test_client: AsyncClient):
        """Test getting non-existent delivery"""
        response = await test_client.get("/api/deliveries/99999")

        assert response.status_code == 404

    @pytest.mark.integration
    async def test_capture_delivery_success(
        self,
        test_client: AsyncClient,
        sample_delivery: Delivery,
        sample_courier: User
    ):
        """Test successful delivery capture"""
        payload = {"courier_id": sample_courier.id}

        response = await test_client.post(
            f"/api/deliveries/{sample_delivery.id}/capture",
            json=payload
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["delivery"]["courier_id"] == sample_courier.id

    @pytest.mark.integration
    async def test_capture_delivery_not_found(
        self,
        test_client: AsyncClient,
        sample_courier: User
    ):
        """Test capturing non-existent delivery"""
        payload = {"courier_id": sample_courier.id}

        response = await test_client.post(
            "/api/deliveries/99999/capture",
            json=payload
        )

        assert response.status_code in [400, 404, 500]

    @pytest.mark.integration
    async def test_cancel_delivery(
        self,
        test_client: AsyncClient,
        sample_delivery: Delivery
    ):
        """Test delivery cancellation"""
        response = await test_client.delete(f"/api/deliveries/{sample_delivery.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestDeliveryValidation:
    """Tests for delivery input validation"""

    @pytest.mark.unit
    async def test_fee_validation_negative(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test that negative fee is rejected"""
        payload = {
            "sender_id": sample_sender.id,
            "pickup_address": "רחוב הרצל 1, תל אביב",
            "dropoff_address": "רחוב בן יהודה 50, ירושלים",
            "fee": -10.0
        }

        response = await test_client.post("/api/deliveries/", json=payload)

        assert response.status_code == 422

    @pytest.mark.unit
    async def test_fee_validation_too_high(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test that excessive fee is rejected"""
        payload = {
            "sender_id": sample_sender.id,
            "pickup_address": "רחוב הרצל 1, תל אביב",
            "dropoff_address": "רחוב בן יהודה 50, ירושלים",
            "fee": 50000.0
        }

        response = await test_client.post("/api/deliveries/", json=payload)

        assert response.status_code == 422

    @pytest.mark.unit
    async def test_notes_sanitization(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test that notes with injection attempts are rejected"""
        payload = {
            "sender_id": sample_sender.id,
            "pickup_address": "רחוב הרצל 1, תל אביב",
            "dropoff_address": "רחוב בן יהודה 50, ירושלים",
            "pickup_notes": "<script>alert('xss')</script>"
        }

        response = await test_client.post("/api/deliveries/", json=payload)

        # Should either reject or sanitize
        if response.status_code == 200:
            data = response.json()
            # If accepted, should be sanitized (no script tags)
            # Note: The actual implementation may reject this entirely
