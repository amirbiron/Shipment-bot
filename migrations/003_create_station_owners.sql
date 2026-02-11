-- יצירת טבלת station_owners - תמיכה בכמה בעלים לתחנה
CREATE TABLE IF NOT EXISTS station_owners (
    id SERIAL PRIMARY KEY,
    station_id INTEGER NOT NULL REFERENCES stations(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_station_owner UNIQUE (station_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_station_owners_station_id ON station_owners(station_id);
CREATE INDEX IF NOT EXISTS ix_station_owners_user_id ON station_owners(user_id);

-- העברת בעלים קיימים מ-stations.owner_id לטבלה החדשה
INSERT INTO station_owners (station_id, user_id, is_active, created_at)
SELECT id, owner_id, TRUE, created_at
FROM stations
WHERE owner_id IS NOT NULL
  AND is_active = TRUE
ON CONFLICT (station_id, user_id) DO NOTHING;
