-- מיגרציה: הוספת אינדקס חסר על approved_by_id בטבלת deliveries
-- ה-FK הזה נוסף בשלב 4 ללא אינדקס, מה שגורם ל-full table scan
-- בשאילתות שמסננות לפי סדרן מאשר.
-- ראה: https://github.com/amirbiron/Shipment-bot/issues/191
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_deliveries_approved_by;

CREATE INDEX IF NOT EXISTS idx_deliveries_approved_by ON deliveries(approved_by_id);
