#!/usr/bin/env python3
"""Ingest readsb aircraft.json into PostgreSQL analytics."""
import json
import os
import time
from datetime import datetime, timezone

from db import (
    close_squawk_alerts,
    get_active_squawk_alert,
    init_schema,
    insert_government_sightings,
    insert_military_sightings,
    insert_positions,
    insert_privacy_sightings,
    insert_session,
    seed_gov_ranges,
    seed_military_type_map,
    update_squawk_alert,
    upsert_meta,
    upsert_squawk_alert,
)
from enrich import (
    dimensions_for_type,
    is_government_icao,
    is_military_icao,
    load_gov_ranges_json,
    load_mil_ranges,
    load_military_type_map,
    lookup_db_shard,
    military_role_for,
)
from sessions_tracker import SessionTracker

AIRCRAFT_JSON = os.environ.get("AIRCRAFT_JSON", "/data/aircraft.json")
INGEST_INTERVAL = float(os.environ.get("INGEST_INTERVAL", "2"))
BATCH_INTERVAL = float(os.environ.get("BATCH_INTERVAL", "8"))
STALE_SEEN = float(os.environ.get("STALE_SEEN", "30"))
DB_PATH = os.environ.get("TAR1090_DB_PATH", "/db")
MILITARY_COOLDOWN = float(os.environ.get("MILITARY_COOLDOWN", "300"))
PRIVACY_COOLDOWN = float(os.environ.get("PRIVACY_COOLDOWN", "300"))
GOV_COOLDOWN = float(os.environ.get("GOV_COOLDOWN", "300"))
EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}

_meta_cache: dict[str, dict] = {}
_last_military: dict[str, float] = {}
_last_privacy: dict[str, float] = {}
_last_gov: dict[str, float] = {}
_active_squawk: dict[str, str] = {}
_buffer: list[tuple] = []
_mil_buffer: list[tuple] = []
_priv_buffer: list[tuple] = []
_gov_buffer: list[tuple] = []
_last_flush = time.time()
_session_tracker = SessionTracker()
_closed_sessions: list[tuple] = []


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


def _squawk_str(ac: dict) -> str | None:
    sq = ac.get("squawk")
    if sq is None:
        return None
    return str(sq).zfill(4) if str(sq).isdigit() else str(sq)


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


def _handle_squawk(icao: str, squawk: str | None, now: datetime, callsign, icao_type, lat, lon, military: bool):
    prev = _active_squawk.get(icao)
    if squawk in EMERGENCY_SQUAWKS:
        if prev != squawk:
            if prev in EMERGENCY_SQUAWKS:
                close_squawk_alerts(icao, prev, now)
            upsert_squawk_alert(icao, squawk, now, callsign, icao_type, lat, lon, military)
        else:
            aid = get_active_squawk_alert(icao, squawk)
            if aid:
                update_squawk_alert(aid, lat, lon, callsign)
        _active_squawk[icao] = squawk
    elif prev in EMERGENCY_SQUAWKS:
        close_squawk_alerts(icao, prev, now)
        _active_squawk.pop(icao, None)


def _handle_privacy(icao: str, db_flags: int, now_iso: str, callsign, lat, lon):
    flags = []
    if db_flags & 4:
        flags.append("pia")
    if db_flags & 8:
        flags.append("ladd")
    for flag in flags:
        key = f"{icao}:{flag}"
        if time.time() - _last_privacy.get(key, 0) >= PRIVACY_COOLDOWN:
            _last_privacy[key] = time.time()
            _priv_buffer.append((now_iso, icao, flag, callsign, lat, lon, db_flags))


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
        squawk = _squawk_str(ac)
        alt = _alt(ac)
        icao_type = meta.get("icao_type")

        row = (
            ts, icao, callsign, float(lat), float(lon), alt,
            ac.get("alt_geom"), ac.get("gs") or ac.get("tas"), ac.get("track"),
            squawk, icao_type, ac.get("wtc"), db_flags, military, _source(ac),
        )
        _buffer.append(row)

        _handle_squawk(icao, squawk, now, callsign, icao_type, float(lat), float(lon), military)
        _handle_privacy(icao, db_flags, ts, callsign, float(lat), float(lon))

        is_gov, country, agency = is_government_icao(icao)
        if is_gov:
            last = _last_gov.get(icao, 0)
            if time.time() - last >= GOV_COOLDOWN:
                _last_gov[icao] = time.time()
                _gov_buffer.append((ts, icao, country, agency, callsign, float(lat), float(lon), alt))

        for closed in _session_tracker.update(
            icao, now, float(lat), float(lon), alt, callsign, icao_type, military
        ):
            _closed_sessions.append(closed)

        if military:
            last = _last_military.get(icao, 0)
            if time.time() - last >= MILITARY_COOLDOWN:
                _last_military[icao] = time.time()
                role = military_role_for(icao_type, callsign)
                _mil_buffer.append(
                    (ts, icao, callsign, icao_type, float(lat), float(lon), alt, db_flags, role)
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
    if _priv_buffer:
        insert_privacy_sightings(_priv_buffer.copy())
        _priv_buffer.clear()
    if _gov_buffer:
        insert_government_sightings(_gov_buffer.copy())
        _gov_buffer.clear()
    for closed in list(_closed_sessions):
        insert_session(closed)
    _closed_sessions.clear()
    for closed in _session_tracker.flush_stale():
        insert_session(closed)
    _last_flush = time.time()


def _seed_static_data():
    from enrich import GOV_RANGES, MIL_TYPE_ROLE

    load_gov_ranges_json()
    load_military_type_map()
    rows = [(lo, hi, country, agency) for lo, hi, country, agency in GOV_RANGES]
    if rows:
        seed_gov_ranges(rows)
    type_rows = [(k, v) for k, v in MIL_TYPE_ROLE.items()]
    if type_rows:
        seed_military_type_map(type_rows)


def wait_for_db(retries=60):
    for i in range(retries):
        try:
            init_schema()
            _seed_static_data()
            print("Database schema ready")
            return
        except Exception as e:
            print(f"Waiting for database ({i+1}/{retries}): {e}")
            time.sleep(2)
    raise RuntimeError("Could not connect to database")


def main():
    load_mil_ranges(DB_PATH)
    load_gov_ranges_json()
    load_military_type_map()
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
