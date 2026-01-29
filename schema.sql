-- Shipment Bot Database Schema
-- PostgreSQL 14+

-- Enable UUID extension (optional)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =====================================================
-- ENUM Types
-- =====================================================

CREATE TYPE user_role AS ENUM ('sender', 'courier', 'admin');
CREATE TYPE delivery_status AS ENUM ('open', 'captured', 'in_transit', 'delivered', 'cancelled');
CREATE TYPE ledger_type AS ENUM ('delivery_fee_debit', 'payment', 'bonus', 'refund', 'adjustment');
CREATE TYPE message_platform AS ENUM ('whatsapp', 'telegram');
CREATE TYPE message_status AS ENUM ('pending', 'processing', 'sent', 'failed');

-- =====================================================
-- Users Table
-- =====================================================

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    phone_number VARCHAR(20) UNIQUE,
    telegram_chat_id VARCHAR(50) UNIQUE,
    name VARCHAR(100),
    role user_role NOT NULL DEFAULT 'sender',
    platform VARCHAR(20) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_platform ON users(platform);
CREATE INDEX idx_users_phone ON users(phone_number);
CREATE INDEX idx_users_telegram ON users(telegram_chat_id);

COMMENT ON TABLE users IS 'All system users - senders, couriers, and admins';

-- =====================================================
-- Deliveries Table
-- =====================================================

