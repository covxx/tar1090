"""Behavior pattern detection from sessions and positions."""
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import geohash2
import pg8000.native

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://tar1090:tar1090@127.0.0.1:5432/tar1090",
)
GEOHASH_PRECISION = int(os.environ.get("GEOHASH_PRECISION", "6"))
ANALYTICS_TZ = os.environ.get("ANALYTICS_TZ", "UTC")
PATTERN_MIN_POINTS = int(os.environ.get("PATTERN_MIN_POINTS", "20"))
PATTERN_LOITER_RADIUS_NM = float(os.environ.get("PATTERN_LOITER_RADIUS_NM", "3"))
PATTERN_REPEAT_MIN = int(os.environ.get("PATTERN_REPEAT_MIN", "3"))
PATTERN_REPEAT_DAYS = int(os.environ.get("PATTERN_REPEAT_DAYS", "28"))


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


def _haversine_nm(lat1, lon1, lat2, lon2):
    r = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _heading_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _load_session_track(conn, icao: str, first_seen, last_seen):
    rows = conn.run(
        "SELECT lat, lon, alt_baro, track, time FROM positions "
        "WHERE icao=:icao AND time >= :fs AND time <= :ls AND lat IS NOT NULL ORDER BY time",
        icao=icao, fs=first_seen, ls=last_seen,
    )
    return rows


def _insert_event(conn, icao, ptype, started, ended, clat, clon, cell_id, conf, mil, cs, itype, meta):
    conn.run(
        "INSERT INTO behavior_events (icao, pattern_type, started_at, ended_at, center_lat, center_lon, "
        "cell_id, confidence, is_military, callsign, icao_type, metadata) "
        "VALUES (:icao,:pt,:st,:en,:clat,:clon,:cell,:conf,:mil,:cs,:it,:meta)",
        icao=icao, pt=ptype, st=started, en=ended, clat=clat, clon=clon,
        cell=cell_id, conf=conf, mil=mil, cs=cs, it=itype, meta=json.dumps(meta),
    )


def _detect_loiter(pts, max_radius_nm, min_minutes=12):
    if len(pts) < PATTERN_MIN_POINTS:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    clat, clon = sum(lats) / len(lats), sum(lons) / len(lons)
    max_r = max(_haversine_nm(clat, clon, la, lo) for la, lo in zip(lats, lons))
    if max_r > max_radius_nm:
        return None
    dist = 0.0
    for i in range(1, len(pts)):
        dist += _haversine_nm(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
    straight = _haversine_nm(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1])
    if straight < 0.5:
        straight = 0.5
    ratio = dist / straight
    if ratio < 2.5:
        return None
    duration_min = (len(pts) * 8) / 60.0
    if duration_min < min_minutes:
        return None
    return clat, clon, min(0.95, ratio / 6), {"max_radius_nm": max_r, "path_ratio": ratio}


def _detect_racetrack(pts):
    headings = [p[3] for p in pts if p[3] is not None]
    if len(headings) < 10:
        return None
    buckets = defaultdict(int)
    for h in headings:
        buckets[int(h / 30) * 30] += 1
    top = sorted(buckets.items(), key=lambda x: -x[1])[:2]
    if len(top) < 2:
        return None
    h1, h2 = top[0][0], top[1][0]
    if _heading_diff(h1, h2) < 140:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    clat, clon = sum(lats) / len(lats), sum(lons) / len(lons)
    lat_span = max(lats) - min(lats)
    lon_span = max(lons) - min(lons)
    aspect = max(lat_span, lon_span) / (min(lat_span, lon_span) + 1e-6)
    if aspect < 1.8:
        return None
    return clat, clon, 0.75, {"headings": [h1, h2], "aspect": aspect}


