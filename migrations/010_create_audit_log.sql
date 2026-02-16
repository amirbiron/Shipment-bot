-- מיגרציה 010: יצירת טבלת audit_logs — לוג ביקורת לפעולות מנהלתיות
-- תיעוד "מי שינה מה מ-X ל-Y" — חיוני לתחנות עם מספר בעלים

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    station_id INTEGER NOT NULL REFERENCES stations(id),
    actor_user_id BIGINT NOT NULL REFERENCES users(id),
    action VARCHAR(50) NOT NULL,
    target_user_id BIGINT REFERENCES users(id),
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- אינדקסים לשאילתות נפוצות
CREATE INDEX IF NOT EXISTS ix_audit_logs_station_id ON audit_logs(station_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_user_id ON audit_logs(actor_user_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs(created_at);
-- אינדקס מורכב לסינון לפי תחנה וזמן (שאילתת ברירת מחדל בפאנל)
CREATE INDEX IF NOT EXISTS ix_audit_logs_station_created ON audit_logs(station_id, created_at DESC);
