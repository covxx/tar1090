"""Analytics REST API for tar1090."""
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import pg8000.native
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from queries import (
    altitude_histogram,
    behavior_event_detail,
    behavior_events_list,
    government_list,
    history_positions,
    leaderboard_fastest,
    leaderboard_from_records,
    leaderboard_highest_alt,
    leaderboard_size,
    military_by_role,
    military_sightings,
    overnight_list,
    overview_stats,
    paths_heatmap,
    patterns_summary,
    peak_hours,
    period_start,
    privacy_sightings_list,
    repeat_visits_list,
    squawk_alerts_list,
    top_paths,
    traffic_trends,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://tar1090:tar1090@127.0.0.1:5432/tar1090",
)
PLANESPOTTERS_URL = os.environ.get(
    "PLANESPOTTERS_URL",
    "https://api.planespotters.net/pub/photos/hex/",
)
PHOTO_CACHE_DIR = os.environ.get("PHOTO_CACHE_DIR", "/cache/photos")
API_CACHE_TTL = int(os.environ.get("API_CACHE_TTL", "20"))

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, dict]] = {}

app = FastAPI(title="tar1090 Analytics API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def _cache_get(cache_key: tuple):
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(cache_key)
        if not hit:
            return None
        expires_at, payload = hit
        if expires_at < now:
            _CACHE.pop(cache_key, None)
            return None
        return payload


def _cache_set(cache_key: tuple, payload: dict):
    with _CACHE_LOCK:
        _CACHE[cache_key] = (time.monotonic() + API_CACHE_TTL, payload)


def _latest_table_time(conn, table: str):
    rows = conn.run(f"SELECT MAX(time), COUNT(*) FROM {table}")
    if not rows:
        return None, 0
    return rows[0][0], int(rows[0][1] or 0)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/details")
def health_details():
    now = datetime.now(timezone.utc)
    info = {"status": "ok", "time": now.isoformat()}
    conn = None
    try:
        conn = get_conn()
        conn.run("SELECT 1")
        last_pos, count_pos = _latest_table_time(conn, "positions")
        last_mil, count_mil = _latest_table_time(conn, "military_sightings")
        info["db"] = {"ok": True}
        info["positions"] = {
            "count": count_pos,
            "last_time": last_pos.isoformat() if isinstance(last_pos, datetime) else None,
            "lag_seconds": round((now - last_pos).total_seconds(), 1) if isinstance(last_pos, datetime) else None,
        }
        info["military"] = {
            "count": count_mil,
            "last_time": last_mil.isoformat() if isinstance(last_mil, datetime) else None,
            "lag_seconds": round((now - last_mil).total_seconds(), 1) if isinstance(last_mil, datetime) else None,
        }
    except Exception as exc:
        info["status"] = "degraded"
        info["db"] = {"ok": False, "error": str(exc)}
    finally:
        if conn:
            conn.close()
    return info


@app.get("/stats/overview")
def stats_overview(period: str = Query("day")):
    cache_key = ("overview", period)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    since = period_start(period)
    conn = get_conn()
    try:
        data = overview_stats(conn, since)
    finally:
        conn.close()
    _cache_set(cache_key, data)
    return data


@app.get("/stats/leaderboard")
def stats_leaderboard(
    category: str = Query("highest_alt"),
    period: str = Query("day"),
    limit: int = Query(20, ge=1, le=100),
):
    cache_key = ("leaderboard", category, period, limit)
    cached_payload = _cache_get(cache_key)
    if cached_payload is not None:
        return cached_payload
    since = period_start(period)
    conn = get_conn()
    try:
        cached_items = leaderboard_from_records(conn, period, category, limit)
        if cached_items:
            payload = {"period": period, "category": category, "items": cached_items}
            _cache_set(cache_key, payload)
            return payload

        if category == "highest_alt":
            items = leaderboard_highest_alt(conn, since, limit)
        elif category == "fastest_gs":
            items = leaderboard_fastest(conn, since, limit)
        elif category == "largest":
            items = leaderboard_size(conn, since, limit, largest=True)
        elif category == "smallest":
            items = leaderboard_size(conn, since, limit, largest=False)
        elif category == "military":
            since_m = period_start(period)
            until = datetime.now(timezone.utc)
            rows = military_sightings(conn, since_m, until, limit)
            items = [
                {
                    "icao": r["icao"],
                    "value": 1,
                    "callsign": r.get("callsign"),
                    "icao_type": r.get("icao_type"),
                    "time": r["time"].isoformat() if r.get("time") else None,
                }
                for r in rows
            ]
        else:
            raise HTTPException(400, f"Unknown category: {category}")
    finally:
        conn.close()

    payload = {"period": period, "category": category, "items": items}
    _cache_set(cache_key, payload)
    return payload


@app.get("/stats/paths/top")
def stats_paths_top(period: str = Query("week"), limit: int = Query(50, ge=1, le=200)):
    cache_key = ("paths_top", period, limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    since = period_start(period)
    conn = get_conn()
    try:
        data = top_paths(conn, since, limit)
    finally:
        conn.close()
    _cache_set(cache_key, data)
    return data


@app.get("/stats/military")
def stats_military(
    period: str = Query("week"),
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    since = datetime.fromisoformat(from_ts) if from_ts else period_start(period)
    until = datetime.fromisoformat(to_ts) if to_ts else datetime.now(timezone.utc)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    conn = get_conn()
    try:
        rows = military_sightings(conn, since, until, limit)
    finally:
        conn.close()
    for r in rows:
        if isinstance(r.get("time"), datetime):
            r["time"] = r["time"].isoformat()
    return {"items": rows}


@app.get("/history/{icao}")
def history_icao(
    icao: str,
    period: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
):
    since = datetime.fromisoformat(from_ts) if from_ts else period_start(period or "day")
    until = datetime.fromisoformat(to_ts) if to_ts else datetime.now(timezone.utc)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    conn = get_conn()
    try:
        points = history_positions(conn, icao.lower(), since, until)
    finally:
        conn.close()
    for p in points:
        if isinstance(p.get("time"), datetime):
            p["time"] = p["time"].isoformat()
    return {"icao": icao.lower(), "points": points}


@app.get("/photo/{icao}")
async def photo_proxy(icao: str, response: Response):
    icao = icao.lower()
    os.makedirs(PHOTO_CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(PHOTO_CACHE_DIR, f"{icao}.json")

    conn = get_conn()
    try:
        rows = conn.run(
            "SELECT thumb_url, link_url FROM photo_cache WHERE icao = :icao",
            icao=icao,
        )
        if rows and rows[0][0]:
            return Response(status_code=302, headers={"Location": rows[0][0]})
    finally:
        conn.close()

    thumb_url = None
    if os.path.isfile(cache_file):
        import json
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
        thumb_url = data.get("thumb_url")

    if not thumb_url:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{PLANESPOTTERS_URL}{icao.upper()}")
                if r.status_code == 200:
                    data = r.json()
                    photos = data.get("photos") or []
                    if photos:
                        thumb = photos[0].get("thumbnail", {})
                        thumb_url = thumb.get("src") or thumb
                        import json
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump({"thumb_url": thumb_url}, f)
                        conn2 = get_conn()
                        try:
                            conn2.run(
                                "INSERT INTO photo_cache (icao, thumb_url, link_url, photographer) "
                                "VALUES (:icao, :thumb, :link, :photo) "
                                "ON CONFLICT (icao) DO UPDATE SET thumb_url = EXCLUDED.thumb_url, fetched_at = NOW()",
                                icao=icao,
                                thumb=thumb_url,
                                link=photos[0].get("link"),
                                photo=photos[0].get("photographer"),
                            )
                            conn2.run("COMMIT")
                        finally:
                            conn2.close()
        except Exception:
            pass

    if not thumb_url:
        raise HTTPException(404, "Photo not found")

    return Response(status_code=302, headers={"Location": thumb_url})


@app.get("/stats/traffic/trends")
def api_traffic_trends(
    granularity: str = Query("hour"),
    period: str = Query("week"),
):
    cache_key = ("traffic_trends", granularity, period)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    since = period_start(period)
    conn = get_conn()
    try:
        data = {"granularity": granularity, "points": traffic_trends(conn, granularity, since)}
    finally:
        conn.close()
    _cache_set(cache_key, data)
    return data


@app.get("/stats/traffic/peak-hours")
def api_peak_hours(period: str = Query("month"), tz: str = Query("UTC")):
    cache_key = ("peak_hours", period, tz)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    since = period_start(period)
    conn = get_conn()
    try:
        data = {"hours": peak_hours(conn, since, tz)}
    finally:
        conn.close()
    _cache_set(cache_key, data)
    return data


@app.get("/stats/paths/heatmap")
def api_paths_heatmap(period: str = Query("week"), limit: int = Query(200, ge=1, le=1000)):
    cache_key = ("paths_heatmap", period, limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    since = period_start(period)
    conn = get_conn()
    try:
        data = paths_heatmap(conn, since, limit)
    finally:
        conn.close()
    _cache_set(cache_key, data)
    return data


@app.get("/stats/altitude/histogram")
def api_altitude_histogram(period: str = Query("day")):
    cache_key = ("alt_hist", period)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    since = period_start(period)
    conn = get_conn()
    try:
        data = altitude_histogram(conn, since)
    finally:
        conn.close()
    _cache_set(cache_key, data)
    return data


@app.get("/stats/overnight")
def api_overnight(night: Optional[str] = None):
    from datetime import date
    night_date = date.fromisoformat(night) if night else date.today()
    conn = get_conn()
    try:
        items = overnight_list(conn, night_date)
    finally:
        conn.close()
    return {"night": str(night_date), "items": items}


@app.get("/stats/military/by-role")
def api_military_by_role(period: str = Query("week")):
    cache_key = ("mil_role", period)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    since = period_start(period)
    conn = get_conn()
    try:
        data = {"roles": military_by_role(conn, since)}
    finally:
        conn.close()
    _cache_set(cache_key, data)
    return data


@app.get("/stats/privacy")
def api_privacy(flag: Optional[str] = None, period: str = Query("week"), limit: int = Query(100, ge=1, le=500)):
    since = period_start(period)
    conn = get_conn()
    try:
        items = privacy_sightings_list(conn, flag, since, limit)
    finally:
        conn.close()
    return {"items": items}


@app.get("/stats/alerts/squawk")
def api_squawk_alerts(
    code: Optional[str] = None,
    active: bool = Query(False),
    period: str = Query("week"),
    limit: int = Query(100, ge=1, le=500),
):
    since = period_start(period)
    conn = get_conn()
    try:
        items = squawk_alerts_list(conn, code, active, since, limit)
    finally:
        conn.close()
    return {"items": items}


@app.get("/stats/government")
def api_government(period: str = Query("week"), limit: int = Query(100, ge=1, le=500)):
    since = period_start(period)
    conn = get_conn()
    try:
        items = government_list(conn, since, limit)
    finally:
        conn.close()
    return {"items": items}


@app.get("/stats/patterns")
def api_patterns(
    type: Optional[str] = None,
    period: str = Query("week"),
    limit: int = Query(50, ge=1, le=200),
):
    since = period_start(period)
    conn = get_conn()
    try:
        items = behavior_events_list(conn, type, since, limit)
    finally:
        conn.close()
    return {"items": items}


@app.get("/stats/patterns/summary")
def api_patterns_summary(period: str = Query("week")):
    since = period_start(period)
    conn = get_conn()
    try:
        summary = patterns_summary(conn, since)
    finally:
        conn.close()
    return {"summary": summary}


@app.get("/stats/patterns/repeat-visits")
def api_repeat_visits(period: str = Query("month"), min_visits: int = Query(3, ge=2, le=20)):
    since = period_start(period)
    conn = get_conn()
    try:
        items = repeat_visits_list(conn, since, min_visits)
    finally:
        conn.close()
    return {"items": items}


@app.get("/stats/patterns/{event_id}")
def api_pattern_detail(event_id: int):
    conn = get_conn()
    try:
        detail = behavior_event_detail(conn, event_id)
    finally:
        conn.close()
    if not detail:
        raise HTTPException(404, "Event not found")
    return detail
