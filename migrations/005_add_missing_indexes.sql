-- מיגרציה: הוספת אינדקסים חסרים על foreign keys קריטיים
-- מונע full table scans בשאילתות תכופות
-- ראה: https://github.com/amirbiron/Shipment-bot/issues/180
--
-- הערה: אינדקסים שכבר קיימים ב-schema.sql או במיגרציות קודמות לא נכללים כאן:
--   deliveries(sender_id)           — idx_deliveries_sender (schema.sql)
--   deliveries(courier_id)          — idx_deliveries_courier (schema.sql)
--   deliveries(requesting_courier_id) — idx_deliveries_requesting_courier (migration 006)
--   outbox_messages(next_retry_at)  — idx_outbox_next_retry (schema.sql, partial)
--   users(role)                     — idx_users_role (schema.sql)
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_stations_owner_id;
--   DROP INDEX IF EXISTS idx_stations_active_owner;
--   DROP INDEX IF EXISTS idx_outbox_recipient_id;

-- טבלת stations — שליפת תחנות לפי בעלים
CREATE INDEX IF NOT EXISTS idx_stations_owner_id ON stations(owner_id);

-- אינדקס חלקי — תחנות פעילות לפי בעלים (שאילתה נפוצה: "מצא תחנות פעילות של בעלים X")
CREATE INDEX IF NOT EXISTS idx_stations_active_owner ON stations(owner_id) WHERE is_active = TRUE;

-- טבלת outbox_messages — סינון הודעות לפי נמען
CREATE INDEX IF NOT EXISTS idx_outbox_recipient_id ON outbox_messages(recipient_id);
