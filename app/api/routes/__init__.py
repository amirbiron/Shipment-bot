"""
API Routes
"""
from fastapi import APIRouter

from app.api.routes.deliveries import router as deliveries_router
from app.api.routes.users import router as users_router
from app.api.routes.wallets import router as wallets_router
from app.api.routes.migrations import router as migrations_router
from app.api.webhooks.whatsapp import router as whatsapp_router
from app.api.webhooks.telegram import router as telegram_router

router = APIRouter()

router.include_router(deliveries_router, prefix="/deliveries", tags=["deliveries"])
router.include_router(users_router, prefix="/users", tags=["users"])
router.include_router(wallets_router, prefix="/wallets", tags=["wallets"])
router.include_router(migrations_router, prefix="/migrations", tags=["migrations"])
# Canonical webhook endpoints (documented)
router.include_router(whatsapp_router, prefix="/whatsapp", tags=["webhooks"])
router.include_router(telegram_router, prefix="/telegram", tags=["webhooks"])

# Backwards-compatible webhook endpoints
router.include_router(
    whatsapp_router,
    prefix="/webhooks/whatsapp",
    tags=["webhooks"],
    include_in_schema=False
)
router.include_router(
    telegram_router,
    prefix="/webhooks/telegram",
    tags=["webhooks"],
    include_in_schema=False
)
