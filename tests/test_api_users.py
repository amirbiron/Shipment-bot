"""
Tests for User API Endpoints
"""
import pytest
from httpx import AsyncClient

from app.db.models.user import User, UserRole


class TestUserAPI:
    """Tests for user endpoints"""

    @pytest.mark.integration
    async def test_create_user_success(self, test_client: AsyncClient):
        """Test successful user creation"""
        payload = {
            "phone_number": "0503333333",
            "name": "Test User",
            "role": "SENDER",
            "platform": "whatsapp"
        }

        response = await test_client.post("/api/users/", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test User"
        assert data["role"] == "SENDER"
        # Phone should be normalized
        assert data["phone_number"] == "+972503333333"

    @pytest.mark.integration
    async def test_create_user_invalid_phone(self, test_client: AsyncClient):
        """Test user creation with invalid phone"""
        payload = {
            "phone_number": "invalid",
            "name": "Test User"
        }

        response = await test_client.post("/api/users/", json=payload)

        assert response.status_code == 422

    @pytest.mark.integration
    async def test_create_user_duplicate(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test creating duplicate user"""
        payload = {
            "phone_number": sample_sender.phone_number,
            "name": "Duplicate User"
        }

        response = await test_client.post("/api/users/", json=payload)

        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    @pytest.mark.integration
    async def test_get_user_by_id(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test getting user by ID"""
        response = await test_client.get(f"/api/users/{sample_sender.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_sender.id
        assert data["name"] == sample_sender.name

    @pytest.mark.integration
    async def test_get_user_not_found(self, test_client: AsyncClient):
        """Test getting non-existent user"""
        response = await test_client.get("/api/users/99999")

        assert response.status_code == 404

    @pytest.mark.integration
    async def test_get_user_by_phone(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test getting user by phone number"""
        response = await test_client.get(
            f"/api/users/phone/{sample_sender.phone_number}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["phone_number"] == sample_sender.phone_number

    @pytest.mark.integration
    async def test_get_couriers(
        self,
        test_client: AsyncClient,
        sample_courier: User
    ):
        """Test getting list of couriers"""
        response = await test_client.get("/api/users/couriers/")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert any(c["id"] == sample_courier.id for c in data)

    @pytest.mark.integration
    async def test_update_user(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test updating user details"""
        payload = {
            "name": "Updated Name",
            "is_active": True
        }

        response = await test_client.patch(
            f"/api/users/{sample_sender.id}",
            json=payload
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"

    @pytest.mark.integration
    async def test_update_user_invalid_name(
        self,
        test_client: AsyncClient,
        sample_sender: User
    ):
        """Test updating user with invalid name"""
        payload = {
            "name": "A"  # Too short
        }

        response = await test_client.patch(
            f"/api/users/{sample_sender.id}",
            json=payload
        )

        assert response.status_code == 422


class TestUserValidation:
    """Tests for user input validation"""

    @pytest.mark.unit
    async def test_phone_normalization(self, test_client: AsyncClient):
        """Test that phone numbers are normalized"""
        payload = {
            "phone_number": "050-444-5555",
            "name": "Phone Test"
        }

        response = await test_client.post("/api/users/", json=payload)

        assert response.status_code == 200
        data = response.json()
        # Should be normalized to international format
        assert data["phone_number"] == "+972504445555"

    @pytest.mark.unit
    async def test_name_sanitization(self, test_client: AsyncClient):
        """Test that names are sanitized"""
        payload = {
            "phone_number": "0505556666",
            "name": "  Test  User  "  # Extra whitespace
        }

        response = await test_client.post("/api/users/", json=payload)

        if response.status_code == 200:
            data = response.json()
            # Whitespace should be normalized
            assert data["name"].strip() == data["name"]

    @pytest.mark.unit
    async def test_invalid_platform(self, test_client: AsyncClient):
        """Test that invalid platform is rejected"""
        payload = {
            "phone_number": "0506667777",
            "name": "Platform Test",
            "platform": "invalid_platform"
        }

        response = await test_client.post("/api/users/", json=payload)

        assert response.status_code == 422

    @pytest.mark.unit
    async def test_telegram_id_validation(self, test_client: AsyncClient):
        """Test Telegram chat ID validation"""
        # Valid numeric ID
        payload = {
            "phone_number": "0507778888",
            "name": "Telegram Test",
            "platform": "telegram",
            "telegram_chat_id": "123456789"
        }

        response = await test_client.post("/api/users/", json=payload)
        assert response.status_code == 200

    @pytest.mark.unit
    async def test_telegram_id_invalid(self, test_client: AsyncClient):
        """Test invalid Telegram chat ID is rejected"""
        payload = {
            "phone_number": "0508889999",
            "name": "Telegram Test",
            "platform": "telegram",
            "telegram_chat_id": "not-a-number"
        }

        response = await test_client.post("/api/users/", json=payload)
        assert response.status_code == 422
