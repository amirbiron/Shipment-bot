"""
בדיקות יחידה ל-Health Check endpoints — liveness ו-readiness.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx


# ============================================================================
# Liveness Probe — GET /health
# ============================================================================


class TestLivenessProbe:
    """בדיקות ל-endpoint /health (liveness probe)."""

    @pytest.mark.unit
    async def test_liveness_returns_healthy(self, test_client: httpx.AsyncClient) -> None:
        """liveness probe מחזיר status=healthy תמיד."""
        response = await test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


# ============================================================================
# Readiness Probe — GET /health/ready
# ============================================================================


class TestReadinessProbe:
    """בדיקות ל-endpoint /health/ready (readiness probe)."""

    @pytest.mark.unit
    async def test_readiness_all_healthy(self, test_client: httpx.AsyncClient) -> None:
        """כשכל התלויות תקינות — status=healthy ו-HTTP 200."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_redis",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_whatsapp_gateway",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_celery",
            new_callable=AsyncMock,
            return_value="ok",
        ):
            response = await test_client.get("/health/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["db"] == "ok"
        assert data["redis"] == "ok"
        assert data["whatsapp_gateway"] == "ok"
        assert data["celery"] == "ok"

    @pytest.mark.unit
    async def test_readiness_db_down(self, test_client: httpx.AsyncClient) -> None:
        """כש-DB לא זמין — status=degraded ו-HTTP 503."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="error: connection refused",
        ), patch(
            "app.domain.services.health_service._check_redis",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_whatsapp_gateway",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_celery",
            new_callable=AsyncMock,
            return_value="ok",
        ):
            response = await test_client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert "error" in data["db"]
        assert data["redis"] == "ok"

    @pytest.mark.unit
    async def test_readiness_whatsapp_gateway_down(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כש-WhatsApp Gateway מחזיר 404 — status=degraded ו-HTTP 503."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_redis",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_whatsapp_gateway",
            new_callable=AsyncMock,
            return_value="error: 404",
        ), patch(
            "app.domain.services.health_service._check_celery",
            new_callable=AsyncMock,
            return_value="ok",
        ):
            response = await test_client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["whatsapp_gateway"] == "error: 404"
        assert data["db"] == "ok"

    @pytest.mark.unit
    async def test_readiness_multiple_failures(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כשכמה תלויות נכשלות — status=degraded ופירוט לכל תלות."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="error: timeout",
        ), patch(
            "app.domain.services.health_service._check_redis",
            new_callable=AsyncMock,
            return_value="error: connection refused",
        ), patch(
            "app.domain.services.health_service._check_whatsapp_gateway",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_celery",
            new_callable=AsyncMock,
            return_value="ok",
        ):
            response = await test_client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert "error" in data["db"]
        assert "error" in data["redis"]
        assert data["whatsapp_gateway"] == "ok"
        assert data["celery"] == "ok"

    @pytest.mark.unit
    async def test_readiness_celery_broker_down(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כש-Celery broker לא זמין — status=degraded."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_redis",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_whatsapp_gateway",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_celery",
            new_callable=AsyncMock,
            return_value="error: connection refused",
        ):
            response = await test_client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert "error" in data["celery"]


# ============================================================================
# בדיקות יחידה לפונקציות בדיקה פנימיות
# ============================================================================


class TestHealthCheckFunctions:
    """בדיקות ישירות לפונקציות הבדיקה בשירות."""

    @pytest.mark.unit
    async def test_check_db_success(self) -> None:
        """_check_db מחזיר ok כש-DB זמין."""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.domain.services.health_service.AsyncSessionLocal",
            return_value=mock_session,
        ):
            from app.domain.services.health_service import _check_db
            result = await _check_db()

        assert result == "ok"

    @pytest.mark.unit
    async def test_check_db_failure(self) -> None:
        """_check_db מחזיר error כש-DB לא זמין."""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=ConnectionError("refused"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.domain.services.health_service.AsyncSessionLocal",
            return_value=mock_session,
        ):
            from app.domain.services.health_service import _check_db
            result = await _check_db()

        assert result.startswith("error:")

    @pytest.mark.unit
    async def test_check_redis_success(self) -> None:
        """_check_redis מחזיר ok כש-Redis זמין."""
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        with patch(
            "app.domain.services.health_service.get_redis",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            from app.domain.services.health_service import _check_redis
            result = await _check_redis()

        assert result == "ok"

    @pytest.mark.unit
    async def test_check_redis_failure(self) -> None:
        """_check_redis מחזיר error כש-Redis לא זמין."""
        with patch(
            "app.domain.services.health_service.get_redis",
            new_callable=AsyncMock,
            side_effect=ConnectionError("refused"),
        ):
            from app.domain.services.health_service import _check_redis
            result = await _check_redis()

        assert result.startswith("error:")

    @pytest.mark.unit
    async def test_check_whatsapp_gateway_success(self) -> None:
        """_check_whatsapp_gateway מחזיר ok כש-Gateway זמין."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.domain.services.health_service.httpx.AsyncClient", return_value=mock_client):
            from app.domain.services.health_service import _check_whatsapp_gateway
            result = await _check_whatsapp_gateway()

        assert result == "ok"

    @pytest.mark.unit
    async def test_check_whatsapp_gateway_404(self) -> None:
        """_check_whatsapp_gateway מחזיר error: 404 כש-Gateway מחזיר 404."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.domain.services.health_service.httpx.AsyncClient", return_value=mock_client):
            from app.domain.services.health_service import _check_whatsapp_gateway
            result = await _check_whatsapp_gateway()

        assert result == "error: 404"

    @pytest.mark.unit
    async def test_check_whatsapp_gateway_connection_error(self) -> None:
        """_check_whatsapp_gateway מחזיר error כשאין חיבור ל-Gateway."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.domain.services.health_service.httpx.AsyncClient", return_value=mock_client):
            from app.domain.services.health_service import _check_whatsapp_gateway
            result = await _check_whatsapp_gateway()

        assert result.startswith("error:")

    @pytest.mark.unit
    async def test_check_celery_success(self) -> None:
        """_check_celery מחזיר ok כש-Celery broker זמין."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.aclose = AsyncMock()

        with patch(
            "app.domain.services.health_service.aioredis.from_url",
            return_value=mock_client,
        ):
            from app.domain.services.health_service import _check_celery
            result = await _check_celery()

        assert result == "ok"

    @pytest.mark.unit
    async def test_check_celery_failure(self) -> None:
        """_check_celery מחזיר error כש-Celery broker לא זמין."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client.aclose = AsyncMock()

        with patch(
            "app.domain.services.health_service.aioredis.from_url",
            return_value=mock_client,
        ):
            from app.domain.services.health_service import _check_celery
            result = await _check_celery()

        assert result.startswith("error:")
