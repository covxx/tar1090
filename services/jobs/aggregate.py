"""Nightly aggregation: path_cells, records, retention."""
import os
from datetime import datetime, timedelta, timezone

import geohash2
import pg8000.native

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://tar1090:tar1090@127.0.0.1:5432/tar1090",
)
GEOHASH_PRECISION = int(os.environ.get("GEOHASH_PRECISION", "6"))


def _parse_dsn(url):
    from urllib.parse import urlparse
    p = urlparse(url)
    return dict(
        user=p.username or "tar1090",
        password=p.password or "tar1090",
        host=p.hostname or "127.0.0.1",
        port=p.port or 5432,
        database=p.path.lstrip("/") or "tar1090",
    )


def get_conn():
    return pg8000.native.Connection(**_parse_dsn(DATABASE_URL))


def aggregate_path_cells(hours_back: int = 2) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    conn = get_conn()
    try:
        rows = conn.run(
            "SELECT date_trunc('hour', time) AS hour, lat, lon, alt_baro "
            "FROM positions "
            "WHERE time >= :since AND lat IS NOT NULL AND lon IS NOT NULL",
            since=since,
        )
    finally:
        conn.close()

    cells: dict[tuple, list] = {}
    for hour, lat, lon, alt in rows:
        cell_id = geohash2.encode(lat, lon, precision=GEOHASH_PRECISION)
        key = (hour, cell_id)
        cells.setdefault(key, []).append(alt)

    upsert = []
    for (hour, cell_id), alts in cells.items():
        valid = [a for a in alts if a is not None]
        avg_alt = sum(valid) / len(valid) if valid else None
        upsert.append((hour, cell_id, len(alts), avg_alt))

    if not upsert:
        return 0

    conn = get_conn()
    try:
        for hour, cell_id, cnt, avg_alt in upsert:
            conn.run(
                "INSERT INTO path_cells (hour, cell_id, crossing_count, avg_alt) "
                "VALUES (:hour, :cell, :cnt, :alt) "
                "ON CONFLICT (hour, cell_id) DO UPDATE SET "
                "crossing_count = path_cells.crossing_count + EXCLUDED.crossing_count, "
                "avg_alt = COALESCE(EXCLUDED.avg_alt, path_cells.avg_alt)",
                hour=hour, cell=cell_id, cnt=cnt, alt=avg_alt,
            )
        conn.run("COMMIT")
    finally:
        conn.close()
    return len(upsert)


def refresh_records(period: str, days: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0
    conn = get_conn()
    try:
        rows = conn.run(
            "SELECT p.icao, MAX(p.alt_baro), MAX(p.callsign), MAX(p.icao_type) "
            "FROM positions p WHERE p.time >= :since AND p.alt_baro IS NOT NULL "
            "GROUP BY p.icao ORDER BY 2 DESC LIMIT 50",
            since=since,
        )
        for icao, val, callsign, icao_type in rows:
            conn.run(
                "INSERT INTO records (period, category, icao, value, metadata, updated_at) "
                "VALUES (:period, 'highest_alt', :icao, :val, :meta, NOW()) "
                "ON CONFLICT (period, category, icao) DO UPDATE SET "
                "value = EXCLUDED.value, metadata = EXCLUDED.metadata, updated_at = NOW()",
                period=period, icao=icao, val=val,
                meta={"callsign": callsign, "icao_type": icao_type},
            )
            count += 1

        rows = conn.run(
            "SELECT p.icao, MAX(p.gs), MAX(p.callsign), MAX(p.icao_type) "
            "FROM positions p WHERE p.time >= :since AND p.gs IS NOT NULL "
            "GROUP BY p.icao ORDER BY 2 DESC LIMIT 50",
            since=since,
        )
        for icao, val, callsign, icao_type in rows:
            conn.run(
                "INSERT INTO records (period, category, icao, value, metadata, updated_at) "
                "VALUES (:period, 'fastest_gs', :icao, :val, :meta, NOW()) "
                "ON CONFLICT (period, category, icao) DO UPDATE SET "
                "value = EXCLUDED.value, metadata = EXCLUDED.metadata, updated_at = NOW()",
                period=period, icao=icao, val=val,
                meta={"callsign": callsign, "icao_type": icao_type},
            )
            count += 1

        rows = conn.run(
            "SELECT icao, wingspan_m, length_m, registration, icao_type "
            "FROM aircraft_meta WHERE wingspan_m IS NOT NULL "
            "ORDER BY wingspan_m DESC LIMIT 30"
        )
        for icao, ws, ln, reg, itype in rows:
            conn.run(
                "INSERT INTO records (period, category, icao, value, metadata, updated_at) "
                "VALUES (:period, 'largest', :icao, :val, :meta, NOW()) "
                "ON CONFLICT (period, category, icao) DO UPDATE SET "
                "value = EXCLUDED.value, metadata = EXCLUDED.metadata, updated_at = NOW()",
                period=period, icao=icao, val=ws or ln,
                meta={"callsign": reg, "icao_type": itype},
            )
            count += 1

        rows = conn.run(
            "SELECT icao, wingspan_m, length_m, registration, icao_type "
            "FROM aircraft_meta WHERE wingspan_m IS NOT NULL AND wingspan_m > 0 "
            "ORDER BY wingspan_m ASC LIMIT 30"
        )
        for icao, ws, ln, reg, itype in rows:
            conn.run(
                "INSERT INTO records (period, category, icao, value, metadata, updated_at) "
                "VALUES (:period, 'smallest', :icao, :val, :meta, NOW()) "
                "ON CONFLICT (period, category, icao) DO UPDATE SET "
                "value = EXCLUDED.value, metadata = EXCLUDED.metadata, updated_at = NOW()",
                period=period, icao=icao, val=ws or ln,
                meta={"callsign": reg, "icao_type": itype},
            )
            count += 1

        conn.run("COMMIT")
    finally:
        conn.close()
    return count


def run_retention() -> None:
    """Retention for plain PostgreSQL (no TimescaleDB policies)."""
    cutoff_paths = datetime.now(timezone.utc) - timedelta(days=365)
    cutoff_positions = datetime.now(timezone.utc) - timedelta(days=90)
    cutoff_military = datetime.now(timezone.utc) - timedelta(days=365)
    conn = get_conn()
    try:
        conn.run("DELETE FROM positions WHERE time < :t", t=cutoff_positions)
        conn.run("DELETE FROM military_sightings WHERE time < :t", t=cutoff_military)
        conn.run("DELETE FROM path_cells WHERE hour < :t", t=cutoff_paths)
        conn.run("DELETE FROM records WHERE updated_at < :t", t=cutoff_paths)
        conn.run("COMMIT")
    finally:
        conn.close()


def run_all():
    n_paths = aggregate_path_cells(hours_back=2)
    counts = 0
    for period, days in [("day", 1), ("week", 7), ("month", 30)]:
        counts += refresh_records(period, days)
    run_retention()
    return {"path_cells": n_paths, "records_updated": counts}
