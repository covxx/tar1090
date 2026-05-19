"""SQL queries for analytics API — pg8000 native interface."""
import json
from datetime import datetime, timedelta, timezone

PERIOD_MAP = {
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
    "all": timedelta(days=3650),
}


def period_start(period: str) -> datetime:
    delta = PERIOD_MAP.get(period, PERIOD_MAP["day"])
    return datetime.now(timezone.utc) - delta


def overview_stats(conn, since: datetime) -> dict:
    rows = conn.run(
        "SELECT COUNT(DISTINCT icao) AS aircraft_seen, "
        "COUNT(*) FILTER (WHERE is_military) AS military_positions, "
        "MAX(alt_baro) AS highest_alt "
        "FROM positions WHERE time >= :since AND alt_baro IS NOT NULL",
        since=since,
    )
    row = rows[0] if rows else (0, 0, None)
    mil_rows = conn.run(
        "SELECT COUNT(DISTINCT icao) FROM military_sightings WHERE time >= :since",
        since=since,
    )
    mil_count = mil_rows[0][0] if mil_rows else 0
    return {
        "aircraft_seen": row[0] or 0,
        "military_positions": row[1] or 0,
        "highest_alt": row[2],
        "military_aircraft": mil_count or 0,
    }


def leaderboard_highest_alt(conn, since: datetime, limit: int) -> list:
    rows = conn.run(
        "SELECT p.icao, MAX(p.alt_baro) AS val, MAX(p.callsign) AS callsign, "
        "MAX(p.icao_type) AS icao_type "
        "FROM positions p WHERE p.time >= :since AND p.alt_baro IS NOT NULL "
        "GROUP BY p.icao ORDER BY val DESC LIMIT :lim",
        since=since, lim=limit,
    )
    return _rows(rows)


def leaderboard_fastest(conn, since: datetime, limit: int) -> list:
    rows = conn.run(
        "SELECT p.icao, MAX(p.gs) AS val, MAX(p.callsign) AS callsign, "
        "MAX(p.icao_type) AS icao_type "
        "FROM positions p WHERE p.time >= :since AND p.gs IS NOT NULL "
        "GROUP BY p.icao ORDER BY val DESC LIMIT :lim",
        since=since, lim=limit,
    )
    return _rows(rows)


def leaderboard_size(conn, since: datetime, limit: int, largest: bool) -> list:
    order = "DESC" if largest else "ASC"
    rows = conn.run(
        f"SELECT m.icao, COALESCE(m.wingspan_m, m.length_m, 0) AS val, "
        f"m.registration AS callsign, m.icao_type "
        f"FROM aircraft_meta m "
        f"WHERE m.wingspan_m IS NOT NULL OR m.length_m IS NOT NULL "
        f"ORDER BY val {order} LIMIT :lim",
        lim=limit,
    )
    return _rows(rows)


def leaderboard_from_records(conn, period: str, category: str, limit: int) -> list:
    rows = conn.run(
        "SELECT icao, value, metadata->>'callsign' AS callsign, "
        "metadata->>'icao_type' AS icao_type "
        "FROM records WHERE period = :period AND category = :cat "
        "ORDER BY value DESC LIMIT :lim",
        period=period, cat=category, lim=limit,
    )
    return _rows(rows)


def top_paths(conn, since: datetime, limit: int) -> list:
    rows = conn.run(
        "SELECT cell_id, SUM(crossing_count) AS cnt, AVG(avg_alt) AS avg_alt "
        "FROM path_cells WHERE hour >= :since "
        "GROUP BY cell_id ORDER BY cnt DESC LIMIT :lim",
        since=since, lim=limit,
    )
    features = []
    for row in rows:
        cell_id, cnt, avg_alt = row[0], row[1], row[2]
        try:
            lat, lon, _, _ = _decode_geohash(cell_id)
        except Exception:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "cell_id": cell_id,
                    "crossing_count": int(cnt),
                    "avg_alt": float(avg_alt) if avg_alt else None,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _decode_geohash(g: str):
    import geohash2
    return geohash2.decode(g)


def military_sightings(conn, since: datetime, until: datetime, limit: int) -> list:
    rows = conn.run(
        "SELECT time, icao, callsign, icao_type, lat, lon, alt_baro, db_flags "
        "FROM military_sightings WHERE time >= :since AND time <= :until "
        "ORDER BY time DESC LIMIT :lim",
        since=since, until=until, lim=limit,
    )
    cols = ["time", "icao", "callsign", "icao_type", "lat", "lon", "alt_baro", "db_flags"]
    return [dict(zip(cols, r)) for r in rows]


