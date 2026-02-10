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

    # CORS
    # Comma-separated list of allowed origins (e.g. "https://app.example.com,https://admin.example.com")
    # In production, leave empty to disable CORS entirely (recommended for server-to-server APIs).
    ALLOWED_ORIGINS: str = ""

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
    REDIS_PENDING_REJECTION_TTL: int = 300  # TTL בשניות לדחייה ממתינה (ברירת מחדל: 5 דקות)

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # WhatsApp Gateway
    WHATSAPP_GATEWAY_URL: str = "http://localhost:3000"
    WHATSAPP_ADMIN_GROUP_ID: Optional[str] = None  # קבוצת מנהלים - לסיכומי אישור/דחייה

    # מנהלים פרטיים - לשליחת כרטיסי נהג לאישור עם כפתורים
    TELEGRAM_ADMIN_CHAT_IDS: str = ""  # מזהי צ'אט פרטיים של מנהלים בטלגרם (מופרדים בפסיקים)
    WHATSAPP_ADMIN_NUMBERS: str = ""  # מספרי וואטסאפ פרטיים של מנהלים (מופרדים בפסיקים)

    # קישורים לתפריט הראשי [שלב 1]
    WHATSAPP_GROUP_LINK: str = ""  # קישור לקבוצת וואטסאפ להעלאת משלוח מהיר
    ADMIN_WHATSAPP_NUMBER: str = ""  # מספר וואטסאפ של המנהל הראשי לפנייה ישירה

    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_ADMIN_CHAT_ID: Optional[str] = None  # קבוצת מנהלים בטלגרם - לסיכומי אישור/דחייה

    # Credit settings
    DEFAULT_CREDIT_LIMIT: float = -500.0  # Minimum balance allowed (500₪ credit)
    DELIVERY_FEE: float = 10.0  # Fee per delivery

    # Outbox retry/backoff
    # Base delay is multiplied by 2**retry_count (capped by OUTBOX_MAX_BACKOFF_SECONDS)
    OUTBOX_RETRY_BASE_SECONDS: int = 30
    OUTBOX_MAX_BACKOFF_SECONDS: int = 3600  # 1 hour cap to prevent multi-day delays

    # WhatsApp Gateway retry settings
    # מספר ניסיונות מקסימלי לשליחת הודעה (כולל הניסיון הראשון)
    WHATSAPP_MAX_RETRIES: int = 3
    # קודי HTTP שנחשבים לשגיאות זמניות ומצדיקים retry (מופרדים בפסיקים)
    WHATSAPP_TRANSIENT_STATUS_CODES: str = "502,503,504,429"

    @field_validator("WHATSAPP_MAX_RETRIES", mode="after")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        """וידוא שמספר הניסיונות הוא לפחות 1 למניעת אובדן הודעות שקט"""
        if v < 1:
            raise ValueError("WHATSAPP_MAX_RETRIES must be at least 1")
        return v

    # File Upload
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
