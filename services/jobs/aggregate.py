"""Aggregation: path_cells, records, traffic rollups, retention."""
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import geohash2
import pg8000.native

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://tar1090:tar1090@127.0.0.1:5432/tar1090",
)
GEOHASH_PRECISION = int(os.environ.get("GEOHASH_PRECISION", "6"))
PATH_HOURS_BACK = int(os.environ.get("PATH_HOURS_BACK", "24"))
ANALYTICS_TZ = os.environ.get("ANALYTICS_TZ", "UTC")
ALT_BINS = [0, 1000, 3000, 10000, 18000, 25000, 40000]


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


def aggregate_path_cells(hours_back: int = PATH_HOURS_BACK) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    conn = get_conn()
    try:
        rows = conn.run(
            "SELECT date_trunc('hour', time) AS hour, lat, lon, alt_baro "
            "FROM positions WHERE time >= :since AND lat IS NOT NULL AND lon IS NOT NULL",
            since=since,
        )
    finally:
        conn.close()

    cells: dict[tuple, list] = {}
    for hour, lat, lon, alt in rows:
        cell_id = geohash2.encode(lat, lon, precision=GEOHASH_PRECISION)
        key = (hour, cell_id)
        cells.setdefault(key, []).append(alt)

    if not cells:
        return 0

    conn = get_conn()
    try:
        for (hour, cell_id), alts in cells.items():
            valid = [a for a in alts if a is not None]
            avg_alt = sum(valid) / len(valid) if valid else None
            conn.run(
                "INSERT INTO path_cells (hour, cell_id, crossing_count, avg_alt) "
                "VALUES (:hour, :cell, :cnt, :alt) "
                "ON CONFLICT (hour, cell_id) DO UPDATE SET "
                "crossing_count = path_cells.crossing_count + EXCLUDED.crossing_count, "
                "avg_alt = COALESCE(EXCLUDED.avg_alt, path_cells.avg_alt)",
                hour=hour, cell=cell_id, cnt=len(alts), alt=avg_alt,
            )
        conn.run("COMMIT")
    finally:
        conn.close()
    return len(cells)


def aggregate_traffic_hourly(hours_back: int = 48) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    conn = get_conn()
    try:
        rows = conn.run(
            "SELECT date_trunc('hour', time) AS hour, COUNT(DISTINCT icao), COUNT(*), "
            "COUNT(DISTINCT icao) FILTER (WHERE is_military) "
            "FROM positions WHERE time >= :since GROUP BY 1",
            since=since,
        )
        for hour, dist, cnt, mil in rows:
            conn.run(
                "INSERT INTO traffic_hourly (hour, distinct_icao, position_count, military_icao) "
                "VALUES (:h,:d,:c,:m) ON CONFLICT (hour) DO UPDATE SET "
                "distinct_icao=EXCLUDED.distinct_icao, position_count=EXCLUDED.position_count, "
                "military_icao=EXCLUDED.military_icao",
                h=hour, d=dist, c=cnt, m=mil or 0,
            )
        conn.run("COMMIT")
    finally:
        conn.close()
    return len(rows)


def aggregate_traffic_daily(days_back: int = 30) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=days_back)
    conn = get_conn()
    try:
        rows = conn.run(
            "SELECT date_trunc('day', time AT TIME ZONE :tz)::date AS day, "
            "COUNT(DISTINCT icao), COUNT(*), COUNT(DISTINCT icao) FILTER (WHERE is_military) "
            "FROM positions WHERE time >= :since GROUP BY 1",
            since=since, tz=ANALYTICS_TZ,
        )
        for day, dist, cnt, mil in rows:
            conn.run(
                "INSERT INTO traffic_daily (day, distinct_icao, position_count, military_icao) "
                "VALUES (:d,:di,:c,:m) ON CONFLICT (day) DO UPDATE SET "
                "distinct_icao=EXCLUDED.distinct_icao, position_count=EXCLUDED.position_count, "
                "military_icao=EXCLUDED.military_icao",
                d=day, di=dist, c=cnt, m=mil or 0,
            )
        conn.run("COMMIT")
    finally:
        conn.close()
    return len(rows)