def history_positions(conn, icao: str, since: datetime, until: datetime) -> list:
    rows = conn.run(
        "SELECT time, lat, lon, alt_baro, gs, track, callsign "
        "FROM positions WHERE icao = :icao AND time >= :since AND time <= :until "
        "AND lat IS NOT NULL AND lon IS NOT NULL ORDER BY time ASC",
        icao=icao.lower(), since=since, until=until,
    )
    cols = ["time", "lat", "lon", "alt_baro", "gs", "track", "callsign"]
    return [dict(zip(cols, r)) for r in rows]


def _rows(raw) -> list:
    out = []
    for row in raw:
        out.append(
            {
                "icao": row[0],
                "value": float(row[1]) if row[1] is not None else None,
                "callsign": row[2] if len(row) > 2 else None,
                "icao_type": row[3] if len(row) > 3 else None,
            }
        )
    return out


def traffic_trends(conn, granularity: str, since: datetime) -> list:
    table = "traffic_hourly" if granularity == "hour" else "traffic_daily"
    col = "hour" if granularity == "hour" else "day"
    rows = conn.run(
        f"SELECT {col}, distinct_icao, position_count, military_icao FROM {table} "
        f"WHERE {col} >= :since ORDER BY {col}",
        since=since.date() if granularity == "day" and hasattr(since, "date") else since,
    )
    return [
        {
            "t": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
            "distinct_icao": r[1],
            "position_count": r[2],
            "military_icao": r[3],
        }
        for r in rows
    ]


def peak_hours(conn, since: datetime, tz: str = "UTC") -> list:
    rows = conn.run(
        "SELECT EXTRACT(HOUR FROM hour AT TIME ZONE :tz)::int AS h, "
        "SUM(distinct_icao) FROM traffic_hourly WHERE hour >= :since GROUP BY 1 ORDER BY 1",
        since=since, tz=tz,
    )
    return [{"hour": int(r[0]), "count": int(r[1] or 0)} for r in rows]


def paths_heatmap(conn, since: datetime, limit: int) -> dict:
    rows = conn.run(
        "SELECT cell_id, SUM(crossing_count) AS cnt, AVG(avg_alt) AS avg_alt "
        "FROM path_cells WHERE hour >= :since GROUP BY cell_id ORDER BY cnt DESC LIMIT :lim",
        since=since, lim=limit,
    )
    features = []
    for cell_id, cnt, avg_alt in rows:
        try:
            lat, lon, _, _ = _decode_geohash(cell_id)
        except Exception:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "cell_id": cell_id,
                    "crossing_count": int(cnt),
                    "avg_alt": float(avg_alt) if avg_alt else None,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def altitude_histogram(conn, since: datetime) -> dict:
    rows = conn.run(
        "SELECT bin_floor, SUM(count) FROM altitude_bins_hourly WHERE hour >= :since GROUP BY 1 ORDER BY 1",
        since=since,
    )
    bins = [{"bin_floor": int(r[0]), "count": int(r[1])} for r in rows]
    labels = {
        0: "ground/low",
        1000: "GA (<3000ft)",
        3000: "low (<10000ft)",
        10000: "medium",
        18000: "high",
        25000: "commercial",
        40000: "very high",
    }
    for b in bins:
        b["label"] = labels.get(b["bin_floor"], str(b["bin_floor"]))
    return {"bins": bins}


def overnight_list(conn, night_date) -> list:
    rows = conn.run(
        "SELECT icao, first_seen, last_seen, callsign, is_military FROM overnight_activity "
        "WHERE night_date = :nd ORDER BY first_seen",
        nd=night_date,
    )
    return [
        {
            "icao": r[0],
            "first_seen": r[1].isoformat() if r[1] else None,
            "last_seen": r[2].isoformat() if r[2] else None,
            "callsign": r[3],
            "is_military": r[4],
        }
        for r in rows
    ]


def military_by_role(conn, since: datetime) -> list:
    rows = conn.run(
        "SELECT COALESCE(military_role, 'other'), COUNT(*) FROM military_sightings "
        "WHERE time >= :since GROUP BY 1 ORDER BY 2 DESC",
        since=since,
    )
    return [{"role": r[0], "count": int(r[1])} for r in rows]


def privacy_sightings_list(conn, flag: str | None, since: datetime, limit: int) -> list:
    q = "SELECT time, icao, flag, callsign, lat, lon FROM privacy_sightings WHERE time >= :since"
    params = {"since": since, "lim": limit}
    if flag:
        q += " AND flag = :flag"
        params["flag"] = flag
    q += " ORDER BY time DESC LIMIT :lim"
    rows = conn.run(q, **params)
    return [
        {
            "time": r[0].isoformat() if r[0] else None,
            "icao": r[1],
            "flag": r[2],
            "callsign": r[3],
            "lat": r[4],
            "lon": r[5],
        }
        for r in rows
    ]


