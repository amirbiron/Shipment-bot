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
    WHATSAPP_COURIER_GROUP_ID: Optional[str] = None

    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_ADMIN_CHAT_ID: Optional[str] = None  # Admin chat for notifications

    # Credit settings
    DEFAULT_CREDIT_LIMIT: float = -500.0  # Minimum balance allowed (500₪ credit)
    DELIVERY_FEE: float = 10.0  # Fee per delivery

    # File Upload
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
