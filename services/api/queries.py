"""SQL queries for analytics API."""
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


def overview_stats(cur, since: datetime) -> dict:
    cur.execute(
        """
        SELECT
            COUNT(DISTINCT icao) AS aircraft_seen,
            COUNT(*) FILTER (WHERE is_military) AS military_positions,
            MAX(alt_baro) AS highest_alt
        FROM positions
        WHERE time >= %s AND alt_baro IS NOT NULL
        """,
        (since,),
    )
    row = cur.fetchone()
    cur.execute(
        "SELECT COUNT(DISTINCT icao) FROM military_sightings WHERE time >= %s",
        (since,),
    )
    mil_count = cur.fetchone()[0]
    return {
        "aircraft_seen": row[0] or 0,
        "military_positions": row[1] or 0,
        "highest_alt": row[2],
        "military_aircraft": mil_count or 0,
    }


def leaderboard_highest_alt(cur, since: datetime, limit: int) -> list:
    cur.execute(
        """
        SELECT p.icao, MAX(p.alt_baro) AS val, MAX(p.callsign) AS callsign,
               MAX(p.icao_type) AS icao_type
        FROM positions p
        WHERE p.time >= %s AND p.alt_baro IS NOT NULL
        GROUP BY p.icao
        ORDER BY val DESC
        LIMIT %s
        """,
        (since, limit),
    )
    return _rows(cur)


def leaderboard_fastest(cur, since: datetime, limit: int) -> list:
    cur.execute(
        """
        SELECT p.icao, MAX(p.gs) AS val, MAX(p.callsign) AS callsign,
               MAX(p.icao_type) AS icao_type
        FROM positions p
        WHERE p.time >= %s AND p.gs IS NOT NULL
        GROUP BY p.icao
        ORDER BY val DESC
        LIMIT %s
        """,
        (since, limit),
    )
    return _rows(cur)


def leaderboard_size(cur, since: datetime, limit: int, largest: bool) -> list:
    order = "DESC" if largest else "ASC"
    cur.execute(
        f"""
        SELECT m.icao,
               COALESCE(m.wingspan_m, m.length_m, 0) AS val,
               m.registration AS callsign,
               m.icao_type
        FROM aircraft_meta m
        WHERE m.wingspan_m IS NOT NULL
           OR m.length_m IS NOT NULL
        ORDER BY val {order}
        LIMIT %s
        """,
        (limit,),
    )
    return _rows(cur)


def leaderboard_from_records(cur, period: str, category: str, limit: int) -> list:
    cur.execute(
        """
        SELECT icao, value, metadata->>'callsign' AS callsign,
               metadata->>'icao_type' AS icao_type
        FROM records
        WHERE period = %s AND category = %s
        ORDER BY value DESC
        LIMIT %s
        """,
        (period, category, limit),
    )
    return _rows(cur)


def top_paths(cur, since: datetime, limit: int) -> list:
    cur.execute(
        """
        SELECT cell_id, SUM(crossing_count) AS cnt, AVG(avg_alt) AS avg_alt
        FROM path_cells
        WHERE hour >= %s
        GROUP BY cell_id
        ORDER BY cnt DESC
        LIMIT %s
        """,
        (since, limit),
    )
    rows = cur.fetchall()
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


def _decode_geohash(g: str):
    import geohash2

    return geohash2.decode(g)


def military_sightings(cur, since: datetime, until: datetime, limit: int) -> list:
    cur.execute(
        """
        SELECT time, icao, callsign, icao_type, lat, lon, alt_baro, db_flags
        FROM military_sightings
        WHERE time >= %s AND time <= %s
        ORDER BY time DESC
        LIMIT %s
        """,
        (since, until, limit),
    )
    cols = ["time", "icao", "callsign", "icao_type", "lat", "lon", "alt_baro", "db_flags"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def history_positions(cur, icao: str, since: datetime, until: datetime) -> list:
    cur.execute(
        """
        SELECT time, lat, lon, alt_baro, gs, track, callsign
        FROM positions
        WHERE icao = %s AND time >= %s AND time <= %s
          AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY time ASC
        """,
        (icao.lower(), since, until),
    )
    cols = ["time", "lat", "lon", "alt_baro", "gs", "track", "callsign"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _rows(cur) -> list:
    out = []
    for row in cur.fetchall():
        out.append(
            {
                "icao": row[0],
                "value": float(row[1]) if row[1] is not None else None,
                "callsign": row[2] if len(row) > 2 else None,
                "icao_type": row[3] if len(row) > 3 else None,
            }
        )
    return out
