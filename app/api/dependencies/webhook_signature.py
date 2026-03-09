"""
אימות חתימת webhook מקיף — מונע הזרקת הודעות מזויפות.

פיצ'ר 4: תמיכה בשלושה ספקים:
- Telegram: X-Telegram-Bot-Api-Secret-Token (כבר קיים ב-webhook_auth.py)
- WhatsApp Cloud API: X-Hub-Signature-256 (HMAC-SHA256)
- WPPConnect: X-Webhook-Signature (HMAC-SHA256)

כולל חסימת IP אוטומטית אחרי X ניסיונות אימות כושלים.
"""
import hashlib
import hmac
import time
from collections import defaultdict

from fastapi import Request, HTTPException, status

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# מעקב אחרי ניסיונות כושלים לכל IP
_failed_attempts: dict[str, list[float]] = defaultdict(list)
# רשימת IP חסומים — {ip: expiry_timestamp}
_blocked_ips: dict[str, float] = {}


def _get_client_ip(request: Request) -> str:
    """חילוץ IP של הלקוח — מכבד X-Forwarded-For מאחורי reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # הראשון ברשימה הוא ה-IP האמיתי
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_ip_blocked(client_ip: str) -> bool:
    """בדיקה האם IP חסום."""
    expiry = _blocked_ips.get(client_ip)
    if expiry is None:
        return False
    if time.time() > expiry:
        # חסימה פגה — מנקים
        del _blocked_ips[client_ip]
        _failed_attempts.pop(client_ip, None)
        return False
    return True


def _record_failed_attempt(client_ip: str) -> None:
    """רישום ניסיון אימות כושל וחסימת IP אם חרג מהמכסה."""
    now = time.time()
    window = settings.WEBHOOK_SIGNATURE_BLOCK_DURATION_SECONDS

    # ניקוי ניסיונות ישנים מחוץ לחלון הזמן
    _failed_attempts[client_ip] = [
        ts for ts in _failed_attempts[client_ip] if now - ts < window
    ]
    _failed_attempts[client_ip].append(now)

    attempt_count = len(_failed_attempts[client_ip])

    if attempt_count >= settings.WEBHOOK_SIGNATURE_BLOCK_AFTER:
        _blocked_ips[client_ip] = now + window
        logger.warning(
            "IP חסום אוטומטית אחרי ניסיונות אימות webhook כושלים",
            extra_data={
                "client_ip": client_ip,
                "failed_attempts": attempt_count,
                "block_duration_seconds": window,
            },
        )


def verify_wppconnect_signature(body: bytes, signature_header: str | None) -> bool:
    """אימות חתימת HMAC-SHA256 של WPPConnect על ה-payload.

    WPPConnect שולח את החתימה בכותרת X-Webhook-Signature בפורמט sha256=<hex>.
    """
    secret = settings.WPPCONNECT_WEBHOOK_SECRET
    if not secret:
        # סוד לא מוגדר — אין אימות (אזהרה נרשמת ב-startup)
        return True

    if not signature_header:
        return False

    # תמיכה בפורמטים: "sha256=<hex>" ו-"<hex>" ישירות
    if signature_header.startswith("sha256="):
        provided_sig = signature_header[7:]
    else:
        provided_sig = signature_header

    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(provided_sig, expected)


async def verify_webhook_signature(
    request: Request, provider: str
) -> None:
    """אימות חתימת webhook לפי ספק — עם חסימת IP אוטומטית.

    Args:
        request: אובייקט הבקשה
        provider: שם הספק ("wppconnect" / "whatsapp_cloud" / "telegram")

    Raises:
        HTTPException: 403 אם האימות נכשל, 429 אם IP חסום
    """
    client_ip = _get_client_ip(request)

    # בדיקת חסימת IP
    if _is_ip_blocked(client_ip):
        logger.warning(
            "בקשת webhook מ-IP חסום",
            extra_data={"client_ip": client_ip, "provider": provider},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="IP חסום זמנית עקב ניסיונות אימות כושלים חוזרים",
        )

    if provider == "wppconnect":
        secret = settings.WPPCONNECT_WEBHOOK_SECRET
        if not secret:
            # אימות לא מוגדר — מדלג
            return

        body = await request.body()
        signature = request.headers.get("X-Webhook-Signature")

        if not verify_wppconnect_signature(body, signature):
            _record_failed_attempt(client_ip)
            logger.warning(
                "WPPConnect webhook: חתימה לא תקינה",
                extra_data={"client_ip": client_ip},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="חתימת webhook לא תקינה",
            )

    elif provider == "whatsapp_cloud":
        # כבר מטופל ב-whatsapp_cloud.py — כאן רק בדיקת IP blocking
        pass

    elif provider == "telegram":
        # כבר מטופל ב-webhook_auth.py — כאן רק בדיקת IP blocking
        pass


def get_blocked_ips() -> dict[str, float]:
    """שליפת רשימת IP חסומים — לצורך ניטור."""
    now = time.time()
    # ניקוי חסימות שפגו
    expired = [ip for ip, expiry in _blocked_ips.items() if now > expiry]
    for ip in expired:
        del _blocked_ips[ip]
        _failed_attempts.pop(ip, None)

    return {
        ip: round(expiry - now, 1)
        for ip, expiry in _blocked_ips.items()
    }


def get_failed_attempt_counts() -> dict[str, int]:
    """מספר ניסיונות כושלים לכל IP — לצורך ניטור."""
    return {ip: len(attempts) for ip, attempts in _failed_attempts.items() if attempts}