def aggregate_altitude_bins(hours_back: int = 48) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    conn = get_conn()
    count = 0
    try:
        rows = conn.run(
            "SELECT date_trunc('hour', time) AS hour, alt_baro FROM positions "
            "WHERE time >= :since AND alt_baro IS NOT NULL",
            since=since,
        )
        bins: dict[tuple, int] = defaultdict(int)
        for hour, alt in rows:
            bf = 0
            for b in ALT_BINS:
                if alt >= b:
                    bf = b
            bins[(hour, bf)] += 1
        for (hour, bf), cnt in bins.items():
            conn.run(
                "INSERT INTO altitude_bins_hourly (hour, bin_floor, count) VALUES (:h,:b,:c) "
                "ON CONFLICT (hour, bin_floor) DO UPDATE SET count=EXCLUDED.count",
                h=hour, b=bf, c=cnt,
            )
            count += 1
        conn.run("COMMIT")
    finally:
        conn.close()
    return count


def aggregate_overnight() -> int:
    try:
        tz = ZoneInfo(ANALYTICS_TZ)
    except Exception:
        tz = timezone.utc
    conn = get_conn()
    count = 0
    try:
        rows = conn.run(
            "SELECT icao, first_seen, last_seen, callsign, is_military FROM sessions "
            "WHERE last_seen >= NOW() - INTERVAL '2 days'"
        )
        for icao, fs, ls, cs, mil in rows:
            fs_local = fs.astimezone(tz) if fs.tzinfo else fs.replace(tzinfo=timezone.utc).astimezone(tz)
            hour = fs_local.hour
            if not (hour >= 22 or hour < 6):
                continue
            night_date = fs_local.date()
            if hour < 6:
                night_date = (fs_local - timedelta(days=1)).date()
            conn.run(
                "INSERT INTO overnight_activity (night_date, icao, first_seen, last_seen, callsign, is_military) "
                "VALUES (:nd,:icao,:fs,:ls,:cs,:mil) ON CONFLICT DO NOTHING",
                nd=night_date, icao=icao, fs=fs, ls=ls, cs=cs, mil=mil,
            )
            count += 1
        conn.run("COMMIT")
    finally:
        conn.close()
    return count


def refresh_military_by_role(period: str, days: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    conn = get_conn()
    count = 0
    try:
        rows = conn.run(
            "SELECT COALESCE(military_role, 'other') AS role, COUNT(*) FROM military_sightings "
            "WHERE time >= :since GROUP BY 1",
            since=since,
        )
        for role, cnt in rows:
            conn.run(
                "INSERT INTO records (period, category, icao, value, metadata, updated_at) "
                "VALUES (:period, :cat, :icao, :val, :meta, NOW()) "
                "ON CONFLICT (period, category, icao) DO UPDATE SET value=EXCLUDED.value, metadata=EXCLUDED.metadata",
                period=period, cat=f"mil_role_{role}", icao="summary", val=float(cnt),
                meta={"role": role},
            )
            count += 1
        conn.run("COMMIT")
    finally:
        conn.close()
    return count


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
            "FROM aircraft_meta WHERE wingspan_m IS NOT NULL ORDER BY wingspan_m DESC LIMIT 30"
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
    cutoff_paths = datetime.now(timezone.utc) - timedelta(days=365)
    cutoff_positions = datetime.now(timezone.utc) - timedelta(days=90)
    cutoff_military = datetime.now(timezone.utc) - timedelta(days=365)
    conn = get_conn()
    try:
        conn.run("DELETE FROM positions WHERE time < :t", t=cutoff_positions)
        conn.run("DELETE FROM military_sightings WHERE time < :t", t=cutoff_military)
        conn.run("DELETE FROM path_cells WHERE hour < :t", t=cutoff_paths)
        conn.run("DELETE FROM records WHERE updated_at < :t", t=cutoff_paths)
        conn.run("DELETE FROM privacy_sightings WHERE time < :t", t=cutoff_military)
        conn.run("DELETE FROM government_sightings WHERE time < :t", t=cutoff_military)
        conn.run("DELETE FROM behavior_events WHERE started_at < :t", t=cutoff_military)
        conn.run("COMMIT")
    finally:
        conn.close()


def run_all():
    from patterns import detect_patterns

    n_paths = aggregate_path_cells()
    n_hourly = aggregate_traffic_hourly()
    n_daily = aggregate_traffic_daily()
    n_alt = aggregate_altitude_bins()
    n_overnight = aggregate_overnight()
    counts = 0
    for period, days in [("day", 1), ("week", 7), ("month", 30)]:
        counts += refresh_records(period, days)
        counts += refresh_military_by_role(period, days)
    pattern_result = detect_patterns()
    run_retention()
    return {
        "path_cells": n_paths,
        "traffic_hourly": n_hourly,
        "traffic_daily": n_daily,
        "altitude_bins": n_alt,
        "overnight": n_overnight,
        "records_updated": counts,
        **pattern_result,
    }
