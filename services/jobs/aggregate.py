"""Nightly aggregation: path_cells, records, retention."""
import os
from datetime import datetime, timedelta, timezone

import geohash2
import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://tar1090:tar1090@timescaledb:5432/tar1090",
)
GEOHASH_PRECISION = int(os.environ.get("GEOHASH_PRECISION", "6"))


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def aggregate_path_cells(hours_back: int = 2) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT date_trunc('hour', time) AS hour,
                       lat, lon, alt_baro
                FROM positions
                WHERE time >= %s AND lat IS NOT NULL AND lon IS NOT NULL
                """,
                (since,),
            )
            rows = cur.fetchall()

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

    sql = """
        INSERT INTO path_cells (hour, cell_id, crossing_count, avg_alt)
        VALUES %s
        ON CONFLICT (hour, cell_id) DO UPDATE SET
            crossing_count = path_cells.crossing_count + EXCLUDED.crossing_count,
            avg_alt = COALESCE(EXCLUDED.avg_alt, path_cells.avg_alt)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, upsert, page_size=500)
        conn.commit()
    return len(upsert)


def refresh_records(period: str, days: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            # highest altitude
            cur.execute(
                """
                SELECT p.icao, MAX(p.alt_baro), MAX(p.callsign), MAX(p.icao_type)
                FROM positions p
                WHERE p.time >= %s AND p.alt_baro IS NOT NULL
                GROUP BY p.icao ORDER BY 2 DESC LIMIT 50
                """,
                (since,),
            )
            for icao, val, callsign, icao_type in cur.fetchall():
                cur.execute(
                    """
                    INSERT INTO records (period, category, icao, value, metadata, updated_at)
                    VALUES (%s, 'highest_alt', %s, %s, %s, NOW())
                    ON CONFLICT (period, category, icao) DO UPDATE SET
                        value = EXCLUDED.value, metadata = EXCLUDED.metadata, updated_at = NOW()
                    """,
                    (
                        period,
                        icao,
                        val,
                        {"callsign": callsign, "icao_type": icao_type},
                    ),
                )
                count += 1

            cur.execute(
                """
                SELECT p.icao, MAX(p.gs), MAX(p.callsign), MAX(p.icao_type)
                FROM positions p
                WHERE p.time >= %s AND p.gs IS NOT NULL
                GROUP BY p.icao ORDER BY 2 DESC LIMIT 50
                """,
                (since,),
            )
            for icao, val, callsign, icao_type in cur.fetchall():
                cur.execute(
                    """
                    INSERT INTO records (period, category, icao, value, metadata, updated_at)
                    VALUES (%s, 'fastest_gs', %s, %s, %s, NOW())
                    ON CONFLICT (period, category, icao) DO UPDATE SET
                        value = EXCLUDED.value, metadata = EXCLUDED.metadata, updated_at = NOW()
                    """,
                    (
                        period,
                        icao,
                        val,
                        {"callsign": callsign, "icao_type": icao_type},
                    ),
                )
                count += 1

            # largest / smallest from meta
            cur.execute(
                """
                SELECT icao, wingspan_m, length_m, registration, icao_type
                FROM aircraft_meta
                WHERE wingspan_m IS NOT NULL
                ORDER BY wingspan_m DESC LIMIT 30
                """
            )
            for icao, ws, ln, reg, itype in cur.fetchall():
                cur.execute(
                    """
                    INSERT INTO records (period, category, icao, value, metadata, updated_at)
                    VALUES (%s, 'largest', %s, %s, %s, NOW())
                    ON CONFLICT (period, category, icao) DO UPDATE SET
                        value = EXCLUDED.value, metadata = EXCLUDED.metadata, updated_at = NOW()
                    """,
                    (period, icao, ws or ln, {"callsign": reg, "icao_type": itype}),
                )
                count += 1

            cur.execute(
                """
                SELECT icao, wingspan_m, length_m, registration, icao_type
                FROM aircraft_meta
                WHERE wingspan_m IS NOT NULL AND wingspan_m > 0
                ORDER BY wingspan_m ASC LIMIT 30
                """
            )
            for icao, ws, ln, reg, itype in cur.fetchall():
                cur.execute(
                    """
                    INSERT INTO records (period, category, icao, value, metadata, updated_at)
                    VALUES (%s, 'smallest', %s, %s, %s, NOW())
                    ON CONFLICT (period, category, icao) DO UPDATE SET
                        value = EXCLUDED.value, metadata = EXCLUDED.metadata, updated_at = NOW()
                    """,
                    (period, icao, ws or ln, {"callsign": reg, "icao_type": itype}),
                )
                count += 1

        conn.commit()
    return count


def run_retention() -> None:
    """Retention for plain PostgreSQL (no TimescaleDB policies)."""
    cutoff_paths = datetime.now(timezone.utc) - timedelta(days=365)
    cutoff_positions = datetime.now(timezone.utc) - timedelta(days=90)
    cutoff_military = datetime.now(timezone.utc) - timedelta(days=365)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM positions WHERE time < %s", (cutoff_positions,))
            cur.execute("DELETE FROM military_sightings WHERE time < %s", (cutoff_military,))
            cur.execute("DELETE FROM path_cells WHERE hour < %s", (cutoff_paths,))
            cur.execute("DELETE FROM records WHERE updated_at < %s", (cutoff_paths,))
        conn.commit()


def run_all():
    n_paths = aggregate_path_cells(hours_back=2)
    counts = 0
    for period, days in [("day", 1), ("week", 7), ("month", 30)]:
        counts += refresh_records(period, days)
    run_retention()
    return {"path_cells": n_paths, "records_updated": counts}
