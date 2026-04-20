-- מיגרציה 017: הוספת עמודת external_user_id לטבלת users
-- תומכת ב-BSUID של Meta Cloud API (Business-Scoped User ID) לקראת שינוי הזהות ביוני 2026.
-- Meta מגדירים עד 128 תווים אחרי "CC." (2 אותיות מדינה + נקודה) — סה"כ עד 131; שמים גבול בטוח 150.
-- UNIQUE יוצר אינדקס אוטומטית ב-PostgreSQL — אסור אינדקס כפול על UNIQUE (ראה CLAUDE.md).

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS external_user_id VARCHAR(150);

-- אילוץ ייחודיות — נדחה לרמת טבלה כדי לתמוך ב-NULL מרובים (מותר ב-UNIQUE של PostgreSQL).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_external_user_id_key'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_external_user_id_key UNIQUE (external_user_id);
    END IF;
END $$;
