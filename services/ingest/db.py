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


def _run_sql_file(conn, path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt and not stmt.startswith("--"):
            try:
                conn.run(stmt)
            except Exception:
                pass


def init_schema():
    schema_path = os.environ.get("SCHEMA_PATH", "/app/schema.sql")
    if not os.path.isfile(schema_path):
        schema_path = os.path.join(os.path.dirname(__file__), "..", "schema-plain.sql")
    v2_path = os.path.join(os.path.dirname(schema_path), "schema-v2.sql")
    if not os.path.isfile(v2_path):
        v2_path = os.path.join(os.path.dirname(__file__), "..", "schema-v2.sql")
    with get_conn() as conn:
        _run_sql_file(conn, schema_path)
        _run_sql_file(conn, v2_path)


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
                "INSERT INTO military_sightings (time, icao, callsign, icao_type, lat, lon, alt_baro, db_flags, military_role) "
                "VALUES (:p1,:p2,:p3,:p4,:p5,:p6,:p7,:p8,:p9)",
                p1=r[0], p2=r[1], p3=r[2], p4=r[3],
                p5=r[4], p6=r[5], p7=r[6], p8=r[7], p9=r[8] if len(r) > 8 else None,
            )


def insert_session(row: tuple) -> None:
    with get_conn() as conn:
        conn.run(
            "INSERT INTO sessions (icao, first_seen, last_seen, max_alt, distance_nm, is_military, "
            "callsign, icao_type, point_count, track_length_nm, max_radius_nm, centroid_lat, centroid_lon, closed) "
            "VALUES (:icao,:fs,:ls,:ma,:dn,:mil,:cs,:it,:pc,:tl,:mr,:clat,:clon,TRUE) "
            "ON CONFLICT (icao, first_seen) DO NOTHING",
            icao=row[0], fs=row[1], ls=row[2], ma=row[3], dn=row[4], mil=row[5],
            cs=row[6], it=row[7], pc=row[8], tl=row[9], mr=row[10],
            clat=row[11], clon=row[12],
        )


def insert_privacy_sightings(rows: list[tuple]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        for r in rows:
            conn.run(
                "INSERT INTO privacy_sightings (time, icao, flag, callsign, lat, lon, db_flags) "
                "VALUES (:p1,:p2,:p3,:p4,:p5,:p6,:p7)",
                p1=r[0], p2=r[1], p3=r[2], p4=r[3], p5=r[4], p6=r[5], p7=r[6],
            )


def insert_government_sightings(rows: list[tuple]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        for r in rows:
            conn.run(
                "INSERT INTO government_sightings (time, icao, country, agency, callsign, lat, lon, alt_baro) "
                "VALUES (:p1,:p2,:p3,:p4,:p5,:p6,:p7,:p8)",
                p1=r[0], p2=r[1], p3=r[2], p4=r[3], p5=r[4], p6=r[5], p7=r[6], p8=r[7],
            )


def upsert_squawk_alert(icao: str, squawk: str, started_at, callsign, icao_type, lat, lon, is_military: bool):
    with get_conn() as conn:
        conn.run(
            "INSERT INTO squawk_alerts (icao, squawk, started_at, callsign, icao_type, last_lat, last_lon, is_military) "
            "VALUES (:icao,:sq,:st,:cs,:it,:lat,:lon,:mil)",
            icao=icao, sq=squawk, st=started_at, cs=callsign, it=icao_type,
            lat=lat, lon=lon, mil=is_military,
        )


def update_squawk_alert(alert_id: int, lat, lon, callsign):
    with get_conn() as conn:
        conn.run(
            "UPDATE squawk_alerts SET last_lat=:lat, last_lon=:lon, callsign=COALESCE(:cs, callsign) WHERE id=:id",
            lat=lat, lon=lon, cs=callsign, id=alert_id,
        )


def close_squawk_alerts(icao: str, squawk: str | None, ended_at):
    with get_conn() as conn:
        if squawk:
            conn.run(
                "UPDATE squawk_alerts SET ended_at=:ea WHERE icao=:icao AND squawk=:sq AND ended_at IS NULL",
                ea=ended_at, icao=icao, sq=squawk,
            )
        else:
            conn.run(
                "UPDATE squawk_alerts SET ended_at=:ea WHERE icao=:icao AND ended_at IS NULL",
                ea=ended_at, icao=icao,
            )


def get_active_squawk_alert(icao: str, squawk: str):
    with get_conn() as conn:
        rows = conn.run(
            "SELECT id FROM squawk_alerts WHERE icao=:icao AND squawk=:sq AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
            icao=icao, sq=squawk,
        )
        return rows[0][0] if rows else None


def seed_gov_ranges(rows: list[tuple]) -> None:
    with get_conn() as conn:
        for r in rows:
            conn.run(
                "INSERT INTO gov_hex_ranges (range_start, range_end, country, agency) "
                "VALUES (:a,:b,:c,:ag) ON CONFLICT (range_start, range_end) DO NOTHING",
                a=r[0], b=r[1], c=r[2], ag=r[3],
            )


def seed_military_type_map(rows: list[tuple]) -> None:
    with get_conn() as conn:
        for r in rows:
            conn.run(
                "INSERT INTO military_type_map (icao_type, role) VALUES (:t,:role) "
                "ON CONFLICT (icao_type) DO UPDATE SET role=EXCLUDED.role",
                t=r[0], role=r[1],
            )
