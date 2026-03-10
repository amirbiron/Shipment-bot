-- מיגרציה 014: הרחבת טבלת audit_logs למערכת audit מקיפה
-- הוספת שדות entity, old/new values, והפיכת station_id ל-nullable

-- station_id הופך ל-nullable — פעולות כמו אישור שליח לא קשורות לתחנה
ALTER TABLE audit_logs ALTER COLUMN station_id DROP NOT NULL;

-- שדות חדשים לזיהוי ישות ומעקב שינויים
ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS entity_type VARCHAR(50),
    ADD COLUMN IF NOT EXISTS entity_id BIGINT,
    ADD COLUMN IF NOT EXISTS old_value JSONB,
    ADD COLUMN IF NOT EXISTS new_value JSONB;

-- אינדקסים לחיפוש לפי ישות
CREATE INDEX IF NOT EXISTS ix_audit_logs_entity_type ON audit_logs(entity_type);
CREATE INDEX IF NOT EXISTS ix_audit_logs_entity ON audit_logs(entity_type, entity_id, created_at DESC);
