"""
Panel API Routes — פאנל ניהול תחנה בווב

כל ה-endpoints דורשים אימות JWT (get_current_station_owner).
"""
from fastapi import APIRouter

from app.api.routes.panel.auth import router as auth_router
from app.api.routes.panel.dashboard import router as dashboard_router
from app.api.routes.panel.dispatchers import router as dispatchers_router
from app.api.routes.panel.deliveries import router as deliveries_router
from app.api.routes.panel.wallet import router as wallet_router
from app.api.routes.panel.blacklist import router as blacklist_router
from app.api.routes.panel.reports import router as reports_router
from app.api.routes.panel.groups import router as groups_router
from app.api.routes.panel.owners import router as owners_router
from app.api.routes.panel.alerts import router as alerts_router
from app.api.routes.panel.settings import router as settings_router
from app.api.routes.panel.stations import router as stations_router
from app.api.routes.panel.auto_block import router as auto_block_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["Panel - אימות"])
router.include_router(dashboard_router, prefix="/dashboard", tags=["Panel - דשבורד"])
router.include_router(stations_router, prefix="/stations", tags=["Panel - מולטי-תחנה"])
router.include_router(owners_router, prefix="/owners", tags=["Panel - בעלים"])
router.include_router(dispatchers_router, prefix="/dispatchers", tags=["Panel - סדרנים"])
router.include_router(deliveries_router, prefix="/deliveries", tags=["Panel - משלוחים"])
router.include_router(wallet_router, prefix="/wallet", tags=["Panel - ארנק"])
router.include_router(blacklist_router, prefix="/blacklist", tags=["Panel - רשימה שחורה"])
router.include_router(reports_router, prefix="/reports", tags=["Panel - דוחות"])
router.include_router(groups_router, prefix="/groups", tags=["Panel - קבוצות"])
router.include_router(alerts_router, prefix="/alerts", tags=["Panel - התראות"])
router.include_router(settings_router, prefix="/settings", tags=["Panel - הגדרות"])
router.include_router(auto_block_router, prefix="/auto-block", tags=["Panel - חסימה אוטומטית"])
