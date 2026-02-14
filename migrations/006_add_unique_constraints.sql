-- מיגרציה: הוספת UniqueConstraints למניעת כפילויות
-- ראה: https://github.com/amirbiron/Shipment-bot/issues/181
--
-- station_ledger: מונע חיוב כפול לאותה תחנה + משלוח + סוג רשומה
-- conversation_sessions: מונע sessions כפולים לאותו משתמש + פלטפורמה
--
-- הערה: IF NOT EXISTS לא נתמך ב-ADD CONSTRAINT, לכן עוטפים ב-DO block
--        שבודק אם האילוץ קיים לפני הוספה.
--
-- Rollback:
--   ALTER TABLE station_ledger DROP CONSTRAINT IF EXISTS uq_station_delivery_type;
--   ALTER TABLE conversation_sessions DROP CONSTRAINT IF EXISTS uq_user_platform_session;

-- station_ledger — מניעת כפילות עמלות (בדומה ל-wallet_ledger.uq_courier_delivery_type)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_station_delivery_type'
    ) THEN
        ALTER TABLE station_ledger
            ADD CONSTRAINT uq_station_delivery_type
            UNIQUE (station_id, delivery_id, entry_type);
    END IF;
END
$$;

-- conversation_sessions — מניעת sessions כפולים לאותו משתמש ופלטפורמה
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_user_platform_session'
    ) THEN
        ALTER TABLE conversation_sessions
            ADD CONSTRAINT uq_user_platform_session
            UNIQUE (user_id, platform);
    END IF;
END
$$;
