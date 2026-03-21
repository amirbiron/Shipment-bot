-- מיגרציה 015: הוספת עמודות תפוגה למשלוחים + username לטלגרם

ALTER TABLE deliveries
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS expiry_warning_sent TIMESTAMP;

CREATE INDEX IF NOT EXISTS ix_deliveries_expires_at
    ON deliveries(expires_at);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS telegram_username VARCHAR(100);
