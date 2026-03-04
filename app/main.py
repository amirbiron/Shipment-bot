"""
Shipment Bot - Main FastAPI Application
"""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

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


_OPENAPI_TAGS = [
    {
        "name": "Deliveries",
        "description": "ניהול משלוחים: יצירה, צפייה, תפיסה (שיבוץ שליח), מסירה וביטול.",
    },
    {"name": "Users", "description": "ניהול משתמשים (שולחים ושליחים)."},
    {"name": "Wallets", "description": "ארנק שליחים: יתרה, היסטוריית תנועות ובדיקת אשראי."},
    {"name": "Webhooks", "description": "Webhook-ים לקבלת הודעות מ-WhatsApp ו-Telegram."},
    {"name": "Migrations", "description": "Endpoints פנימיים להרצת מיגרציות/התאמות DB."},
    {
        "name": "Admin Debug",
        "description": "כלי דיאגנוסטיקה לאדמין: circuit breakers, הודעות כושלות, ומצב state machine של משתמשים.",
    },
]


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "מערכת בוט לניהול משלוחים עבור WhatsApp ו-Telegram. "
        "התיעוד מבוסס OpenAPI ומוצג ב-Swagger UI וב-ReDoc."
    ),
    docs_url=None,  # משתמשים ב-endpoint מותאם במקום
    redoc_url=None,  # משתמשים ב-endpoint מותאם במקום
    openapi_tags=_OPENAPI_TAGS,
    openapi_url="/openapi.json",
)

# Static assets for docs (RTL CSS, etc.)
_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

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


_STATIC_ASSET_EXTENSIONS = {
    ".js", ".css", ".html", ".json", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
}


class _SPAStaticFiles(StaticFiles):
    """StaticFiles עם SPA fallback — מחזיר index.html לנתיבי ניווט שלא תואמים קובץ סטטי."""

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        full_path, stat_result = super().lookup_path(path)
        if stat_result is None:
            ext = os.path.splitext(path)[1].lower()
            if ext not in _STATIC_ASSET_EXTENSIONS:
                # נתיב ניווט (לא נכס סטטי) — fallback ל-index.html
                return super().lookup_path("index.html")
        return full_path, stat_result


# הגשת Frontend של פאנל ניהול התחנה — SPA עם client-side routing
_PANEL_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _PANEL_DIR.exists():
    app.mount("/panel", _SPAStaticFiles(directory=_PANEL_DIR, html=True), name="panel")
else:
    logger.warning("frontend/dist לא נמצא — הפאנל לא יוגש", extra_data={"path": str(_PANEL_DIR)})


