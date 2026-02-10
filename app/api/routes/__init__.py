"""
API Routes
"""
from fastapi import APIRouter

from app.api.routes.deliveries import router as deliveries_router
from app.api.routes.users import router as users_router
from app.api.routes.wallets import router as wallets_router
from app.api.routes.stations import router as stations_router
from app.api.routes.migrations import router as migrations_router
from app.api.routes.panel import router as panel_router
from app.api.webhooks.whatsapp import router as whatsapp_router
from app.api.webhooks.telegram import router as telegram_router

router = APIRouter()

router.include_router(deliveries_router, prefix="/deliveries", tags=["Deliveries"])
router.include_router(users_router, prefix="/users", tags=["Users"])
router.include_router(wallets_router, prefix="/wallets", tags=["Wallets"])
router.include_router(stations_router, prefix="/stations", tags=["Stations"])
router.include_router(migrations_router, prefix="/migrations", tags=["Migrations"])
router.include_router(panel_router, prefix="/panel", tags=["Panel"])
# Webhook endpoints
router.include_router(whatsapp_router, prefix="/whatsapp", tags=["Webhooks"])
router.include_router(telegram_router, prefix="/telegram", tags=["Webhooks"])
