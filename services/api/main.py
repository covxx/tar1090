"""Analytics REST API for tar1090."""
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
import psycopg2
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from psycopg2.extras import RealDictCursor

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
    "postgresql://tar1090:tar1090@timescaledb:5432/tar1090",
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


def get_conn():
    return psycopg2.connect(DATABASE_URL)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats/overview")
def stats_overview(period: str = Query("day")):
    since = period_start(period)
    with get_conn() as conn:
        with conn.cursor() as cur:
            return overview_stats(cur, since)


@app.get("/stats/leaderboard")
def stats_leaderboard(
    category: str = Query("highest_alt"),
    period: str = Query("day"),
    limit: int = Query(20, ge=1, le=100),
):
    since = period_start(period)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cached = leaderboard_from_records(cur, period, category, limit)
            if cached:
                return {"period": period, "category": category, "items": cached}

            if category == "highest_alt":
                items = leaderboard_highest_alt(cur, since, limit)
            elif category == "fastest_gs":
                items = leaderboard_fastest(cur, since, limit)
            elif category == "largest":
                items = leaderboard_size(cur, since, limit, largest=True)
            elif category == "smallest":
                items = leaderboard_size(cur, since, limit, largest=False)
            elif category == "military":
                since_m = period_start(period)
                until = datetime.now(timezone.utc)
                rows = military_sightings(cur, since_m, until, limit)
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

    return {"period": period, "category": category, "items": items}


@app.get("/stats/paths/top")
def stats_paths_top(period: str = Query("week"), limit: int = Query(50, ge=1, le=200)):
    since = period_start(period)
    with get_conn() as conn:
        with conn.cursor() as cur:
            return top_paths(cur, since, limit)


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
    with get_conn() as conn:
        with conn.cursor() as cur:
            rows = military_sightings(cur, since, until, limit)
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
    with get_conn() as conn:
        with conn.cursor() as cur:
            points = history_positions(cur, icao.lower(), since, until)
    for p in points:
        if isinstance(p.get("time"), datetime):
            p["time"] = p["time"].isoformat()
    return {"icao": icao.lower(), "points": points}


@app.get("/photo/{icao}")
async def photo_proxy(icao: str, response: Response):
    icao = icao.lower()
    os.makedirs(PHOTO_CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(PHOTO_CACHE_DIR, f"{icao}.json")

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT thumb_url, link_url FROM photo_cache WHERE icao = %s",
                (icao,),
            )
            row = cur.fetchone()
            if row and row["thumb_url"]:
                return Response(status_code=302, headers={"Location": row["thumb_url"]})

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
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    INSERT INTO photo_cache (icao, thumb_url, link_url, photographer)
                                    VALUES (%s, %s, %s, %s)
                                    ON CONFLICT (icao) DO UPDATE SET thumb_url = EXCLUDED.thumb_url,
                                        fetched_at = NOW()
                                    """,
                                    (
                                        icao,
                                        thumb_url,
                                        photos[0].get("link"),
                                        photos[0].get("photographer"),
                                    ),
                                )
                            conn.commit()
        except Exception:
            pass

    if not thumb_url:
        raise HTTPException(404, "Photo not found")

    return Response(status_code=302, headers={"Location": thumb_url})
