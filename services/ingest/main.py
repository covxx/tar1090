#!/usr/bin/env python3
"""Ingest readsb aircraft.json into TimescaleDB."""
import json
import os
import time
from datetime import datetime, timezone

from db import init_schema, insert_military_sightings, insert_positions, upsert_meta
from enrich import (
    dimensions_for_type,
    is_military_icao,
    load_mil_ranges,
    lookup_db_shard,
)

AIRCRAFT_JSON = os.environ.get("AIRCRAFT_JSON", "/data/aircraft.json")
INGEST_INTERVAL = float(os.environ.get("INGEST_INTERVAL", "2"))
BATCH_INTERVAL = float(os.environ.get("BATCH_INTERVAL", "8"))
STALE_SEEN = float(os.environ.get("STALE_SEEN", "30"))
DB_PATH = os.environ.get("TAR1090_DB_PATH", "/db")
MILITARY_COOLDOWN = float(os.environ.get("MILITARY_COOLDOWN", "300"))

_meta_cache: dict[str, dict] = {}
_last_military: dict[str, float] = {}
_buffer: list[tuple] = []
_mil_buffer: list[tuple] = []
_last_flush = time.time()


def _alt(ac: dict):
    if ac.get("ground"):
        return 0.0
    if ac.get("alt_baro") is not None:
        return float(ac["alt_baro"])
    if ac.get("altitude") is not None:
        return float(ac["altitude"])
    if ac.get("alt_geom") is not None:
        return float(ac["alt_geom"])
    return None


def _source(ac: dict) -> str:
    if ac.get("mlat"):
        return "mlat"
    if ac.get("tisb"):
        return "tisb"
    return ac.get("type", "adsb") or "adsb"


def _enrich(icao: str, ac: dict) -> dict:
    if icao in _meta_cache:
        return _meta_cache[icao]
    meta = lookup_db_shard(DB_PATH, icao) or {}
    icao_type = ac.get("t") or ac.get("icaoType") or meta.get("icao_type")
    length_m, wingspan_m = dimensions_for_type(icao_type)
    db_flags = meta.get("db_flags", 0) or ac.get("dbFlags", 0) or 0
    result = {
        "registration": meta.get("registration") or ac.get("r"),
        "icao_type": icao_type,
        "model": meta.get("model"),
        "db_flags": db_flags,
        "length_m": length_m,
        "wingspan_m": wingspan_m,
    }
    _meta_cache[icao] = result
    if icao_type or meta.get("registration"):
        upsert_meta(
            icao,
            result["registration"],
            icao_type,
            None,
            result["model"],
            length_m,
            wingspan_m,
        )
    return result


def _parse_aircraft(data: dict) -> None:
    global _last_flush
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    aircraft = data.get("aircraft") or []

    for ac in aircraft:
        if ac.get("seen", 999) > STALE_SEEN:
            continue
        icao = (ac.get("hex") or "").lower()
        if not icao or len(icao) != 6 or icao[0] == "~":
            continue
        lat = ac.get("lat")
        lon = ac.get("lon")
        if lat is None or lon is None:
            continue

        meta = _enrich(icao, ac)
        db_flags = meta.get("db_flags", 0)
        military = is_military_icao(icao, db_flags)

        flight = ac.get("flight")
        callsign = flight.strip() if isinstance(flight, str) else None

        row = (
            ts,
            icao,
            callsign,
            float(lat),
            float(lon),
            _alt(ac),
            ac.get("alt_geom"),
            ac.get("gs") or ac.get("tas"),
            ac.get("track"),
            ac.get("squawk"),
            meta.get("icao_type"),
            ac.get("wtc"),
            db_flags,
            military,
            _source(ac),
        )
        _buffer.append(row)

        if military:
            last = _last_military.get(icao, 0)
            if time.time() - last >= MILITARY_COOLDOWN:
                _last_military[icao] = time.time()
                _mil_buffer.append(
                    (
                        ts,
                        icao,
                        callsign,
                        meta.get("icao_type"),
                        float(lat),
                        float(lon),
                        _alt(ac),
                        db_flags,
                    )
                )

    if time.time() - _last_flush >= BATCH_INTERVAL:
        flush()


def flush():
    global _last_flush
    if _buffer:
        insert_positions(_buffer.copy())
        _buffer.clear()
    if _mil_buffer:
        insert_military_sightings(_mil_buffer.copy())
        _mil_buffer.clear()
    _last_flush = time.time()


def wait_for_db(retries=60):
    for i in range(retries):
        try:
            init_schema()
            print("Database schema ready")
            return
        except Exception as e:
            print(f"Waiting for database ({i+1}/{retries}): {e}")
            time.sleep(2)
    raise RuntimeError("Could not connect to database")


def main():
    load_mil_ranges(DB_PATH)
    wait_for_db()
    print(f"Ingesting from {AIRCRAFT_JSON} every {INGEST_INTERVAL}s")

    while True:
        try:
            if os.path.isfile(AIRCRAFT_JSON):
                with open(AIRCRAFT_JSON, encoding="utf-8") as f:
                    data = json.load(f)
                _parse_aircraft(data)
            else:
                print(f"Missing {AIRCRAFT_JSON}, retrying...")
        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
        except Exception as e:
            print(f"Ingest error: {e}")
        time.sleep(INGEST_INTERVAL)


if __name__ == "__main__":
    main()
