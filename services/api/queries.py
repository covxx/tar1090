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
