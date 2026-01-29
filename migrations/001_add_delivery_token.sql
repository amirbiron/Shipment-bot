-- Migration: Add secure token field to deliveries table
-- Date: 2026-01-29
-- Description: Adds a unique token for smart links (prevents ID guessing)

-- Step 1: Add the column (nullable first for existing rows)
ALTER TABLE deliveries
ADD COLUMN IF NOT EXISTS token VARCHAR(32);

-- Step 2: Generate tokens for existing deliveries
UPDATE deliveries
SET token = encode(gen_random_bytes(16), 'base64')
WHERE token IS NULL;

-- Step 3: Add NOT NULL constraint and UNIQUE constraint
ALTER TABLE deliveries
ALTER COLUMN token SET NOT NULL;

ALTER TABLE deliveries
ALTER COLUMN token SET DEFAULT encode(gen_random_bytes(16), 'base64');

-- Add unique constraint (if not exists - PostgreSQL 9.5+)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'deliveries_token_key'
    ) THEN
        ALTER TABLE deliveries ADD CONSTRAINT deliveries_token_key UNIQUE (token);
    END IF;
END$$;

-- Step 4: Create index for fast lookup
CREATE INDEX IF NOT EXISTS idx_deliveries_token ON deliveries(token);

-- Verify migration
DO $$
DECLARE
    v_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM deliveries WHERE token IS NULL;
    IF v_count > 0 THEN
        RAISE EXCEPTION 'Migration failed: % deliveries without token', v_count;
    END IF;
    RAISE NOTICE 'Migration successful: All deliveries have tokens';
END$$;