async def _register_telegram_webhook() -> None:
    """רישום webhook אוטומטי של טלגרם בעלייה — מבטיח שהטוקן ותואם ל-URL הנוכחי."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.info("TELEGRAM_BOT_TOKEN לא מוגדר — דילוג על רישום webhook")
        return

    # URL חיצוני: הגדרה מפורשת, או RENDER_EXTERNAL_URL שמסופק אוטומטית ב-Render
    base_url = settings.TELEGRAM_WEBHOOK_BASE_URL or os.environ.get("RENDER_EXTERNAL_URL", "")
    if not base_url:
        logger.warning(
            "TELEGRAM_WEBHOOK_BASE_URL ו-RENDER_EXTERNAL_URL לא מוגדרים — "
            "לא ניתן לרשום webhook אוטומטית. "
            "הגדר TELEGRAM_WEBHOOK_BASE_URL או רשום ידנית."
        )
        return

    webhook_url = f"{base_url.rstrip('/')}/api/telegram/webhook"
    secret_token = settings.TELEGRAM_WEBHOOK_SECRET_TOKEN

    payload: dict[str, str] = {"url": webhook_url}
    if secret_token:
        payload["secret_token"] = secret_token

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/setWebhook",
                data=payload,
            )
            result = response.json()
            if result.get("ok"):
                logger.info(
                    "Telegram webhook נרשם בהצלחה",
                    extra_data={"webhook_url": webhook_url},
                )
            else:
                logger.error(
                    "רישום Telegram webhook נכשל",
                    extra_data={"response": result, "webhook_url": webhook_url},
                )
    except Exception as e:
        logger.error(
            "שגיאה ברישום Telegram webhook",
            extra_data={"error": str(e), "webhook_url": webhook_url},
            exc_info=True,
        )


@app.on_event("startup")
async def startup() -> None:
    """Initialize database tables on startup"""
    logger.info("Starting application", extra_data={"app_name": settings.APP_NAME})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized")

    # הרצת מיגרציות אוטומטיות - הוספת עמודות חדשות לטבלאות קיימות
    # (create_all לא מוסיף עמודות לטבלאות שכבר קיימות)
    # הערה: המיגרציות רצות רק על PostgreSQL. ב-SQLite (בדיקות) create_all מספיק.
    if engine.dialect.name == "postgresql":
        from app.db.migrations import run_all_migrations, add_enum_values

        # שלב 1: הוספת ערכי enum חדשים (דורש AUTOCOMMIT, לפני יצירת טבלאות)
        await add_enum_values(engine)

        # שלב 2: מיגרציות רגילות (טבלאות, עמודות, אינדקסים)
        async with engine.begin() as conn:
            await run_all_migrations(conn)
        logger.info("Auto-migrations completed")

    # רישום webhook של טלגרם — מבטיח שהטוקן הנוכחי מצביע ל-URL הנכון
    await _register_telegram_webhook()


@app.on_event("shutdown")
async def shutdown() -> None:
    """Cleanup on shutdown"""
    logger.info("Shutting down application")
    # סגירת חיבור Redis
    from app.core.redis_client import close_redis
    await close_redis()
    # סגירת חיבורי מסד הנתונים למניעת connection pool exhaustion
    await engine.dispose()
    logger.info("Database connections disposed")


@app.get(
    "/health",
    summary="בדיקת חיוּת (Liveness Probe)",
    description=(
        "בדיקה קלה שהתהליך חי ומגיב. "
        "משמש ל-Render/Load Balancer להחלטת restart. "
        "לא בודק תלויות חיצוניות — כדי למנוע restart מיותר בגלל כשלון DB/Redis."
    ),
    tags=["Health"],
)
async def health_check() -> dict[str, str]:
    """Liveness probe — התהליך חי ומגיב."""
    return {"status": "healthy"}


@app.get(
    "/health/ready",
    summary="בדיקת מוכנות (Readiness Probe)",
    description=(
        "בדיקה מקיפה של כל התלויות: DB, Redis, WhatsApp Gateway, Celery broker. "
        "מחזיר status=healthy אם הכל תקין, status=degraded אם תלות לא קריטית "
        "(WhatsApp) לא זמינה (עדיין HTTP 200), או status=unhealthy עם HTTP 503 "
        "אם תלות קריטית (DB/Redis/Celery) לא זמינה."
    ),
    responses={
        200: {
            "description": "תלויות קריטיות תקינות (WhatsApp עשוי להיות מושבת)",
            "content": {
                "application/json": {
                    "examples": {
                        "healthy": {
                            "summary": "הכל תקין",
                            "value": {
                                "status": "healthy",
                                "db": "ok",
                                "redis": "ok",
                                "whatsapp_gateway": "ok",
                                "celery": "ok",
                            },
                        },
                        "degraded": {
                            "summary": "WhatsApp מושבת — טלגרם עדיין עובד",
                            "value": {
                                "status": "degraded",
                                "db": "ok",
                                "redis": "ok",
                                "whatsapp_gateway": "error: whatsapp_disconnected",
                                "celery": "ok",
                            },
                        },
                    }
                }
            },
        },
        503: {
            "description": "תלות קריטית לא זמינה — המערכת לא יכולה לשרת בקשות",
            "content": {
                "application/json": {
                    "example": {
                        "status": "unhealthy",
                        "db": "error: db_unavailable",
                        "redis": "ok",
                        "whatsapp_gateway": "ok",
                        "celery": "ok",
                    }
                }
            },
        },
    },
    tags=["Health"],
)
async def readiness_check() -> dict[str, str]:
    """Readiness probe — בדיקת כל התלויות החיצוניות."""
    from starlette.responses import JSONResponse

    from app.domain.services.health_service import check_readiness

    result = await check_readiness()
    # רק כשל בתלות קריטית (DB/Redis/Celery) מחזיר 503.
    # WhatsApp מושבת = degraded אבל עדיין HTTP 200 — כדי לא לחסום תעבורת טלגרם.
    status_code = 503 if result["status"] == "unhealthy" else 200
    return JSONResponse(content=result, status_code=status_code)


@app.get("/docs", include_in_schema=False)
async def swagger_ui_html() -> HTMLResponse:
    """Swagger UI documentation (RTL + Hebrew-friendly styling + כניסה מהירה)."""
    from html import escape as html_escape
    from json import dumps as json_dumps

    title = html_escape(f"{app.title} - תיעוד API (Swagger UI)")
    # json.dumps מייצר מחרוזת JS תקינה עם מירכאות — מחליפה את url: "__OPENAPI_URL__"
    openapi_url_js = json_dumps(app.openapi_url or "/openapi.json")

    # סדר ההחלפות חשוב: URL קודם, אח"כ כותרת —
    # מונע מצב שבו הכותרת מכילה את ה-placeholder של ה-URL
    html = (
        _SWAGGER_HTML_TEMPLATE
        .replace("__OPENAPI_URL_JS__", openapi_url_js)
        .replace("__TITLE__", title)
    )
    return HTMLResponse(content=html)


# תבנית HTML מותאמת ל-Swagger UI.
# מוסיפה:
# 1. window.ui — חשיפת instance של Swagger UI לסקריפט הכניסה המהירה
# 2. swagger-auth.js — ווידג'ט כניסה מהירה (Admin API Key + OTP → JWT)
_SWAGGER_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
    <meta charset="UTF-8">
    <title>__TITLE__</title>
    <link rel="stylesheet" type="text/css" href="/static/swagger-rtl.css">
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.9.0/swagger-ui-bundle.js"></script>
    <script>
    window.ui = SwaggerUIBundle({
        url: __OPENAPI_URL_JS__,
        dom_id: "#swagger-ui",
        presets: [
            SwaggerUIBundle.presets.apis,
        ],
        layout: "BaseLayout",
        deepLinking: true,
        showExtensions: true,
        showCommonExtensions: true,
        displayRequestDuration: true,
        defaultModelsExpandDepth: 1,
    });
    </script>
    <script src="/static/swagger-auth.js"></script>
</body>
</html>
"""


@app.get("/redoc", include_in_schema=False)
async def redoc_html():
    """
    ReDoc documentation endpoint (default FastAPI page, English).

    משתמש ב-unpkg במקום jsdelivr כדי למנוע בעיות טעינה שגורמות לדף ריק.
    """
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - ReDoc",
        redoc_js_url="https://unpkg.com/redoc@latest/bundles/redoc.standalone.js",
    )
