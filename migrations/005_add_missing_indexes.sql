-- מיגרציה: הוספת אינדקסים חסרים על foreign keys קריטיים
-- מונע full table scans בשאילתות תכופות
-- ראה: https://github.com/amirbiron/Shipment-bot/issues/180

-- טבלת deliveries — שליפת משלוחים לפי שולח, שליח, ושליח מבקש
CREATE INDEX IF NOT EXISTS idx_deliveries_sender_id ON deliveries(sender_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_courier_id ON deliveries(courier_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_requesting_courier_id ON deliveries(requesting_courier_id);

-- טבלת stations — שליפת תחנות לפי בעלים וסינון תחנות פעילות
CREATE INDEX IF NOT EXISTS idx_stations_owner_id ON stations(owner_id);
-- אינדקס חלקי — רק תחנות פעילות, מכיוון שרוב השאילתות מסננות is_active = TRUE
CREATE INDEX IF NOT EXISTS idx_stations_is_active ON stations(id) WHERE is_active = TRUE;

-- טבלת outbox_messages — polling לניסיונות חוזרים וסינון לפי נמען
-- אינדקס חלקי — רק הודעות שממתינות לשליחה חוזרת
CREATE INDEX IF NOT EXISTS idx_outbox_next_retry ON outbox_messages(next_retry_at)
    WHERE status IN ('pending', 'failed');
CREATE INDEX IF NOT EXISTS idx_outbox_recipient_id ON outbox_messages(recipient_id);

-- טבלת users — סינון לפי תפקיד
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
