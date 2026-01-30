-- Migration: Add courier registration fields
-- Date: 2026-01-30
-- Description: Add fields for courier registration flow (full_name, approval_status, etc.)

-- Create approval_status enum type
DO $$ BEGIN
    CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected', 'blocked');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add new columns to users table
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS full_name VARCHAR(150),
    ADD COLUMN IF NOT EXISTS approval_status approval_status,
    ADD COLUMN IF NOT EXISTS id_document_url TEXT,
    ADD COLUMN IF NOT EXISTS service_area VARCHAR(100),
    ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMP WITH TIME ZONE;

-- Create index on approval_status for faster queries
CREATE INDEX IF NOT EXISTS idx_users_approval_status ON users(approval_status);

-- Update credit_limit default to -500 (500₪ credit)
ALTER TABLE courier_wallets
    ALTER COLUMN credit_limit SET DEFAULT -500.00;

COMMENT ON COLUMN users.full_name IS 'Legal name as appears on ID document';
COMMENT ON COLUMN users.approval_status IS 'Courier approval status (pending/approved/rejected/blocked)';
COMMENT ON COLUMN users.id_document_url IS 'Path or file_id of uploaded ID/license document';
COMMENT ON COLUMN users.service_area IS 'Geographic area where courier operates';
COMMENT ON COLUMN users.terms_accepted_at IS 'When courier accepted terms and conditions';