def squawk_alerts_list(conn, code: str | None, active: bool, since: datetime, limit: int) -> list:
    q = "SELECT id, icao, squawk, started_at, ended_at, callsign, icao_type, last_lat, last_lon, is_military "
    q += "FROM squawk_alerts WHERE started_at >= :since"
    params = {"since": since, "lim": limit}
    if code:
        q += " AND squawk = :code"
        params["code"] = code
    if active:
        q += " AND ended_at IS NULL"
    q += " ORDER BY started_at DESC LIMIT :lim"
    rows = conn.run(q, **params)
    out = []
    for r in rows:
        out.append({
            "id": r[0], "icao": r[1], "squawk": r[2],
            "started_at": r[3].isoformat() if r[3] else None,
            "ended_at": r[4].isoformat() if r[4] else None,
            "callsign": r[5], "icao_type": r[6],
            "lat": r[7], "lon": r[8], "is_military": r[9],
        })
    return out


def government_list(conn, since: datetime, limit: int) -> list:
    rows = conn.run(
        "SELECT time, icao, country, agency, callsign, lat, lon, alt_baro "
        "FROM government_sightings WHERE time >= :since ORDER BY time DESC LIMIT :lim",
        since=since, lim=limit,
    )
    return [
        {
            "time": r[0].isoformat() if r[0] else None,
            "icao": r[1], "country": r[2], "agency": r[3],
            "callsign": r[4], "lat": r[5], "lon": r[6], "alt_baro": r[7],
        }
        for r in rows
    ]


def behavior_events_list(conn, pattern_type: str | None, since: datetime, limit: int) -> list:
    q = (
        "SELECT id, icao, pattern_type, started_at, ended_at, center_lat, center_lon, "
        "confidence, is_military, callsign, icao_type, metadata FROM behavior_events WHERE started_at >= :since"
    )
    params = {"since": since, "lim": limit}
    if pattern_type:
        q += " AND pattern_type = :pt"
        params["pt"] = pattern_type
    q += " ORDER BY started_at DESC LIMIT :lim"
    rows = conn.run(q, **params)
    out = []
    for r in rows:
        out.append({
            "id": r[0], "icao": r[1], "pattern_type": r[2],
            "started_at": r[3].isoformat() if r[3] else None,
            "ended_at": r[4].isoformat() if r[4] else None,
            "center_lat": r[5], "center_lon": r[6],
            "confidence": r[7], "is_military": r[8],
            "callsign": r[9], "icao_type": r[10],
            "metadata": r[11],
        })
    return out


def behavior_event_detail(conn, event_id: int) -> dict | None:
    rows = conn.run(
        "SELECT id, icao, pattern_type, started_at, ended_at, center_lat, center_lon, "
        "confidence, is_military, callsign, icao_type, metadata FROM behavior_events WHERE id = :id",
        id=event_id,
    )
    if not rows:
        return None
    r = rows[0]
    points = history_positions(conn, r[1], r[3], r[4])
    coords = [[p["lon"], p["lat"]] for p in points if p.get("lat") is not None]
    return {
        "id": r[0], "icao": r[1], "pattern_type": r[2],
        "started_at": r[3].isoformat() if r[3] else None,
        "ended_at": r[4].isoformat() if r[4] else None,
        "center_lat": r[5], "center_lon": r[6],
        "confidence": r[7], "is_military": r[8],
        "callsign": r[9], "icao_type": r[10], "metadata": r[11],
        "track": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}},
    }


def repeat_visits_list(conn, since: datetime, min_visits: int) -> list:
    rows = conn.run(
        "SELECT icao, cell_id, dow, hour_bucket, visit_count, last_seen FROM repeat_visit_signatures "
        "WHERE visit_count >= :mv AND last_seen >= :since ORDER BY visit_count DESC LIMIT 100",
        mv=min_visits, since=since,
    )
    out = []
    for r in rows:
        try:
            lat, lon, _, _ = _decode_geohash(r[1])
        except Exception:
            lat, lon = None, None
        out.append({
            "icao": r[0], "cell_id": r[1], "dow": r[2], "hour_bucket": r[3],
            "visit_count": r[4],
            "last_seen": r[5].isoformat() if r[5] else None,
            "lat": lat, "lon": lon,
        })
    return out


def patterns_summary(conn, since: datetime) -> dict:
    rows = conn.run(
        "SELECT pattern_type, COUNT(*) FROM behavior_events WHERE started_at >= :since GROUP BY 1",
        since=since,
    )
    return {r[0]: int(r[1]) for r in rows}
