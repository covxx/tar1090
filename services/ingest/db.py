"""Database helpers for ingest service."""
import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://tar1090:tar1090@timescaledb:5432/tar1090",
)


@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema():
    schema_path = os.environ.get("SCHEMA_PATH", "/app/schema.sql")
    if not os.path.isfile(schema_path):
        schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
    with open(schema_path, encoding="utf-8") as f:
        sql = f.read()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


def insert_positions(rows: list[tuple]) -> None:
    if not rows:
        return
    sql = """
        INSERT INTO positions (
            time, icao, callsign, lat, lon, alt_baro, alt_geom, gs, track,
            squawk, icao_type, wtc, db_flags, is_military, source
        ) VALUES %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=500)


def upsert_meta(icao: str, registration, icao_type, manufacturer, model, length_m, wingspan_m):
    sql = """
        INSERT INTO aircraft_meta (icao, registration, icao_type, manufacturer, model, length_m, wingspan_m, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (icao) DO UPDATE SET
            registration = COALESCE(EXCLUDED.registration, aircraft_meta.registration),
            icao_type = COALESCE(EXCLUDED.icao_type, aircraft_meta.icao_type),
            manufacturer = COALESCE(EXCLUDED.manufacturer, aircraft_meta.manufacturer),
            model = COALESCE(EXCLUDED.model, aircraft_meta.model),
            length_m = COALESCE(EXCLUDED.length_m, aircraft_meta.length_m),
            wingspan_m = COALESCE(EXCLUDED.wingspan_m, aircraft_meta.wingspan_m),
            updated_at = NOW()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (icao, registration, icao_type, manufacturer, model, length_m, wingspan_m))


def insert_military_sightings(rows: list[tuple]) -> None:
    if not rows:
        return
    sql = """
        INSERT INTO military_sightings (time, icao, callsign, icao_type, lat, lon, alt_baro, db_flags)
        VALUES %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=200)
