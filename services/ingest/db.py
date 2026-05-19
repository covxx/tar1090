"""Database helpers for ingest service."""
import os
import json
from contextlib import contextmanager

import pg8000.native

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://tar1090:tar1090@127.0.0.1:5432/tar1090",
)


def _parse_dsn(url):
    """Parse postgresql://user:pass@host:port/dbname into pg8000 kwargs."""
    from urllib.parse import urlparse
    p = urlparse(url)
    return dict(
        user=p.username or "tar1090",
        password=p.password or "tar1090",
        host=p.hostname or "127.0.0.1",
        port=p.port or 5432,
        database=p.path.lstrip("/") or "tar1090",
    )


def _get_raw_conn():
    return pg8000.native.Connection(**_parse_dsn(DATABASE_URL))


@contextmanager
def get_conn():
    conn = _get_raw_conn()
    try:
        yield conn
        conn.run("COMMIT")
    except Exception:
        conn.run("ROLLBACK")
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
        conn.run(sql)


def insert_positions(rows: list[tuple]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        for r in rows:
            conn.run(
                "INSERT INTO positions "
                "(time, icao, callsign, lat, lon, alt_baro, alt_geom, gs, track, "
                "squawk, icao_type, wtc, db_flags, is_military, source) "
                "VALUES (:p1,:p2,:p3,:p4,:p5,:p6,:p7,:p8,:p9,:p10,:p11,:p12,:p13,:p14,:p15)",
                p1=r[0], p2=r[1], p3=r[2], p4=r[3], p5=r[4],
                p6=r[5], p7=r[6], p8=r[7], p9=r[8], p10=r[9],
                p11=r[10], p12=r[11], p13=r[12], p14=r[13], p15=r[14],
            )


def upsert_meta(icao, registration, icao_type, manufacturer, model, length_m, wingspan_m):
    with get_conn() as conn:
        conn.run(
            "INSERT INTO aircraft_meta (icao, registration, icao_type, manufacturer, model, length_m, wingspan_m, updated_at) "
            "VALUES (:icao,:reg,:itype,:mfr,:model,:len,:ws, NOW()) "
            "ON CONFLICT (icao) DO UPDATE SET "
            "registration = COALESCE(EXCLUDED.registration, aircraft_meta.registration), "
            "icao_type = COALESCE(EXCLUDED.icao_type, aircraft_meta.icao_type), "
            "manufacturer = COALESCE(EXCLUDED.manufacturer, aircraft_meta.manufacturer), "
            "model = COALESCE(EXCLUDED.model, aircraft_meta.model), "
            "length_m = COALESCE(EXCLUDED.length_m, aircraft_meta.length_m), "
            "wingspan_m = COALESCE(EXCLUDED.wingspan_m, aircraft_meta.wingspan_m), "
            "updated_at = NOW()",
            icao=icao, reg=registration, itype=icao_type, mfr=manufacturer,
            model=model, len=length_m, ws=wingspan_m,
        )


def insert_military_sightings(rows: list[tuple]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        for r in rows:
            conn.run(
                "INSERT INTO military_sightings (time, icao, callsign, icao_type, lat, lon, alt_baro, db_flags) "
                "VALUES (:p1,:p2,:p3,:p4,:p5,:p6,:p7,:p8)",
                p1=r[0], p2=r[1], p3=r[2], p4=r[3],
                p5=r[4], p6=r[5], p7=r[6], p8=r[7],
            )
