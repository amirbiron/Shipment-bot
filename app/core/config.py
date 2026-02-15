"""
Application Configuration
"""
from pydantic_settings import BaseSettings
from pydantic import field_validator, model_validator
from typing import Optional

# ספקי WhatsApp נתמכים
VALID_WHATSAPP_PROVIDERS = {"wppconnect", "pywa"}


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

    # WhatsApp Gateway (WPPConnect — Arm B)
    WHATSAPP_GATEWAY_URL: str = "http://localhost:3000"
    # סוג ספק WhatsApp: "wppconnect" (ברירת מחדל) או "pywa" (Cloud API)
    WHATSAPP_PROVIDER: str = "wppconnect"

    # WhatsApp Cloud API (pywa — Arm A)
    WHATSAPP_CLOUD_API_TOKEN: str = ""            # Meta access token
    WHATSAPP_CLOUD_API_PHONE_ID: str = ""         # Phone number ID מ-Meta dashboard
    WHATSAPP_CLOUD_API_PHONE_NUMBER: str = ""     # מספר הטלפון הרשמי ליצירת wa.me/ links (ללא +)
    WHATSAPP_CLOUD_API_APP_SECRET: str = ""       # App secret לאימות webhook signatures
    WHATSAPP_CLOUD_API_VERIFY_TOKEN: str = ""     # Verify token לאימות webhook registration

    # מצב היברידי: Cloud API לפרטי, WPPConnect לקבוצות
    WHATSAPP_HYBRID_MODE: bool = False

    @field_validator("WHATSAPP_PROVIDER", mode="before")
    @classmethod
    def validate_whatsapp_provider(cls, v: str) -> str:
        """נרמול ובדיקת ערכים מותרים — נכשל מהר בהפעלה ולא בזמן ריצה"""
        v = v.strip().lower()
        if v not in VALID_WHATSAPP_PROVIDERS:
            raise ValueError(
                f"WHATSAPP_PROVIDER='{v}' לא נתמך. "
                f"ערכים מותרים: {', '.join(sorted(VALID_WHATSAPP_PROVIDERS))}"
            )
        return v

    @field_validator("WHATSAPP_GATEWAY_URL", mode="before")
    @classmethod
    def normalize_gateway_url(cls, v: str) -> str:
        """Render fromService hostport מחזיר host:port בלבד — מוסיף http:// אם חסר"""
        if v and not v.startswith("http"):
            v = f"http://{v}"
        return v.rstrip("/")
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
    TELEGRAM_WEBHOOK_SECRET_TOKEN: str = ""  # אימות webhook — openssl rand -hex 32

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

    # Rate limiting — webhooks
    WEBHOOK_RATE_LIMIT_MAX_REQUESTS: int = 100  # מספר בקשות מקסימלי לכל IP
    WEBHOOK_RATE_LIMIT_WINDOW_SECONDS: int = 60  # חלון זמן בשניות

    # Admin debug endpoints — מפתח API לגישה ל-endpoints דיאגנוסטיים
    ADMIN_API_KEY: str = ""  # openssl rand -hex 32

    # JWT — פאנל ווב
    JWT_SECRET_KEY: str = ""  # חובה בפרודקשן — openssl rand -hex 32
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 שעות
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30  # חודש — refresh token ב-Redis
    OTP_EXPIRE_SECONDS: int = 300  # 5 דקות

    @field_validator("REFRESH_TOKEN_EXPIRE_DAYS", mode="after")
    @classmethod
    def validate_refresh_token_expire_days(cls, v: int) -> int:
        """ערך חייב להיות חיובי — אחרת setex ב-Redis יכשל ותהליך login ישבר"""
        if v < 1:
            raise ValueError("REFRESH_TOKEN_EXPIRE_DAYS must be at least 1")
        return v

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """ולידציות חוצות-שדות לסביבת פרודקשן.

        1. JWT_SECRET_KEY ריק בפרודקשן — זורק ValueError שעוצר את ההפעלה.
        2. TELEGRAM_WEBHOOK_SECRET_TOKEN ריק — אזהרה (webhook לא מאומת).
        3. DEBUG=True עם DB חיצוני — אזהרה שייתכן ששכחו לכבות DEBUG.
        """
        import warnings

        # --- JWT_SECRET_KEY ---
        if not self.JWT_SECRET_KEY:
            if not self.DEBUG:
                raise ValueError(
                    "JWT_SECRET_KEY ריק בסביבת פרודקשן (DEBUG=False) — "
                    "אי אפשר להפעיל את המערכת בלי מפתח הצפנה. "
                    "הגדר: export JWT_SECRET_KEY=$(openssl rand -hex 32)"
                )
            warnings.warn(
                "JWT_SECRET_KEY ריק — הפאנל לא יעבוד. הגדר בסביבת הייצור: openssl rand -hex 32",
                stacklevel=2,
            )

        # --- TELEGRAM_WEBHOOK_SECRET_TOKEN ---
        if not self.TELEGRAM_WEBHOOK_SECRET_TOKEN and self.TELEGRAM_BOT_TOKEN:
            warnings.warn(
                "TELEGRAM_WEBHOOK_SECRET_TOKEN ריק — webhook של טלגרם לא מאומת. "
                "הגדר: export TELEGRAM_WEBHOOK_SECRET_TOKEN=$(openssl rand -hex 32) "
                "ועדכן את ה-secret_token בקריאת setWebhook.",
                stacklevel=2,
            )

        # --- Cloud API credentials: נדרש גם ב-HYBRID_MODE וגם ב-PROVIDER=pywa ---
        _needs_cloud_api = self.WHATSAPP_HYBRID_MODE or self.WHATSAPP_PROVIDER == "pywa"
        if _needs_cloud_api:
            _missing = []
            if not self.WHATSAPP_CLOUD_API_TOKEN:
                _missing.append("WHATSAPP_CLOUD_API_TOKEN")
            if not self.WHATSAPP_CLOUD_API_PHONE_ID:
                _missing.append("WHATSAPP_CLOUD_API_PHONE_ID")
            if not self.WHATSAPP_CLOUD_API_APP_SECRET:
                _missing.append("WHATSAPP_CLOUD_API_APP_SECRET")
            if _missing:
                _mode = "WHATSAPP_HYBRID_MODE=True" if self.WHATSAPP_HYBRID_MODE else "WHATSAPP_PROVIDER=pywa"
                raise ValueError(
                    f"{_mode} אבל חסרות הגדרות Cloud API: "
                    f"{', '.join(_missing)}"
                )

        # --- DEBUG + DB חיצוני = כנראה שכחו לכבות DEBUG ---
        _local_hosts = ("localhost", "127.0.0.1", "::1")
        _is_local_db = any(h in self.DATABASE_URL for h in _local_hosts) if self.DATABASE_URL else True
        if self.DEBUG and not _is_local_db:
            warnings.warn(
                "DEBUG=True עם DATABASE_URL חיצוני — ייתכן שמצב DEBUG פעיל בפרודקשן. "
                "כותרות אבטחה (HSTS, CSP) לא יוחלו. הגדר DEBUG=False בסביבת ייצור.",
                stacklevel=2,
            )

        return self

    # File Upload
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
