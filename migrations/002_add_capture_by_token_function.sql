-- Migration: Add capture_delivery_by_token function
-- Date: 2026-01-29
-- Description: Allows capturing delivery using secure token (smart links)

CREATE OR REPLACE FUNCTION capture_delivery_by_token(
    p_token VARCHAR(32),
    p_courier_id INTEGER,
    p_fee DECIMAL DEFAULT 10.00
)
RETURNS TABLE(success BOOLEAN, message TEXT, new_balance DECIMAL, delivery_id INTEGER) AS $$
DECLARE
    v_delivery_id INTEGER;
    v_result RECORD;
BEGIN
    -- Find delivery by token
    SELECT id INTO v_delivery_id
    FROM deliveries
    WHERE token = p_token;

    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, 'Invalid token - delivery not found'::TEXT, 0::DECIMAL, 0;
        RETURN;
    END IF;

    -- Delegate to existing capture_delivery function
    SELECT * INTO v_result
    FROM capture_delivery(v_delivery_id, p_courier_id, p_fee);

    RETURN QUERY SELECT v_result.success, v_result.message, v_result.new_balance, v_delivery_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION capture_delivery_by_token IS 'Capture delivery using secure token (prevents ID guessing)';
