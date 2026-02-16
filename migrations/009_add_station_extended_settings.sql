-- מיגרציה 009: הגדרות תחנה מורחבות (סעיף 8 - Issue #210)
-- הוספת שדות: description, operating_hours, service_areas, logo_url

ALTER TABLE stations ADD COLUMN IF NOT EXISTS description VARCHAR(500) DEFAULT NULL;
ALTER TABLE stations ADD COLUMN IF NOT EXISTS operating_hours JSONB DEFAULT NULL;
ALTER TABLE stations ADD COLUMN IF NOT EXISTS service_areas JSONB DEFAULT NULL;
ALTER TABLE stations ADD COLUMN IF NOT EXISTS logo_url VARCHAR(500) DEFAULT NULL;
