-- מיגרציה: הוספת CHECK constraint לאחוז עמלה בארנק תחנה
-- מגן על טווח 6%–12% גם מעדכונים ישירים (admin scripts וכו')
--
-- Rollback:
--   ALTER TABLE station_wallets DROP CONSTRAINT IF EXISTS ck_station_wallets_commission_rate_range;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_station_wallets_commission_rate_range'
    ) THEN
        ALTER TABLE station_wallets
            ADD CONSTRAINT ck_station_wallets_commission_rate_range
            CHECK (commission_rate >= 0.06 AND commission_rate <= 0.12);
    END IF;
END
$$;