CREATE TABLE deliveries (
    id SERIAL PRIMARY KEY,
    sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    courier_id INTEGER REFERENCES users(id) ON DELETE SET NULL,

    -- Pickup information
    pickup_address TEXT NOT NULL,
    pickup_latitude DECIMAL(10, 8),
    pickup_longitude DECIMAL(11, 8),
    pickup_contact_name VARCHAR(100),
    pickup_contact_phone VARCHAR(20),

    -- Dropoff information
    dropoff_address TEXT NOT NULL,
    dropoff_latitude DECIMAL(10, 8),
    dropoff_longitude DECIMAL(11, 8),
    dropoff_contact_name VARCHAR(100),
    dropoff_contact_phone VARCHAR(20),

    -- Status and pricing
    status delivery_status DEFAULT 'open',
    fee DECIMAL(10, 2) DEFAULT 10.00,
    notes TEXT,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    captured_at TIMESTAMP WITH TIME ZONE,
    delivered_at TIMESTAMP WITH TIME ZONE,
    cancelled_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_deliveries_status ON deliveries(status);
CREATE INDEX idx_deliveries_sender ON deliveries(sender_id);
CREATE INDEX idx_deliveries_courier ON deliveries(courier_id);
CREATE INDEX idx_deliveries_created ON deliveries(created_at DESC);

COMMENT ON TABLE deliveries IS 'Shipment records with full pickup/dropoff details';

-- =====================================================
-- Courier Wallets Table
-- =====================================================

CREATE TABLE courier_wallets (
    id SERIAL PRIMARY KEY,
    courier_id INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    balance DECIMAL(10, 2) DEFAULT 0.00,
    credit_limit DECIMAL(10, 2) DEFAULT -100.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_wallets_courier ON courier_wallets(courier_id);

COMMENT ON TABLE courier_wallets IS 'Current balance per courier with credit limit';

-- =====================================================
-- Wallet Ledger Table
-- =====================================================

CREATE TABLE wallet_ledger (
    id SERIAL PRIMARY KEY,
    courier_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    delivery_id INTEGER REFERENCES deliveries(id) ON DELETE SET NULL,
    type ledger_type NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    balance_after DECIMAL(10, 2) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    -- Prevent duplicate charges for same delivery
    CONSTRAINT unique_delivery_charge UNIQUE(courier_id, delivery_id, type)
);

CREATE INDEX idx_ledger_courier ON wallet_ledger(courier_id);
CREATE INDEX idx_ledger_delivery ON wallet_ledger(delivery_id);
CREATE INDEX idx_ledger_created ON wallet_ledger(created_at DESC);

COMMENT ON TABLE wallet_ledger IS 'Immutable transaction history preventing double-debit';

-- =====================================================
-- Conversation Sessions Table
-- =====================================================

CREATE TABLE conversation_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform VARCHAR(20) NOT NULL,
    current_state VARCHAR(100) NOT NULL DEFAULT 'initial',
    context JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_user_platform UNIQUE(user_id, platform)
);

CREATE INDEX idx_sessions_user ON conversation_sessions(user_id);
CREATE INDEX idx_sessions_state ON conversation_sessions(current_state);

COMMENT ON TABLE conversation_sessions IS 'Per-user state machine tracking for conversation flows';

-- =====================================================
-- Outbox Messages Table (Transactional Outbox Pattern)
-- =====================================================

CREATE TABLE outbox_messages (
    id SERIAL PRIMARY KEY,
    platform message_platform NOT NULL,
    recipient_id VARCHAR(50) NOT NULL,
    message_type VARCHAR(50) NOT NULL,
    message_content JSONB NOT NULL,
    status message_status DEFAULT 'pending',
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP WITH TIME ZONE,
    next_retry_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT
);

CREATE INDEX idx_outbox_status ON outbox_messages(status);
CREATE INDEX idx_outbox_next_retry ON outbox_messages(next_retry_at)
    WHERE status = 'pending' OR status = 'failed';
CREATE INDEX idx_outbox_created ON outbox_messages(created_at DESC);

COMMENT ON TABLE outbox_messages IS 'Pending broadcasts with retry tracking for reliable messaging';

-- =====================================================
-- Broadcast Messages Table (For tracking sent broadcasts)
-- =====================================================

CREATE TABLE broadcast_messages (
    id SERIAL PRIMARY KEY,
    delivery_id INTEGER REFERENCES deliveries(id) ON DELETE CASCADE,
    message_text TEXT NOT NULL,
    total_recipients INTEGER DEFAULT 0,
    successful_sends INTEGER DEFAULT 0,
    failed_sends INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_broadcast_delivery ON broadcast_messages(delivery_id);

COMMENT ON TABLE broadcast_messages IS 'Track broadcast campaigns to couriers';

-- =====================================================
-- Functions
-- =====================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply triggers for updated_at
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_wallets_updated_at
    BEFORE UPDATE ON courier_wallets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_sessions_updated_at
    BEFORE UPDATE ON conversation_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =====================================================
-- Function for Atomic Delivery Capture
-- =====================================================

CREATE OR REPLACE FUNCTION capture_delivery(
    p_delivery_id INTEGER,
    p_courier_id INTEGER,
    p_fee DECIMAL DEFAULT 10.00
)
RETURNS TABLE(success BOOLEAN, message TEXT, new_balance DECIMAL) AS $$
DECLARE
    v_delivery deliveries%ROWTYPE;
    v_wallet courier_wallets%ROWTYPE;
    v_new_balance DECIMAL;
BEGIN
    -- Lock delivery row
    SELECT * INTO v_delivery
    FROM deliveries
    WHERE id = p_delivery_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, 'Delivery not found'::TEXT, 0::DECIMAL;
        RETURN;
    END IF;

    IF v_delivery.status != 'open' THEN
        RETURN QUERY SELECT FALSE, 'Delivery is not available'::TEXT, 0::DECIMAL;
        RETURN;
    END IF;

    -- Lock wallet row
    SELECT * INTO v_wallet
    FROM courier_wallets
    WHERE courier_id = p_courier_id
    FOR UPDATE;

    IF NOT FOUND THEN
        -- Create wallet if not exists
        INSERT INTO courier_wallets (courier_id, balance)
        VALUES (p_courier_id, 0)
        RETURNING * INTO v_wallet;
    END IF;

    -- Check credit limit
    v_new_balance := v_wallet.balance - p_fee;
    IF v_new_balance < v_wallet.credit_limit THEN
        RETURN QUERY SELECT FALSE, 'Insufficient credit'::TEXT, v_wallet.balance;
        RETURN;
    END IF;

    -- Update delivery status
    UPDATE deliveries
    SET status = 'captured',
        courier_id = p_courier_id,
        captured_at = CURRENT_TIMESTAMP
    WHERE id = p_delivery_id;

    -- Update wallet balance
    UPDATE courier_wallets
    SET balance = v_new_balance
    WHERE courier_id = p_courier_id;

    -- Insert ledger entry
    INSERT INTO wallet_ledger (courier_id, delivery_id, type, amount, balance_after, description)
    VALUES (p_courier_id, p_delivery_id, 'delivery_fee_debit', -p_fee, v_new_balance, 'Delivery capture fee');

    RETURN QUERY SELECT TRUE, 'Delivery captured successfully'::TEXT, v_new_balance;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION capture_delivery IS 'Atomic delivery capture with credit check and wallet debit';

-- =====================================================
-- Sample Data (Optional - for testing)
-- =====================================================

-- Uncomment to insert sample data:

-- INSERT INTO users (phone_number, name, role, platform) VALUES
-- ('0501234567', 'ישראל ישראלי', 'sender', 'whatsapp'),
-- ('0509876543', 'יוסי השליח', 'courier', 'telegram'),
-- ('0521111111', 'מנהל המערכת', 'admin', 'whatsapp');

-- INSERT INTO courier_wallets (courier_id, balance) VALUES
-- (2, 50.00);
