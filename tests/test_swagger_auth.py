"""
בדיקות ווידג'ט כניסה מהירה ב-Swagger UI — /docs

מוודא ש:
1. דף /docs מוגש כ-HTML תקין עם Swagger UI
2. הסקריפט swagger-auth.js נטען
3. window.ui חשוף (לא const) כדי שווידג'ט הכניסה יוכל לגשת ל-Swagger UI
4. הקובץ הסטטי swagger-auth.js מוגש מ-/static/
"""
import pytest


class TestSwaggerDocsPage:
    """בדיקות דף /docs"""

    @pytest.mark.asyncio
    async def test_docs_returns_html(self, test_client):
        """GET /docs מחזיר HTML תקין"""
        response = await test_client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_docs_contains_swagger_ui_div(self, test_client):
        """דף הדוקומנטציה מכיל את אלמנט swagger-ui"""
        response = await test_client.get("/docs")
        assert 'id="swagger-ui"' in response.text

    @pytest.mark.asyncio
    async def test_docs_exposes_window_ui(self, test_client):
        """Swagger UI חשוף כ-window.ui ולא כ-const — נדרש לווידג'ט הכניסה"""
        response = await test_client.get("/docs")
        assert "window.ui = SwaggerUIBundle(" in response.text
        # מוודא שאין const ui שיחסום גישה מסקריפט חיצוני
        assert "const ui = SwaggerUIBundle(" not in response.text

    @pytest.mark.asyncio
    async def test_docs_loads_auth_widget_script(self, test_client):
        """דף הדוקומנטציה טוען את סקריפט ווידג'ט הכניסה המהירה"""
        response = await test_client.get("/docs")
        assert "/static/swagger-auth.js" in response.text

    @pytest.mark.asyncio
    async def test_docs_loads_swagger_rtl_css(self, test_client):
        """דף הדוקומנטציה טוען את ה-CSS לתמיכת RTL"""
        response = await test_client.get("/docs")
        assert "/static/swagger-rtl.css" in response.text

    @pytest.mark.asyncio
    async def test_docs_loads_openapi_json(self, test_client):
        """דף הדוקומנטציה מפנה ל-/openapi.json"""
        response = await test_client.get("/docs")
        assert "/openapi.json" in response.text

    @pytest.mark.asyncio
    async def test_docs_has_rtl_direction(self, test_client):
        """HTML מוגדר כ-RTL עם שפה עברית"""
        response = await test_client.get("/docs")
        assert 'dir="rtl"' in response.text
        assert 'lang="he"' in response.text


class TestSwaggerAuthStatic:
    """בדיקות הגשת קובץ סטטי swagger-auth.js"""

    @pytest.mark.asyncio
    async def test_auth_js_served(self, test_client):
        """הקובץ swagger-auth.js מוגש מ-/static/"""
        response = await test_client.get("/static/swagger-auth.js")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_auth_js_contains_widget(self, test_client):
        """הקובץ מכיל את לוגיקת הווידג'ט"""
        response = await test_client.get("/static/swagger-auth.js")
        content = response.text
        # ווידג'ט הכניסה המהירה
        assert "qa-api-key" in content
        assert "qa-phone" in content
        assert "qa-otp" in content

    @pytest.mark.asyncio
    async def test_auth_js_sets_bearer_token(self, test_client):
        """הסקריפט מגדיר Bearer token ב-Swagger UI אחרי אימות מוצלח"""
        response = await test_client.get("/static/swagger-auth.js")
        content = response.text
        assert "authActions.authorize" in content
        assert "HTTPBearer" in content

    @pytest.mark.asyncio
    async def test_auth_js_handles_station_picker(self, test_client):
        """הסקריפט מטפל במקרה של ריבוי תחנות (station picker)"""
        response = await test_client.get("/static/swagger-auth.js")
        content = response.text
        assert "qa-stations" in content
        assert "choose_station" in content

    @pytest.mark.asyncio
    async def test_auth_js_escapes_html_in_station_picker(self, test_client):
        """הסקריפט משתמש ב-escapeHtml לשמות תחנות — מניעת XSS"""
        response = await test_client.get("/static/swagger-auth.js")
        content = response.text
        assert "escapeHtml" in content
        # וידוא ש-escapeHtml מיושמת על station_id ו-station_name
        assert "escapeHtml(s.station_id)" in content
        assert "escapeHtml(s.station_name)" in content

    @pytest.mark.asyncio
    async def test_auth_js_escape_handles_quotes(self, test_client):
        """escapeHtml חייבת לטפל גם במירכאות — בטוח לשימוש ב-HTML attributes"""
        response = await test_client.get("/static/swagger-auth.js")
        content = response.text
        # וידוא ש-escapeHtml מטפלת ב-" ו-' (נדרש ל-attribute context)
        assert "&quot;" in content
        assert "&#39;" in content

    @pytest.mark.asyncio
    async def test_auth_js_resets_stations_on_new_otp(self, test_client):
        """בקשת OTP חדשה מאפסת את בורר התחנות מבקשה קודמת"""
        response = await test_client.get("/static/swagger-auth.js")
        content = response.text
        # בתוך onRequestOTP — איפוס _pendingStations
        assert "_pendingStations = null" in content


class TestSwaggerDefaultParams:
    """בדיקה שתבנית ה-HTML כוללת את ברירות המחדל של FastAPI"""

    @pytest.mark.asyncio
    async def test_docs_loads_standalone_preset_script(self, test_client):
        """דף הדוקומנטציה טוען את swagger-ui-standalone-preset.js"""
        response = await test_client.get("/docs")
        assert "swagger-ui-standalone-preset.js" in response.text

    @pytest.mark.asyncio
    async def test_docs_has_deep_linking(self, test_client):
        """deepLinking מופעל — תמיכה בקישורים ישירים ל-endpoints"""
        response = await test_client.get("/docs")
        assert "deepLinking: true" in response.text


class TestRedocStillWorks:
    """בדיקה שדף ReDoc לא נפגע מהשינויים"""

    @pytest.mark.asyncio
    async def test_redoc_returns_html(self, test_client):
        """GET /redoc עדיין עובד"""
        response = await test_client.get("/redoc")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
