-- Analytics v2 extensions (traffic, alerts, patterns)

CREATE TABLE IF NOT EXISTS traffic_hourly (
    hour            TIMESTAMPTZ NOT NULL PRIMARY KEY,
    distinct_icao   INTEGER NOT NULL DEFAULT 0,
    position_count  INTEGER NOT NULL DEFAULT 0,
    military_icao   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS traffic_daily (
    day             DATE NOT NULL PRIMARY KEY,
    distinct_icao   INTEGER NOT NULL DEFAULT 0,
    position_count  INTEGER NOT NULL DEFAULT 0,
    military_icao   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS altitude_bins_hourly (
    hour        TIMESTAMPTZ NOT NULL,
    bin_floor   INTEGER NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour, bin_floor)
);

CREATE TABLE IF NOT EXISTS overnight_activity (
    night_date    DATE NOT NULL,
    icao          CHAR(6) NOT NULL,
    first_seen    TIMESTAMPTZ NOT NULL,
    last_seen     TIMESTAMPTZ NOT NULL,
    callsign      TEXT,
    is_military   BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (night_date, icao, first_seen)
);

CREATE TABLE IF NOT EXISTS squawk_alerts (
    id           BIGSERIAL PRIMARY KEY,
    icao         CHAR(6) NOT NULL,
    squawk       TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    ended_at     TIMESTAMPTZ,
    last_lat     DOUBLE PRECISION,
    last_lon     DOUBLE PRECISION,
    callsign     TEXT,
    icao_type    TEXT,
    is_military  BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_squawk_alerts_active ON squawk_alerts (squawk, started_at DESC) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS privacy_sightings (
    time       TIMESTAMPTZ NOT NULL,
    icao       CHAR(6) NOT NULL,
    flag       TEXT NOT NULL,
    callsign   TEXT,
    lat        DOUBLE PRECISION,
    lon        DOUBLE PRECISION,
    db_flags   SMALLINT DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_privacy_time ON privacy_sightings (time DESC);

CREATE TABLE IF NOT EXISTS gov_hex_ranges (
    range_start INTEGER NOT NULL,
    range_end   INTEGER NOT NULL,
    country     TEXT,
    agency      TEXT,
    PRIMARY KEY (range_start, range_end)
);

CREATE TABLE IF NOT EXISTS government_sightings (
    time       TIMESTAMPTZ NOT NULL,
    icao       CHAR(6) NOT NULL,
    country    TEXT,
    agency     TEXT,
    callsign   TEXT,
    lat        DOUBLE PRECISION,
    lon        DOUBLE PRECISION,
    alt_baro   DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_government_time ON government_sightings (time DESC);

CREATE TABLE IF NOT EXISTS military_type_map (
    icao_type   TEXT PRIMARY KEY,
    role        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS behavior_events (
    id            BIGSERIAL PRIMARY KEY,
    icao          CHAR(6) NOT NULL,
    pattern_type  TEXT NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    ended_at      TIMESTAMPTZ NOT NULL,
    center_lat    DOUBLE PRECISION,
    center_lon    DOUBLE PRECISION,
    cell_id       TEXT,
    confidence    REAL,
    is_military   BOOLEAN DEFAULT FALSE,
    callsign      TEXT,
    icao_type     TEXT,
    metadata      JSONB
);

CREATE INDEX IF NOT EXISTS idx_behavior_events_time ON behavior_events (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_events_type ON behavior_events (pattern_type, started_at DESC);

CREATE TABLE IF NOT EXISTS repeat_visit_signatures (
    icao          CHAR(6) NOT NULL,
    cell_id       TEXT NOT NULL,
    dow           SMALLINT NOT NULL,
    hour_bucket   SMALLINT NOT NULL,
    visit_count   INTEGER NOT NULL DEFAULT 0,
    last_seen     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (icao, cell_id, dow, hour_bucket)
);

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS callsign TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS icao_type TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS point_count INTEGER DEFAULT 0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS track_length_nm DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS max_radius_nm DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS centroid_lat DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS centroid_lon DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS closed BOOLEAN DEFAULT TRUE;

ALTER TABLE military_sightings ADD COLUMN IF NOT EXISTS military_role TEXT;
