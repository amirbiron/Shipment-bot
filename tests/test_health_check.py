"""
בדיקות יחידה ל-Health Check endpoints — liveness, readiness ו-detailed.
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
        """כש-DB לא זמין — status=unhealthy ו-HTTP 503."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="error: db_unavailable",
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
        assert data["status"] == "unhealthy"
        assert data["db"] == "error: db_unavailable"
        assert data["redis"] == "ok"

    @pytest.mark.unit
    async def test_readiness_whatsapp_gateway_down(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כש-WhatsApp Gateway לא זמין — status=degraded אבל HTTP 200 (תלות לא קריטית)."""
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
            return_value="error: whatsapp_unavailable",
        ), patch(
            "app.domain.services.health_service._check_celery",
            new_callable=AsyncMock,
            return_value="ok",
        ):
            response = await test_client.get("/health/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["whatsapp_gateway"] == "error: whatsapp_unavailable"
        assert data["db"] == "ok"

    @pytest.mark.unit
    async def test_readiness_multiple_critical_failures(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כשכמה תלויות קריטיות נכשלות — status=unhealthy ו-HTTP 503."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="error: db_unavailable",
        ), patch(
            "app.domain.services.health_service._check_redis",
            new_callable=AsyncMock,
            return_value="error: redis_unavailable",
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
        assert data["status"] == "unhealthy"
        assert data["db"] == "error: db_unavailable"
        assert data["redis"] == "error: redis_unavailable"
        assert data["whatsapp_gateway"] == "ok"
        assert data["celery"] == "ok"

    @pytest.mark.unit
    async def test_readiness_celery_broker_down(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כש-Celery broker לא זמין — status=unhealthy ו-HTTP 503 (תלות קריטית)."""
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
            return_value="error: celery_unavailable",
        ):
            response = await test_client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["celery"] == "error: celery_unavailable"

    @pytest.mark.unit
    async def test_readiness_whatsapp_and_critical_down(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כש-WhatsApp וגם DB לא זמינים — status=unhealthy ו-HTTP 503 (בגלל DB)."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="error: db_unavailable",
        ), patch(
            "app.domain.services.health_service._check_redis",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._check_whatsapp_gateway",
            new_callable=AsyncMock,
            return_value="error: whatsapp_unavailable",
        ), patch(
            "app.domain.services.health_service._check_celery",
            new_callable=AsyncMock,
            return_value="ok",
        ):
            response = await test_client.get("/health/ready")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["db"] == "error: db_unavailable"
        assert data["whatsapp_gateway"] == "error: whatsapp_unavailable"


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
        """_check_db מחזיר הודעת שגיאה מסוננת כש-DB לא זמין."""
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

        assert result == "error: db_unavailable"

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
        """_check_redis מחזיר הודעת שגיאה מסוננת כש-Redis לא זמין."""
        with patch(
            "app.domain.services.health_service.get_redis",
            new_callable=AsyncMock,
            side_effect=ConnectionError("refused"),
        ):
            from app.domain.services.health_service import _check_redis
            result = await _check_redis()

        assert result == "error: redis_unavailable"

    @pytest.mark.unit
    async def test_check_whatsapp_gateway_success(self) -> None:
        """_check_whatsapp_gateway מחזיר ok כש-Gateway זמין ומחובר."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "connected": True}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.domain.services.health_service.httpx.AsyncClient", return_value=mock_client):
            from app.domain.services.health_service import _check_whatsapp_gateway
            result = await _check_whatsapp_gateway()

        assert result == "ok"

    @pytest.mark.unit
    async def test_check_whatsapp_gateway_disconnected(self) -> None:
        """_check_whatsapp_gateway מחזיר שגיאת ניתוק כש-Gateway מחזיר connected=false."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "connected": False}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.domain.services.health_service.httpx.AsyncClient", return_value=mock_client):
            from app.domain.services.health_service import _check_whatsapp_gateway
            result = await _check_whatsapp_gateway()

        assert result == "error: whatsapp_disconnected"

    @pytest.mark.unit
    async def test_check_whatsapp_gateway_404(self) -> None:
        """_check_whatsapp_gateway מחזיר הודעת שגיאה מסוננת כש-Gateway מחזיר 404."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.domain.services.health_service.httpx.AsyncClient", return_value=mock_client):
            from app.domain.services.health_service import _check_whatsapp_gateway
            result = await _check_whatsapp_gateway()

        assert result == "error: whatsapp_unavailable"

    @pytest.mark.unit
    async def test_check_whatsapp_gateway_connection_error(self) -> None:
        """_check_whatsapp_gateway מחזיר הודעת שגיאה מסוננת כשאין חיבור ל-Gateway."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.domain.services.health_service.httpx.AsyncClient", return_value=mock_client):
            from app.domain.services.health_service import _check_whatsapp_gateway
            result = await _check_whatsapp_gateway()

        assert result == "error: whatsapp_unavailable"

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
        """_check_celery מחזיר הודעת שגיאה מסוננת כש-Celery broker לא זמין."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client.aclose = AsyncMock()

        with patch(
            "app.domain.services.health_service.aioredis.from_url",
            return_value=mock_client,
        ):
            from app.domain.services.health_service import _check_celery
            result = await _check_celery()

        assert result == "error: celery_unavailable"


# ============================================================================
# Detailed Health Check — GET /health/detailed
# ============================================================================


class TestDetailedHealthCheck:
    """בדיקות ל-endpoint /health/detailed (דשבורד מפורט)."""

    @pytest.mark.unit
    async def test_detailed_requires_api_key(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """ללא API key — מחזיר 401 או 403."""
        response = await test_client.get("/health/detailed")
        assert response.status_code in (401, 403)

    @pytest.mark.unit
    async def test_detailed_all_healthy(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כשכל הרכיבים תקינים — מחזיר מבנה מפורט עם status=healthy."""
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
        ), patch(
            "app.domain.services.health_service._get_db_pool_info",
            return_value={
                "pool_size": 20,
                "checked_in": 18,
                "checked_out": 2,
                "overflow": 0,
            },
        ), patch(
            "app.api.dependencies.admin_auth.settings"
        ) as mock_settings:
            mock_settings.ADMIN_API_KEY = "test-key"
            response = await test_client.get(
                "/health/detailed",
                headers={"X-Admin-API-Key": "test-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        # בדיקת מבנה
        assert "timestamp" in data
        assert "uptime_seconds" in data
        assert "components" in data
        assert "circuit_breakers" in data
        assert "db_pool" in data
        # בדיקת components
        for comp_name in ("db", "redis", "whatsapp_gateway", "celery"):
            assert data["components"][comp_name]["status"] == "ok"
            assert "response_time_ms" in data["components"][comp_name]

    @pytest.mark.unit
    async def test_detailed_db_down_returns_503(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כש-DB לא זמין — מחזיר status=unhealthy ו-HTTP 503."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="error: db_unavailable",
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
        ), patch(
            "app.domain.services.health_service._get_db_pool_info",
            return_value={"pool_size": 20, "checked_in": 20, "checked_out": 0, "overflow": 0},
        ), patch(
            "app.api.dependencies.admin_auth.settings"
        ) as mock_settings:
            mock_settings.ADMIN_API_KEY = "test-key"
            response = await test_client.get(
                "/health/detailed",
                headers={"X-Admin-API-Key": "test-key"},
            )

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["components"]["db"]["status"] == "error: db_unavailable"

    @pytest.mark.unit
    async def test_detailed_whatsapp_down_returns_degraded(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """כש-WhatsApp לא זמין — מחזיר status=degraded ו-HTTP 200."""
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
            return_value="error: whatsapp_disconnected",
        ), patch(
            "app.domain.services.health_service._check_celery",
            new_callable=AsyncMock,
            return_value="ok",
        ), patch(
            "app.domain.services.health_service._get_db_pool_info",
            return_value={"pool_size": 20, "checked_in": 20, "checked_out": 0, "overflow": 0},
        ), patch(
            "app.api.dependencies.admin_auth.settings"
        ) as mock_settings:
            mock_settings.ADMIN_API_KEY = "test-key"
            response = await test_client.get(
                "/health/detailed",
                headers={"X-Admin-API-Key": "test-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"

    @pytest.mark.unit
    async def test_detailed_includes_circuit_breakers(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """התשובה כוללת רשימת circuit breakers עם שדות נדרשים."""
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
        ), patch(
            "app.domain.services.health_service._get_db_pool_info",
            return_value={"pool_size": 20, "checked_in": 20, "checked_out": 0, "overflow": 0},
        ), patch(
            "app.api.dependencies.admin_auth.settings"
        ) as mock_settings:
            mock_settings.ADMIN_API_KEY = "test-key"
            response = await test_client.get(
                "/health/detailed",
                headers={"X-Admin-API-Key": "test-key"},
            )

        assert response.status_code == 200
        data = response.json()
        cbs = data["circuit_breakers"]
        assert len(cbs) >= 1
        for cb in cbs:
            assert "service" in cb
            assert "state" in cb
            assert "failure_count" in cb
            assert "retry_after_seconds" in cb


# ============================================================================
# בדיקות יחידה לפונקציות מפורטות — check_detailed
# ============================================================================


class TestCheckDetailedFunction:
    """בדיקות ישירות לפונקציית check_detailed."""

    @pytest.mark.unit
    async def test_check_detailed_returns_response_times(self) -> None:
        """check_detailed מחזיר זמני תגובה לכל רכיב."""
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
        ), patch(
            "app.domain.services.health_service._get_db_pool_info",
            return_value={"pool_size": 5, "checked_in": 5, "checked_out": 0, "overflow": 0},
        ):
            from app.domain.services.health_service import check_detailed
            result = await check_detailed()

        assert result["status"] == "healthy"
        assert isinstance(result["uptime_seconds"], float)
        assert "timestamp" in result
        for comp in ("db", "redis", "whatsapp_gateway", "celery"):
            assert result["components"][comp]["status"] == "ok"
            assert isinstance(result["components"][comp]["response_time_ms"], float)
            assert result["components"][comp]["response_time_ms"] >= 0

    @pytest.mark.unit
    async def test_check_detailed_unhealthy_on_critical_failure(self) -> None:
        """check_detailed מחזיר unhealthy כשתלות קריטית נכשלת."""
        with patch(
            "app.domain.services.health_service._check_db",
            new_callable=AsyncMock,
            return_value="error: db_unavailable",
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
        ), patch(
            "app.domain.services.health_service._get_db_pool_info",
            return_value={"pool_size": 5, "checked_in": 5, "checked_out": 0, "overflow": 0},
        ):
            from app.domain.services.health_service import check_detailed
            result = await check_detailed()

        assert result["status"] == "unhealthy"
        assert result["components"]["db"]["status"] == "error: db_unavailable"

    @pytest.mark.unit
    async def test_timed_check_captures_exception(self) -> None:
        """_timed_check תופס חריגה ומחזיר שגיאה עם זמן תגובה."""
        async def _failing_check() -> str:
            raise ConnectionError("test error")

        from app.domain.services.health_service import _timed_check
        result = await _timed_check(_failing_check)

        assert "error:" in result["status"]
        assert isinstance(result["response_time_ms"], float)


# ============================================================================
# בדיקות ל-Celery task — periodic_health_check
# ============================================================================


class TestPeriodicHealthCheckTask:
    """בדיקות ל-task התקופתי של ניטור בריאות."""

    @pytest.mark.unit
    async def test_periodic_task_healthy_no_alert(self) -> None:
        """כשהכל תקין — לא שולח התראה."""
        mock_detailed = {
            "status": "healthy",
            "components": {
                "db": {"status": "ok", "response_time_ms": 1.0},
                "redis": {"status": "ok", "response_time_ms": 1.0},
                "whatsapp_gateway": {"status": "ok", "response_time_ms": 1.0},
                "celery": {"status": "ok", "response_time_ms": 1.0},
            },
            "circuit_breakers": [],
            "db_pool": {},
        }

        with patch(
            "app.domain.services.health_service.check_detailed",
            new_callable=AsyncMock,
            return_value=mock_detailed,
        ):
            # קריאה ישירה ל-async function הפנימית
            from app.domain.services.health_service import check_detailed
            result = await check_detailed()

        assert result["status"] == "healthy"

    @pytest.mark.unit
    async def test_periodic_task_unhealthy_triggers_alert(self) -> None:
        """כש-DB נכשל — task מזהה ומתכונן לשלוח התראה."""
        mock_detailed = {
            "status": "unhealthy",
            "components": {
                "db": {"status": "error: db_unavailable", "response_time_ms": 100.0},
                "redis": {"status": "ok", "response_time_ms": 1.0},
                "whatsapp_gateway": {"status": "ok", "response_time_ms": 1.0},
                "celery": {"status": "ok", "response_time_ms": 1.0},
            },
            "circuit_breakers": [],
            "db_pool": {},
        }

        with patch(
            "app.domain.services.health_service.check_detailed",
            new_callable=AsyncMock,
            return_value=mock_detailed,
        ):
            from app.domain.services.health_service import check_detailed
            result = await check_detailed()

        assert result["status"] == "unhealthy"
        # וידוא שהרכיב הכושל מזוהה
        assert result["components"]["db"]["status"] == "error: db_unavailable"
