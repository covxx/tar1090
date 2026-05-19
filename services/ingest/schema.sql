-- TimescaleDB schema for tar1090 analytics

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS positions (
    time        TIMESTAMPTZ NOT NULL,
    icao        CHAR(6) NOT NULL,
    callsign    TEXT,
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    alt_baro    DOUBLE PRECISION,
    alt_geom    DOUBLE PRECISION,
    gs          DOUBLE PRECISION,
    track       DOUBLE PRECISION,
    squawk      TEXT,
    icao_type   TEXT,
    wtc         TEXT,
    db_flags    SMALLINT DEFAULT 0,
    is_military BOOLEAN DEFAULT FALSE,
    source      TEXT
);

SELECT create_hypertable('positions', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_positions_icao_time ON positions (icao, time DESC);
CREATE INDEX IF NOT EXISTS idx_positions_military_time ON positions (is_military, time DESC) WHERE is_military;

CREATE TABLE IF NOT EXISTS aircraft_meta (
    icao          CHAR(6) PRIMARY KEY,
    registration  TEXT,
    icao_type     TEXT,
    manufacturer  TEXT,
    model         TEXT,
    length_m      DOUBLE PRECISION,
    wingspan_m    DOUBLE PRECISION,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS path_cells (
    hour           TIMESTAMPTZ NOT NULL,
    cell_id        TEXT NOT NULL,
    crossing_count INTEGER DEFAULT 0,
    avg_alt        DOUBLE PRECISION,
    PRIMARY KEY (hour, cell_id)
);

SELECT create_hypertable('path_cells', 'hour', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS records (
    period     TEXT NOT NULL,
    category   TEXT NOT NULL,
    icao       CHAR(6) NOT NULL,
    value      DOUBLE PRECISION NOT NULL,
    metadata   JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (period, category, icao)
);

CREATE TABLE IF NOT EXISTS military_sightings (
    time       TIMESTAMPTZ NOT NULL,
    icao       CHAR(6) NOT NULL,
    callsign   TEXT,
    icao_type  TEXT,
    lat        DOUBLE PRECISION,
    lon        DOUBLE PRECISION,
    alt_baro   DOUBLE PRECISION,
    db_flags   SMALLINT DEFAULT 0
);

SELECT create_hypertable('military_sightings', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS sessions (
    icao         CHAR(6) NOT NULL,
    first_seen   TIMESTAMPTZ NOT NULL,
    last_seen    TIMESTAMPTZ NOT NULL,
    max_alt      DOUBLE PRECISION,
    distance_nm  DOUBLE PRECISION,
    is_military  BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (icao, first_seen)
);

CREATE TABLE IF NOT EXISTS photo_cache (
    icao        CHAR(6) PRIMARY KEY,
    thumb_url   TEXT,
    link_url    TEXT,
    photographer TEXT,
    fetched_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Retention: raw positions 90 days
SELECT add_retention_policy('positions', INTERVAL '90 days', if_not_exists => TRUE);

-- Military sightings 12 months
SELECT add_retention_policy('military_sightings', INTERVAL '365 days', if_not_exists => TRUE);
