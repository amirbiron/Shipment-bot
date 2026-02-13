-- מיגרציה: המרת עמודות כספיות מ-FLOAT ל-NUMERIC(10,2)
-- מונע אובדן דיוק בחישובים פיננסיים
-- ראה: https://github.com/amirbiron/Shipment-bot/issues/178

-- ארנק שליח
ALTER TABLE courier_wallets
    ALTER COLUMN balance TYPE NUMERIC(10,2) USING balance::NUMERIC(10,2),
    ALTER COLUMN credit_limit TYPE NUMERIC(10,2) USING credit_limit::NUMERIC(10,2);

-- היסטוריית תנועות שליח
ALTER TABLE wallet_ledger
    ALTER COLUMN amount TYPE NUMERIC(10,2) USING amount::NUMERIC(10,2),
    ALTER COLUMN balance_after TYPE NUMERIC(10,2) USING balance_after::NUMERIC(10,2);

-- ארנק תחנה
ALTER TABLE station_wallets
    ALTER COLUMN balance TYPE NUMERIC(10,2) USING balance::NUMERIC(10,2),
    ALTER COLUMN commission_rate TYPE NUMERIC(10,2) USING commission_rate::NUMERIC(10,2);

-- היסטוריית תנועות תחנה
ALTER TABLE station_ledger
    ALTER COLUMN amount TYPE NUMERIC(10,2) USING amount::NUMERIC(10,2),
    ALTER COLUMN balance_after TYPE NUMERIC(10,2) USING balance_after::NUMERIC(10,2);

-- חיוב ידני
ALTER TABLE manual_charges
    ALTER COLUMN amount TYPE NUMERIC(10,2) USING amount::NUMERIC(10,2);

-- משלוחים
ALTER TABLE deliveries
    ALTER COLUMN fee TYPE NUMERIC(10,2) USING fee::NUMERIC(10,2);
