-- מיגרציה 016: הוספת עמודת subscription_expires_at לטבלת users (מנוי שליח)
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP;
