"""
Shipment Bot - Main FastAPI Application
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.middleware import setup_middleware, setup_exception_handlers
from app.api.routes import router as api_router
from app.db.database import engine, Base

# Setup logging before anything else
setup_logging(
    level="DEBUG" if settings.DEBUG else "INFO",
    json_format=not settings.DEBUG,
    app_name=settings.APP_NAME
)

logger = get_logger(__name__)


def _parse_allowed_origins(raw: str) -> list[str]:
    """Parse comma-separated CORS origins string into a clean list."""
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Delivery dispatch bot system for WhatsApp and Telegram",
    docs_url="/docs",
    redoc_url=None,  # משתמשים ב-endpoint מותאם במקום
    openapi_url="/openapi.json"
)

# Setup middleware (correlation ID, request logging)
setup_middleware(app)
setup_exception_handlers(app)

allowed_origins = _parse_allowed_origins(settings.ALLOWED_ORIGINS)

# Safe dev default to support local frontend development without opening CORS in production.
if not allowed_origins and settings.DEBUG:
    allowed_origins = [
        "http://localhost",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )

app.include_router(api_router, prefix="/api")


@app.on_event("startup")
async def startup() -> None:
    """Initialize database tables on startup"""
    logger.info("Starting application", extra_data={"app_name": settings.APP_NAME})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized")


@app.on_event("shutdown")
async def shutdown() -> None:
    """Cleanup on shutdown"""
    logger.info("Shutting down application")
    # סגירת חיבורי מסד הנתונים למניעת connection pool exhaustion
    await engine.dispose()
    logger.info("Database connections disposed")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy"}


@app.get("/redoc", include_in_schema=False)
async def redoc_html():
    """
    ReDoc documentation endpoint עם CDN מותאם.

    משתמש ב-unpkg במקום jsdelivr כדי למנוע בעיות טעינה
    שגורמות לדף ריק.
    """
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - ReDoc",
        redoc_js_url="https://unpkg.com/redoc@latest/bundles/redoc.standalone.js",
    )
