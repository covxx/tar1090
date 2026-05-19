"""Analytics REST API for tar1090."""
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
import pg8000.native
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from queries import (
    history_positions,
    leaderboard_fastest,
    leaderboard_from_records,
    leaderboard_highest_alt,
    leaderboard_size,
    military_sightings,
    overview_stats,
    period_start,
    top_paths,
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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats/overview")
def stats_overview(period: str = Query("day")):
    since = period_start(period)
    conn = get_conn()
    try:
        return overview_stats(conn, since)
    finally:
        conn.close()


@app.get("/stats/leaderboard")
def stats_leaderboard(
    category: str = Query("highest_alt"),
    period: str = Query("day"),
    limit: int = Query(20, ge=1, le=100),
):
    since = period_start(period)
    conn = get_conn()
    try:
        cached = leaderboard_from_records(conn, period, category, limit)
        if cached:
            return {"period": period, "category": category, "items": cached}

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

    return {"period": period, "category": category, "items": items}


@app.get("/stats/paths/top")
def stats_paths_top(period: str = Query("week"), limit: int = Query(50, ge=1, le=200)):
    since = period_start(period)
    conn = get_conn()
    try:
        return top_paths(conn, since, limit)
    finally:
        conn.close()


@app.get("/stats/military")
def stats_military(
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    since = datetime.fromisoformat(from_ts) if from_ts else period_start("week")
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
