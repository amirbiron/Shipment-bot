# מדריך מימוש - פאנל ניהול תחנה (Web App)

## תוכן עניינים
1. [סקירה כללית](#סקירה-כללית)
2. [ארכיטקטורה](#ארכיטקטורה)
3. [שלב 1 - אימות (Authentication)](#שלב-1---אימות-authentication)
4. [שלב 2 - API Endpoints לפאנל](#שלב-2---api-endpoints-לפאנל)
5. [שלב 3 - Frontend](#שלב-3---frontend)
6. [שלב 4 - דפי הפאנל](#שלב-4---דפי-הפאנל)
7. [שלב 5 - בדיקות](#שלב-5---בדיקות)
8. [שלב 6 - Deployment](#שלב-6---deployment)
9. [סכמת מודלים קיימת](#סכמת-מודלים-קיימת)
10. [מיפוי שירותים קיימים](#מיפוי-שירותים-קיימים)

---

## סקירה כללית

### מה הפאנל עושה
פאנל ווב לבעלי תחנות שמרחיב את היכולות שקיימות היום בבוט, עם דגש על:
- **דשבורד** — סטטוס משלוחים בזמן אמת, סיכום פיננסי
- **דוחות** — סינון לפי תאריכים, ייצוא CSV/PDF
- **ניהול bulk** — הוספת כמה סדרנים/חסומים בפעולה אחת
- **טבלאות נתונים** — היסטוריית ארנק מלאה, משלוחים עם pagination

### מה נשאר בבוט
פעולות יומיומיות מהירות (צפייה בארנק, הוספת סדרן בודד) ממשיכות לעבוד דרך הבוט כרגיל. הפאנל הוא **תוספת**, לא תחליף.

### גישה היברידית
```
בעל תחנה
├── בוט (Telegram/WhatsApp) → פעולות מהירות יומיומיות
└── פאנל ווב → דוחות, דשבורד, ניהול מתקדם, פעולות bulk
```

---

## ארכיטקטורה

### מבנה קיים (לא משתנה)
```
Bot Gateway (Webhooks) → State Machine → Services → PostgreSQL
```

### תוספת הפאנל
```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│   Frontend       │────▶│   FastAPI Backend     │────▶│   PostgreSQL    │
│   (React/Vue)    │     │   /api/panel/...      │     │   (קיים)       │
│   SPA            │◀────│   + JWT Auth          │◀────│                 │
└─────────────────┘     └──────────────────────┘     └─────────────────┘
                               │
                               ▼
                        StationService (קיים)
```

### עיקרון מפתח: שימוש חוזר
**שכבת ה-services (`StationService`) כבר קיימת ומוכנה**. לא צריך לשכפל לוגיקה עסקית.
מה שצריך לבנות:
1. שכבת אימות (JWT)
2. API endpoints חדשים שקוראים ל-`StationService` הקיים
3. Frontend

---

## שלב 1 - אימות (Authentication)

### מצב קיים
- **אין מנגנון אימות ל-API** — הבוט מזהה משתמשים לפי `telegram_chat_id` / WhatsApp contact
- מודל `User` לא מכיל שדה סיסמה או token
- `CORS` מוגדר ב-`app/main.py` עם `Authorization` header

### מה צריך לבנות

#### 1.1 זרימת כניסה באמצעות OTP דרך הבוט
הגישה המומלצת — **בלי סיסמאות**, התחברות דרך הבוט שכבר מזהה את המשתמש:

```
בעל תחנה → שולח "כניסה לפאנל" בבוט → מקבל קוד OTP (6 ספרות, תוקף 5 דקות)
→ מזין בפאנל ווב → מקבל JWT token → גישה לפאנל
```

#### 1.2 קבצים חדשים

**`app/core/auth.py`** — לוגיקת JWT:
```python
"""
אימות JWT לפאנל ווב
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# הגדרות (להוסיף ל-Settings ב-config.py)
# JWT_SECRET_KEY: str  — מפתח סודי (לייצר עם: openssl rand -hex 32)
# JWT_ALGORITHM: str = "HS256"
# JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  (8 שעות)
# OTP_EXPIRE_SECONDS: int = 300  (5 דקות)


class TokenPayload(BaseModel):
    """תוכן ה-JWT token"""
    user_id: int
    station_id: int
    role: str  # "station_owner"
    exp: datetime


def create_access_token(user_id: int, station_id: int, role: str) -> str:
    """יצירת JWT token"""
    expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "user_id": user_id,
        "station_id": station_id,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> Optional[TokenPayload]:
    """אימות token — מחזיר None אם לא תקין"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return TokenPayload(**payload)
    except JWTError:
        return None


def generate_otp() -> str:
    """יצירת קוד OTP בטוח — 6 ספרות"""
    return f"{secrets.randbelow(1000000):06d}"
```

**`app/api/dependencies/auth.py`** — FastAPI dependency:
```python
"""
Dependency לאימות בקשות לפאנל
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_token, TokenPayload
from app.db.database import get_db
from app.domain.services.station_service import StationService

security = HTTPBearer()


async def get_current_station_owner(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> TokenPayload:
    """
    אימות הבקשה ווידוא שהמשתמש הוא בעל תחנה פעיל.

    שימוש:
        @router.get("/dashboard")
        async def dashboard(auth: TokenPayload = Depends(get_current_station_owner)):
            station_id = auth.station_id
    """
    token_data = verify_token(credentials.credentials)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="טוקן לא תקין או פג תוקף",
        )

    # ולידציה שהתחנה עדיין פעילה
    station_service = StationService(db)
    station = await station_service.get_station(token_data.station_id)
    if not station:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="התחנה לא פעילה",
        )

    return token_data
```

#### 1.3 אחסון OTP ב-Redis
```python
# ב-StationService או בשירות auth ייעודי
from app.core.redis_client import get_redis

async def store_otp(user_id: int, otp: str) -> None:
    """שמירת OTP ב-Redis עם TTL של 5 דקות"""
    redis = await get_redis()
    key = f"panel_otp:{user_id}"
    await redis.setex(key, settings.OTP_EXPIRE_SECONDS, otp)

async def verify_otp(user_id: int, otp: str) -> bool:
    """אימות OTP — מוחק לאחר שימוש (one-time)"""
    redis = await get_redis()
    key = f"panel_otp:{user_id}"
    stored = await redis.get(key)
    if stored and stored.decode() == otp:
        await redis.delete(key)  # שימוש חד-פעמי
        return True
    return False
```

#### 1.4 הגדרות חדשות ב-`app/core/config.py`
```python
# להוסיף ל-class Settings:
JWT_SECRET_KEY: str = ""  # חובה בפרודקשן — openssl rand -hex 32
JWT_ALGORITHM: str = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 שעות
OTP_EXPIRE_SECONDS: int = 300  # 5 דקות
```

---

## שלב 2 - API Endpoints לפאנל

### מבנה קבצים חדש
```
app/api/routes/
├── panel/
│   ├── __init__.py         # Router ראשי לפאנל
│   ├── auth.py             # כניסה והתנתקות
│   ├── dashboard.py        # דשבורד
│   ├── dispatchers.py      # ניהול סדרנים
│   ├── deliveries.py       # משלוחים
│   ├── wallet.py           # ארנק ולדג'ר
│   ├── blacklist.py        # רשימה שחורה
│   ├── reports.py          # דוחות וייצוא
│   └── groups.py           # הגדרות קבוצות
```

### 2.1 Router ראשי — `app/api/routes/panel/__init__.py`
```python
from fastapi import APIRouter

from app.api.routes.panel.auth import router as auth_router
from app.api.routes.panel.dashboard import router as dashboard_router
from app.api.routes.panel.dispatchers import router as dispatchers_router
from app.api.routes.panel.deliveries import router as deliveries_router
from app.api.routes.panel.wallet import router as wallet_router
from app.api.routes.panel.blacklist import router as blacklist_router
from app.api.routes.panel.reports import router as reports_router
from app.api.routes.panel.groups import router as groups_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["Panel - אימות"])
router.include_router(dashboard_router, prefix="/dashboard", tags=["Panel - דשבורד"])
router.include_router(dispatchers_router, prefix="/dispatchers", tags=["Panel - סדרנים"])
router.include_router(deliveries_router, prefix="/deliveries", tags=["Panel - משלוחים"])
router.include_router(wallet_router, prefix="/wallet", tags=["Panel - ארנק"])
router.include_router(blacklist_router, prefix="/blacklist", tags=["Panel - רשימה שחורה"])
router.include_router(reports_router, prefix="/reports", tags=["Panel - דוחות"])
router.include_router(groups_router, prefix="/groups", tags=["Panel - קבוצות"])
```

**רישום ב-`app/api/routes/__init__.py`** — להוסיף שורה:
```python
from app.api.routes.panel import router as panel_router
router.include_router(panel_router, prefix="/panel", tags=["Panel"])
```

### 2.2 Auth — `panel/auth.py`

| Endpoint | Method | תיאור |
|----------|--------|--------|
| `/api/panel/auth/request-otp` | POST | בקשת OTP (מזהה לפי טלפון) |
| `/api/panel/auth/verify-otp` | POST | אימות OTP → JWT token |
| `/api/panel/auth/refresh` | POST | חידוש token |
| `/api/panel/auth/me` | GET | פרטי המשתמש המחובר |

```python
"""
אימות לפאנל ווב — כניסה באמצעות OTP
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.user import UserRole
from app.core.auth import create_access_token, generate_otp
from app.core.validation import PhoneNumberValidator
from app.domain.services.station_service import StationService
from app.api.dependencies.auth import get_current_station_owner

router = APIRouter()


class OTPRequest(BaseModel):
    phone_number: str

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PhoneNumberValidator.validate(v):
            raise ValueError("מספר טלפון לא תקין")
        return PhoneNumberValidator.normalize(v)


class OTPVerify(BaseModel):
    phone_number: str
    otp: str

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PhoneNumberValidator.validate(v):
            raise ValueError("מספר טלפון לא תקין")
        return PhoneNumberValidator.normalize(v)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    station_id: int
    station_name: str


@router.post(
    "/request-otp",
    summary="בקשת קוד כניסה",
    description="שולח קוד OTP לבעל התחנה דרך הבוט (Telegram/WhatsApp).",
    responses={
        200: {"description": "OTP נשלח בהצלחה"},
        404: {"description": "משתמש לא נמצא או לא בעל תחנה"},
    },
    tags=["Panel - אימות"],
)
async def request_otp(
    data: OTPRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # 1. חיפוש המשתמש
    # 2. ולידציה שהוא STATION_OWNER
    # 3. יצירת OTP ושמירה ב-Redis
    # 4. שליחת ההודעה דרך הבוט (Telegram/WhatsApp)
    ...
    return {"message": "קוד כניסה נשלח לבוט"}


@router.post(
    "/verify-otp",
    response_model=TokenResponse,
    summary="אימות קוד כניסה",
    description="אימות קוד OTP וקבלת JWT token.",
    tags=["Panel - אימות"],
)
async def verify_otp_endpoint(
    data: OTPVerify,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # 1. אימות OTP מ-Redis
    # 2. יצירת JWT token
    # 3. החזרת token + פרטי תחנה
    ...
```

### 2.3 Dashboard — `panel/dashboard.py`

| Endpoint | Method | תיאור |
|----------|--------|--------|
| `/api/panel/dashboard` | GET | נתוני דשבורד מרכזיים |

```python
"""
דשבורד — סיכום תחנה
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.core.auth import TokenPayload
from app.api.dependencies.auth import get_current_station_owner
from app.domain.services.station_service import StationService

router = APIRouter()


class DashboardResponse(BaseModel):
    """נתוני דשבורד"""
    station_name: str
    # משלוחים
    active_deliveries_count: int
    today_deliveries_count: int
    today_delivered_count: int
    # פיננסי
    wallet_balance: float
    commission_rate: float
    today_revenue: float
    # כוח אדם
    active_dispatchers_count: int
    blacklisted_count: int


@router.get(
    "",
    response_model=DashboardResponse,
    summary="נתוני דשבורד תחנה",
    description="מחזיר סיכום נתונים מרכזיים לדשבורד: משלוחים, ארנק, סדרנים.",
    tags=["Panel - דשבורד"],
)
async def get_dashboard(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    station_service = StationService(db)

    # שימוש בשירותים קיימים
    station = await station_service.get_station(auth.station_id)
    wallet = await station_service.get_station_wallet(auth.station_id)
    active = await station_service.get_station_active_deliveries(auth.station_id)
    dispatchers = await station_service.get_dispatchers(auth.station_id)
    blacklist = await station_service.get_blacklist(auth.station_id)

    # חישובים נוספים (משלוחים של היום, הכנסה יומית) —
    # צריך להוסיף מתודות ל-StationService:
    #   get_today_deliveries_count(station_id) -> int
    #   get_today_revenue(station_id) -> float

    return DashboardResponse(
        station_name=station.name,
        active_deliveries_count=len(active),
        today_deliveries_count=0,      # לממש
        today_delivered_count=0,        # לממש
        wallet_balance=wallet.balance,
        commission_rate=wallet.commission_rate,
        today_revenue=0.0,             # לממש
        active_dispatchers_count=len(dispatchers),
        blacklisted_count=len(blacklist),
    )
```

### 2.4 Dispatchers — `panel/dispatchers.py`

| Endpoint | Method | תיאור |
|----------|--------|--------|
| `/api/panel/dispatchers` | GET | רשימת סדרנים |
| `/api/panel/dispatchers` | POST | הוספת סדרן |
| `/api/panel/dispatchers/bulk` | POST | הוספת כמה סדרנים |
| `/api/panel/dispatchers/{user_id}` | DELETE | הסרת סדרן |

```python
"""
ניהול סדרנים — מבוסס על StationService.add_dispatcher / remove_dispatcher / get_dispatchers
"""
# כל ה-endpoints קוראים ל-StationService הקיים.
# הוספת bulk — לולאה על add_dispatcher עם אגרגציית תוצאות:

@router.post("/bulk", summary="הוספת סדרנים בכמות")
async def add_dispatchers_bulk(
    data: BulkDispatchersRequest,  # רשימת מספרי טלפון
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> BulkDispatchersResponse:
    station_service = StationService(db)
    results = []
    for phone in data.phone_numbers:
        success, message = await station_service.add_dispatcher(auth.station_id, phone)
        results.append({"phone": PhoneNumberValidator.mask(phone), "success": success, "message": message})
    return BulkDispatchersResponse(results=results)
```

### 2.5 Deliveries — `panel/deliveries.py`

| Endpoint | Method | תיאור |
|----------|--------|--------|
| `/api/panel/deliveries/active` | GET | משלוחים פעילים (עם pagination) |
| `/api/panel/deliveries/history` | GET | היסטוריה (עם סינון תאריכים) |
| `/api/panel/deliveries/{id}` | GET | פרטי משלוח בודד |

```python
# דגשים למימוש:
# 1. pagination — query params: page, page_size (ברירת מחדל 20, מקסימום 100)
# 2. סינון — status, date_from, date_to, courier_name
# 3. שימוש ב-joinedload — למנוע N+1:
#
#    query = select(Delivery).options(
#        joinedload(Delivery.sender),
#        joinedload(Delivery.courier),
#    ).where(
#        Delivery.station_id == auth.station_id,
#    )
#
# 4. הרחבת StationService: צריך להוסיף מתודה עם תמיכה ב-pagination וסינון:
#    get_station_deliveries_paginated(station_id, page, page_size, filters) -> (items, total)
```

### 2.6 Wallet — `panel/wallet.py`

| Endpoint | Method | תיאור |
|----------|--------|--------|
| `/api/panel/wallet` | GET | יתרה ופרטי ארנק |
| `/api/panel/wallet/ledger` | GET | היסטוריית תנועות (pagination + סינון) |

```python
# דגשים:
# 1. הרחבת get_station_ledger לתמוך ב:
#    - pagination (offset, limit)
#    - סינון לפי entry_type (COMMISSION_CREDIT / MANUAL_CHARGE / WITHDRAWAL)
#    - סינון לפי טווח תאריכים (date_from, date_to)
# 2. סיכום: total_credits, total_charges, total_withdrawals בטווח הנבחר
```

### 2.7 Blacklist — `panel/blacklist.py`

| Endpoint | Method | תיאור |
|----------|--------|--------|
| `/api/panel/blacklist` | GET | רשימה שחורה |
| `/api/panel/blacklist` | POST | הוספה לרשימה שחורה |
| `/api/panel/blacklist/bulk` | POST | הוספה מרובה |
| `/api/panel/blacklist/{courier_id}` | DELETE | הסרה |

```python
# מבוסס על StationService.add_to_blacklist / remove_from_blacklist / get_blacklist
# תוספת: joinedload על User כדי להציג שם + טלפון (ממוסך) של הנהג
```

### 2.8 Reports — `panel/reports.py`

| Endpoint | Method | תיאור |
|----------|--------|--------|
| `/api/panel/reports/collection` | GET | דוח גבייה (JSON) |
| `/api/panel/reports/collection/export` | GET | ייצוא CSV |
| `/api/panel/reports/revenue` | GET | דוח הכנסות לפי טווח תאריכים |
| `/api/panel/reports/revenue/export` | GET | ייצוא CSV |

```python
# דוח גבייה — מבוסס על StationService.get_collection_report
# הרחבה: תמיכה בבחירת מחזור חיוב (לא רק הנוכחי)
#
# דוח הכנסות — חדש. צריך מתודה חדשה ב-StationService:
#   get_revenue_report(station_id, date_from, date_to) -> RevenueReport
#
# ייצוא CSV:
from fastapi.responses import StreamingResponse
import csv
import io

@router.get("/collection/export", summary="ייצוא דוח גבייה ל-CSV")
async def export_collection_report(
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    station_service = StationService(db)
    report = await station_service.get_collection_report(auth.station_id)

    output = io.StringIO()
    # BOM לתמיכה ב-Excel עברית
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(["שם נהג", "סכום חוב"])
    for row in report:
        writer.writerow([row["driver_name"], row["total_debt"]])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=collection_report.csv"},
    )
```

### 2.9 Groups — `panel/groups.py`

| Endpoint | Method | תיאור |
|----------|--------|--------|
| `/api/panel/groups` | GET | הגדרות קבוצות נוכחיות |
| `/api/panel/groups` | PUT | עדכון הגדרות קבוצות |

```python
# מבוסס על StationService.update_station_groups
# שיפור: ולידציה שה-chat_id תקין (לפחות פורמט)
```

---

## שלב 3 - Frontend

### טכנולוגיה מומלצת
**React + TypeScript + Vite + Tailwind CSS + shadcn/ui**

סיבות:
- **React** — הנפוץ ביותר, קל למצוא מפתחים
- **TypeScript** — type safety שמתאים לסכמות ה-API
- **Vite** — build מהיר
- **Tailwind** — עיצוב מהיר עם תמיכה מובנית ב-RTL (`dir="rtl"`)
- **shadcn/ui** — קומפוננטות מוכנות (טבלאות, טפסים, graphs)

### מבנה Frontend
```
frontend/
├── src/
│   ├── api/
│   │   ├── client.ts         # Axios/fetch wrapper עם JWT
│   │   ├── auth.ts           # קריאות auth
│   │   ├── dashboard.ts      # קריאות דשבורד
│   │   ├── dispatchers.ts    # קריאות סדרנים
│   │   ├── deliveries.ts     # קריאות משלוחים
│   │   ├── wallet.ts         # קריאות ארנק
│   │   ├── blacklist.ts      # קריאות רשימה שחורה
│   │   ├── reports.ts        # קריאות דוחות
│   │   └── groups.ts         # קריאות קבוצות
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx   # ניווט צדדי
│   │   │   ├── Header.tsx    # כותרת + שם תחנה
│   │   │   └── Layout.tsx    # Shell ראשי
│   │   ├── ui/               # shadcn/ui קומפוננטות
│   │   └── shared/
│   │       ├── DataTable.tsx  # טבלת נתונים גנרית עם pagination
│   │       ├── ExportButton.tsx
│   │       ├── DateRangePicker.tsx
│   │       └── StatusBadge.tsx
│   ├── pages/
│   │   ├── LoginPage.tsx
│   │   ├── DashboardPage.tsx
│   │   ├── DispatchersPage.tsx
│   │   ├── DeliveriesPage.tsx
│   │   ├── WalletPage.tsx
│   │   ├── BlacklistPage.tsx
│   │   ├── ReportsPage.tsx
│   │   └── GroupSettingsPage.tsx
│   ├── hooks/
│   │   ├── useAuth.ts
│   │   └── useStationData.ts
│   ├── store/
│   │   └── authStore.ts      # Zustand — ניהול state של auth
│   ├── App.tsx
│   └── main.tsx
├── index.html
├── tailwind.config.ts
├── tsconfig.json
├── vite.config.ts
└── package.json
```

### API Client עם JWT
```typescript
// src/api/client.ts
import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL + "/api/panel",
  headers: { "Content-Type": "application/json" },
});

// הוספת token לכל בקשה
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// טיפול ב-401 — ניתוב לדף כניסה
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("access_token");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export default api;
```

### RTL Support
```typescript
// App.tsx
function App() {
  return (
    <div dir="rtl" className="font-sans">
      <RouterProvider router={router} />
    </div>
  );
}
```

```typescript
// tailwind.config.ts
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Heebo", "Arial", "sans-serif"],
      },
    },
  },
};
```

---

## שלב 4 - דפי הפאנל

### 4.1 דף כניסה (`LoginPage`)
```
┌─────────────────────────────────┐
│         כניסה לפאנל תחנה         │
│                                  │
│   מספר טלפון: [____________]     │
│   [שלח קוד כניסה]               │
│                                  │
│   ── לאחר שליחה ──              │
│   קוד אימות: [______]           │
│   [כניסה]                        │
│                                  │
│   * הקוד נשלח אליך דרך הבוט     │
└─────────────────────────────────┘
```

### 4.2 דשבורד (`DashboardPage`)
```
┌──────────────────────────────────────────────────────┐
│  [Sidebar]  │  דשבורד — תחנת "אקספרס ת"א"           │
│  ──────────  │                                        │
│  📊 דשבורד  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │
│  👥 סדרנים  │  │ פעיל │ │ היום │ │ נמסר │ │ יתרה │  │
│  📦 משלוחים │  │  12  │ │  28  │ │  16  │ │₪4,200│  │
│  💰 ארנק   │  └──────┘ └──────┘ └──────┘ └──────┘  │
│  🚫 חסומים │                                        │
│  📋 דוחות  │  ─── משלוחים פעילים (אחרונים) ───     │
│  ⚙️ קבוצות │  │ #  │ מ      │ אל    │ סטטוס │ שליח │
│             │  │124 │ ת"א   │ חיפה  │ בדרך  │ דני  │
│             │  │123 │ ר"ג   │ ת"א   │ נתפס  │ משה  │
│             │  │... │       │       │       │      │
│             │                                        │
│             │  ─── הכנסות (7 ימים אחרונים) ───      │
│             │  [גרף עמודות]                          │
└──────────────────────────────────────────────────────┘
```

### 4.3 ניהול סדרנים (`DispatchersPage`)
```
┌──────────────────────────────────────────────────────┐
│  סדרנים                     [+ הוסף סדרן] [+ ייבוא] │
│                                                       │
│  │ שם        │ טלפון        │ מאז       │ פעולות    │ │
│  │ ישראל כהן │ +97250***4567│ 15/01/26  │ [הסר]    │ │
│  │ דנה לוי   │ +97252***8901│ 03/02/26  │ [הסר]    │ │
│  │ ...       │              │           │           │ │
│                                                       │
│  ─── הוספה מרובה ───                                 │
│  [textarea — מספר אחד בכל שורה]                      │
│  [הוסף הכל]                                          │
└──────────────────────────────────────────────────────┘
```

### 4.4 משלוחים (`DeliveriesPage`)
```
┌──────────────────────────────────────────────────────┐
│  משלוחים           [סינון▾] [מ: __/__] [עד: __/__]  │
│                                                       │
│  סטטוס: [הכל ▾]                                      │
│                                                       │
│  │ # │ מ    │ אל   │ סטטוס │ שליח │ עמלה │ תאריך  │ │
│  │124│ ת"א  │ חיפה │ בדרך  │ דני  │ ₪10  │ 10/02  │ │
│  │123│ ר"ג  │ ת"א  │ נמסר  │ משה  │ ₪10  │ 09/02  │ │
│  │...│      │      │       │      │      │        │ │
│                                                       │
│  [◀ 1 2 3 ... 12 ▶]                    [ייצוא CSV]  │
└──────────────────────────────────────────────────────┘
```

### 4.5 ארנק (`WalletPage`)
```
┌──────────────────────────────────────────────────────┐
│  ארנק תחנה                                           │
│                                                       │
│  יתרה: ₪4,200.00        עמלה: 10%                    │
│                                                       │
│  ─── תנועות ───     [סוג: הכל ▾] [מ: __] [עד: __]  │
│                                                       │
│  │ תאריך  │ סוג      │ תיאור             │ סכום    │ │
│  │ 10/02  │ עמלה     │ עמלה ממשלוח #124  │ +₪10   │ │
│  │ 09/02  │ חיוב ידני│ משה — משלוח חיצוני │ +₪50   │ │
│  │ 08/02  │ משיכה    │ העברה לחשבון       │ -₪500  │ │
│  │ ...    │          │                    │         │ │
│                                                       │
│  סיכום תקופה: עמלות ₪320 | חיובים ₪150 | משיכות ₪500│
│  [◀ 1 2 3 ▶]                            [ייצוא CSV] │
└──────────────────────────────────────────────────────┘
```

### 4.6 דוחות (`ReportsPage`)
```
┌──────────────────────────────────────────────────────┐
│  דוח גבייה                                           │
│                                                       │
│  מחזור: [28/01 — 28/02 ▾]                            │
│                                                       │
│  │ שם נהג   │ סה"כ חוב │ מספר חיובים │               │
│  │ משה כהן  │ ₪350     │ 7           │               │
│  │ דני לוי  │ ₪200     │ 4           │               │
│  │ ...      │          │             │               │
│                                                       │
│  סה"כ: ₪550                          [ייצוא CSV]    │
│                                                       │
│  ─── דוח הכנסות ───                                  │
│  [גרף לפי ימים/שבועות/חודשים]                        │
│  טווח: [מ: __/__] [עד: __/__]                        │
│  סה"כ: עמלות ₪1,200 | חיובים ₪800   [ייצוא CSV]    │
└──────────────────────────────────────────────────────┘
```

---

## שלב 5 - בדיקות

### בדיקות Backend

#### מבנה
```
tests/
├── test_panel_auth.py          # בדיקות אימות
├── test_panel_dashboard.py     # בדיקות דשבורד
├── test_panel_dispatchers.py   # בדיקות סדרנים
├── test_panel_deliveries.py    # בדיקות משלוחים
├── test_panel_wallet.py        # בדיקות ארנק
├── test_panel_blacklist.py     # בדיקות רשימה שחורה
├── test_panel_reports.py       # בדיקות דוחות
└── test_panel_groups.py        # בדיקות קבוצות
```

#### דוגמאות בדיקות
```python
import pytest
from httpx import AsyncClient

class TestPanelAuth:
    """בדיקות אימות לפאנל"""

    @pytest.mark.unit
    async def test_request_otp_valid_station_owner(self, client: AsyncClient, station_owner_user):
        """בקשת OTP למשתמש שהוא בעל תחנה — אמור להצליח"""
        response = await client.post("/api/panel/auth/request-otp", json={
            "phone_number": station_owner_user.phone_number,
        })
        assert response.status_code == 200

    @pytest.mark.unit
    async def test_request_otp_non_owner_rejected(self, client: AsyncClient, sender_user):
        """בקשת OTP למשתמש שאינו בעל תחנה — אמור להידחות"""
        response = await client.post("/api/panel/auth/request-otp", json={
            "phone_number": sender_user.phone_number,
        })
        assert response.status_code == 403

    @pytest.mark.unit
    async def test_verify_otp_returns_jwt(self, client: AsyncClient, station_owner_with_otp):
        """אימות OTP תקין — מחזיר JWT token"""
        user, otp = station_owner_with_otp
        response = await client.post("/api/panel/auth/verify-otp", json={
            "phone_number": user.phone_number,
            "otp": otp,
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.unit
    async def test_wrong_otp_rejected(self, client: AsyncClient, station_owner_with_otp):
        """OTP שגוי — נדחה"""
        user, _ = station_owner_with_otp
        response = await client.post("/api/panel/auth/verify-otp", json={
            "phone_number": user.phone_number,
            "otp": "000000",
        })
        assert response.status_code == 401

    @pytest.mark.unit
    async def test_expired_token_rejected(self, client: AsyncClient, expired_token):
        """token שפג תוקף — 401"""
        response = await client.get(
            "/api/panel/dashboard",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 401


class TestPanelDashboard:
    """בדיקות דשבורד"""

    @pytest.mark.unit
    async def test_dashboard_returns_data(self, authed_client: AsyncClient):
        """דשבורד מחזיר נתונים תקינים"""
        response = await authed_client.get("/api/panel/dashboard")
        assert response.status_code == 200
        data = response.json()
        assert "active_deliveries_count" in data
        assert "wallet_balance" in data

    @pytest.mark.unit
    async def test_dashboard_unauthorized(self, client: AsyncClient):
        """גישה ללא token — 401/403"""
        response = await client.get("/api/panel/dashboard")
        assert response.status_code in (401, 403)


class TestPanelDispatchers:
    """בדיקות ניהול סדרנים"""

    @pytest.mark.unit
    async def test_add_dispatcher(self, authed_client: AsyncClient, courier_user):
        """הוספת סדרן"""
        response = await authed_client.post("/api/panel/dispatchers", json={
            "phone_number": courier_user.phone_number,
        })
        assert response.status_code == 200

    @pytest.mark.unit
    async def test_add_dispatcher_invalid_phone(self, authed_client: AsyncClient):
        """הוספת סדרן עם טלפון לא תקין — שגיאת ולידציה"""
        response = await authed_client.post("/api/panel/dispatchers", json={
            "phone_number": "invalid",
        })
        assert response.status_code == 422

    @pytest.mark.unit
    async def test_bulk_add_dispatchers(self, authed_client: AsyncClient):
        """הוספה מרובה — מחזיר תוצאה לכל מספר"""
        response = await authed_client.post("/api/panel/dispatchers/bulk", json={
            "phone_numbers": ["0501234567", "0521234567", "invalid"],
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 3

    @pytest.mark.unit
    async def test_remove_dispatcher(self, authed_client: AsyncClient, dispatcher_in_station):
        """הסרת סדרן — וידוא שלא פעיל אחרי הסרה"""
        response = await authed_client.delete(
            f"/api/panel/dispatchers/{dispatcher_in_station.user_id}"
        )
        assert response.status_code == 200


class TestPanelReports:
    """בדיקות דוחות"""

    @pytest.mark.unit
    async def test_collection_report(self, authed_client: AsyncClient):
        """דוח גבייה מחזיר נתונים"""
        response = await authed_client.get("/api/panel/reports/collection")
        assert response.status_code == 200

    @pytest.mark.unit
    async def test_export_csv(self, authed_client: AsyncClient):
        """ייצוא CSV — בודק headers"""
        response = await authed_client.get("/api/panel/reports/collection/export")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]
```

### בדיקות Frontend
```bash
# E2E עם Playwright
npx playwright test

# Unit עם Vitest
npx vitest
```

---

## שלב 6 - Deployment

### הגדרת CORS
ב-`app/core/config.py` — להוסיף את דומיין הפאנל ל-`ALLOWED_ORIGINS`:
```
ALLOWED_ORIGINS=https://panel.example.com,https://admin.example.com
```

### הגשת Frontend

**אפשרות א': Static files דרך FastAPI (פשוט)**
```python
# ב-app/main.py — להוסיף אחרי כל ה-API routes:
from fastapi.staticfiles import StaticFiles

# Serve frontend build
app.mount("/panel", StaticFiles(directory="frontend/dist", html=True), name="panel")
```

**אפשרות ב': Nginx (מומלץ לפרודקשן)**
```nginx
server {
    # Frontend
    location /panel {
        root /var/www/frontend/dist;
        try_files $uri $uri/ /panel/index.html;
    }

    # API
    location /api {
        proxy_pass http://localhost:8000;
    }
}
```

### משתני סביבה חדשים
```env
# .env — להוסיף:
JWT_SECRET_KEY=<output of: openssl rand -hex 32>
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=480
OTP_EXPIRE_SECONDS=300
ALLOWED_ORIGINS=https://panel.example.com
```

---

## סכמת מודלים קיימת

כל המודלים כבר קיימים ב-`app/db/models/`. אין צורך לשנות אותם.

```
Station (stations)
├── id, name, owner_id, is_active
├── public_group_chat_id, private_group_chat_id
├── public_group_platform, private_group_platform
├── created_at, updated_at
│
├── → StationWallet (station_wallets) [1:1]
│   └── id, station_id, balance, commission_rate
│
├── → StationDispatcher (station_dispatchers) [1:N]
│   └── id, station_id, user_id, is_active, created_at
│
├── → StationBlacklist (station_blacklist) [1:N]
│   └── id, station_id, courier_id, reason, blocked_at
│
├── → StationLedger (station_ledger) [1:N]
│   └── id, station_id, delivery_id, entry_type, amount, balance_after, description
│
├── → ManualCharge (manual_charges) [1:N]
│   └── id, station_id, dispatcher_id, driver_name, amount, description
│
└── → Delivery (deliveries) [1:N]
    └── id, station_id, sender_id, courier_id, status, fee, ...
```

---

## מיפוי שירותים קיימים

### מתודות `StationService` שקיימות ומוכנות לשימוש

| מתודה | קובץ ושורה | משמשת ב-endpoint |
|--------|------------|------------------|
| `create_station(name, owner_id)` | `station_service.py:35` | — (כבר קיים ב-API) |
| `get_station(station_id)` | `station_service.py:57` | dashboard, auth |
| `get_station_by_owner(owner_id)` | `station_service.py:67` | auth |
| `add_dispatcher(station_id, phone)` | `station_service.py:79` | dispatchers |
| `remove_dispatcher(station_id, user_id)` | `station_service.py:136` | dispatchers |
| `get_dispatchers(station_id)` | `station_service.py:162` | dispatchers, dashboard |
| `get_station_active_deliveries(station_id)` | `station_service.py:217` | deliveries, dashboard |
| `get_station_delivery_history(station_id, limit)` | `station_service.py:234` | deliveries |
| `create_manual_charge(station_id, ...)` | `station_service.py:257` | — (סדרן, לא בעל תחנה) |
| `get_station_wallet(station_id)` | `station_service.py:329` | wallet, dashboard |
| `credit_station_commission(station_id, ...)` | `station_service.py:335` | — (אוטומטי) |
| `get_station_ledger(station_id, limit)` | `station_service.py:361` | wallet |
| `add_to_blacklist(station_id, phone, reason)` | `station_service.py:374` | blacklist |
| `remove_from_blacklist(station_id, courier_id)` | `station_service.py:423` | blacklist |
| `get_blacklist(station_id)` | `station_service.py:445` | blacklist, dashboard |
| `is_blacklisted(station_id, courier_id)` | `station_service.py:456` | — (אוטומטי) |
| `update_station_groups(station_id, ...)` | `station_service.py:470` | groups |
| `get_collection_report(station_id)` | `station_service.py:518` | reports |

### מתודות חדשות שצריך להוסיף ל-`StationService`

| מתודה חדשה | מה עושה |
|------------|---------|
| `get_station_deliveries_paginated(station_id, page, page_size, filters)` | משלוחים עם pagination וסינון |
| `get_station_ledger_paginated(station_id, page, page_size, entry_type, date_from, date_to)` | תנועות ארנק עם pagination וסינון |
| `get_today_stats(station_id)` | ספירת משלוחים ומסירות של היום |
| `get_revenue_report(station_id, date_from, date_to)` | דוח הכנסות לפי טווח תאריכים |
| `get_collection_report_by_cycle(station_id, cycle_start, cycle_end)` | דוח גבייה לפי מחזור ספציפי |

---

## סדר מימוש מומלץ

| שלב | משימה | תלות | הערכת מורכבות |
|-----|--------|-------|---------------|
| 1 | הגדרות JWT ב-config + `app/core/auth.py` | — | נמוכה |
| 2 | `app/api/dependencies/auth.py` | שלב 1 | נמוכה |
| 3 | `panel/auth.py` (OTP + login) | שלב 1, 2 | בינונית |
| 4 | `panel/dashboard.py` | שלב 2 | נמוכה |
| 5 | `panel/dispatchers.py` + bulk | שלב 2 | נמוכה |
| 6 | `panel/deliveries.py` + pagination | שלב 2 | בינונית |
| 7 | `panel/wallet.py` + pagination | שלב 2 | בינונית |
| 8 | `panel/blacklist.py` + bulk | שלב 2 | נמוכה |
| 9 | `panel/reports.py` + CSV export | שלב 2 | בינונית |
| 10 | `panel/groups.py` | שלב 2 | נמוכה |
| 11 | בדיקות backend | שלבים 3–10 | בינונית |
| 12 | Frontend — React scaffolding + auth | שלב 3 | בינונית |
| 13 | Frontend — דפים | שלב 12 | גבוהה |
| 14 | Deployment + CORS | הכל | נמוכה |

---

## חבילות Python חדשות (להוסיף ל-requirements.txt)

```
python-jose[cryptography]>=3.3.0   # JWT encoding/decoding
```

## חבילות Frontend (package.json)

```json
{
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.0.0",
    "axios": "^1.7.0",
    "zustand": "^5.0.0",
    "@tanstack/react-query": "^5.0.0",
    "@tanstack/react-table": "^8.0.0",
    "recharts": "^2.15.0",
    "date-fns": "^4.0.0"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "vite": "^6.0.0",
    "@vitejs/plugin-react": "^4.0.0",
    "tailwindcss": "^4.0.0",
    "vitest": "^3.0.0",
    "@playwright/test": "^1.50.0"
  }
}
```
