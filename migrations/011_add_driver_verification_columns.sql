-- מיגרציה: הוספת עמודות אימות לטבלת driver_profiles (סשן 3)
-- הוספת שדות לשמירת קבצי אימות (סלפי, ת.ז.) וסיבת דחייה

ALTER TABLE driver_profiles
    ADD COLUMN IF NOT EXISTS verification_selfie_file_id TEXT,
    ADD COLUMN IF NOT EXISTS verification_id_file_id TEXT,
    ADD COLUMN IF NOT EXISTS rejection_reason TEXT;