def _detect_alt_oscillation(pts):
    alts = [p[2] for p in pts if p[2] is not None]
    if len(alts) < 8:
        return None
    reversals = 0
    direction = 0
    for i in range(1, len(alts)):
        d = alts[i] - alts[i - 1]
        if abs(d) < 200:
            continue
        sign = 1 if d > 0 else -1
        if direction and sign != direction:
            reversals += 1
        direction = sign
    if reversals < 4:
        return None
    alt_range = max(alts) - min(alts)
    if alt_range < 1500:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    disp = _haversine_nm(lats[0], lons[0], lats[-1], lons[-1])
    if disp > 5:
        return None
    clat, clon = sum(lats) / len(lats), sum(lons) / len(lons)
    return clat, clon, min(0.9, reversals / 8), {"reversals": reversals, "alt_range_ft": alt_range}


def _aggregate_repeat_visits(conn) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=PATTERN_REPEAT_DAYS)
    rows = conn.run(
        "SELECT icao, first_seen, centroid_lat, centroid_lon FROM sessions "
        "WHERE first_seen >= :since AND centroid_lat IS NOT NULL",
        since=since,
    )
    sigs = defaultdict(list)
    for icao, first_seen, clat, clon in rows:
        if clat is None or clon is None:
            continue
        cell = geohash2.encode(clat, clon, precision=GEOHASH_PRECISION)
        dow = first_seen.weekday()
        hour = first_seen.hour
        sigs[(icao, cell, dow, hour)].append(first_seen)
    count = 0
    for (icao, cell, dow, hour), times in sigs.items():
        if len(times) < PATTERN_REPEAT_MIN:
            continue
        conn.run(
            "INSERT INTO repeat_visit_signatures (icao, cell_id, dow, hour_bucket, visit_count, last_seen) "
            "VALUES (:icao,:cell,:dow,:hr,:cnt,:ls) "
            "ON CONFLICT (icao, cell_id, dow, hour_bucket) DO UPDATE SET "
            "visit_count=EXCLUDED.visit_count, last_seen=EXCLUDED.last_seen",
            icao=icao, cell=cell, dow=dow, hr=hour, cnt=len(times), ls=max(times),
        )
        count += 1
    return count


def detect_patterns(hours_back: int = 48) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    conn = get_conn()
    events = 0
    try:
        sessions = conn.run(
            "SELECT icao, first_seen, last_seen, is_military, callsign, icao_type, "
            "point_count, max_radius_nm FROM sessions WHERE last_seen >= :since AND point_count >= :mp",
            since=since, mp=PATTERN_MIN_POINTS,
        )
        for row in sessions:
            icao, fs, ls, mil, cs, itype, pc, _mr = row
            track = _load_session_track(conn, icao, fs, ls)
            if len(track) < PATTERN_MIN_POINTS:
                continue
            pts = [(r[0], r[1], r[2], r[3]) for r in track]
            duration = (ls - fs).total_seconds() / 60 if hasattr(ls - fs, "total_seconds") else 15
            if duration < 8:
                continue
            cell = geohash2.encode(pts[0][0], pts[0][1], precision=GEOHASH_PRECISION) if pts else None

            loiter = _detect_loiter(pts, PATTERN_LOITER_RADIUS_NM)
            if loiter:
                clat, clon, conf, meta = loiter
                _insert_event(conn, icao, "loiter", fs, ls, clat, clon, cell, conf, mil, cs, itype, meta)
                events += 1
                continue

            rt = _detect_racetrack(pts)
            if rt:
                clat, clon, conf, meta = rt
                _insert_event(conn, icao, "racetrack", fs, ls, clat, clon, cell, conf, mil, cs, itype, meta)
                events += 1
                continue

            alt_o = _detect_alt_oscillation(pts)
            if alt_o:
                clat, clon, conf, meta = alt_o
                _insert_event(conn, icao, "alt_oscillation", fs, ls, clat, clon, cell, conf, mil, cs, itype, meta)
                events += 1

        repeats = _aggregate_repeat_visits(conn)
        conn.run("COMMIT")
    finally:
        conn.close()
    return {"behavior_events": events, "repeat_signatures": repeats}
