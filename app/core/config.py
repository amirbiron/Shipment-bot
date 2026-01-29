"""
Application Configuration
"""
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    APP_NAME: str = "Shipment Bot"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/shipment_bot"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def convert_database_url(cls, v: str) -> str:
        """Convert postgres:// or postgresql:// to postgresql+asyncpg:// for async support"""
        if v:
            # Render uses postgres:// but SQLAlchemy needs postgresql+asyncpg://
            if v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+asyncpg://", 1)
            if v.startswith("postgresql://"):
                return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # WhatsApp Gateway
    WHATSAPP_GATEWAY_URL: str = "http://localhost:3000"

    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None

    # Credit settings
    DEFAULT_CREDIT_LIMIT: float = -100.0  # Minimum balance allowed
    DELIVERY_FEE: float = 10.0  # Fee per delivery

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
