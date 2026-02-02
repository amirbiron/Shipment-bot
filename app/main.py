"""
Shipment Bot - Main FastAPI Application
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Delivery dispatch bot system for WhatsApp and Telegram",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Setup middleware (correlation ID, request logging)
setup_middleware(app)
setup_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy"}
