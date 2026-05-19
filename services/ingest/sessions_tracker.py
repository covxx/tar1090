"""Track per-aircraft sessions for pattern analysis."""
import math
import time
from datetime import datetime, timezone

SESSION_GAP_SEC = float(__import__("os").environ.get("SESSION_GAP_SEC", "900"))


def _haversine_nm(lat1, lon1, lat2, lon2):
    r = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


class SessionTracker:
    def __init__(self):
        self._active: dict[str, dict] = {}

    def update(self, icao: str, ts: datetime, lat: float, lon: float, alt, callsign, icao_type, is_military: bool):
        now_ts = ts.timestamp() if isinstance(ts, datetime) else time.time()
        s = self._active.get(icao)
        if s and now_ts - s["last_ts"] > SESSION_GAP_SEC:
            closed = self._close(icao)
            if closed:
                yield closed
            s = None
        if not s:
            self._active[icao] = {
                "first_seen": ts,
                "last_seen": ts,
                "last_ts": now_ts,
                "points": [(lat, lon, alt)],
                "max_alt": alt,
                "distance_nm": 0.0,
                "callsign": callsign,
                "icao_type": icao_type,
                "is_military": is_military,
            }
            return
        prev = s["points"][-1]
        s["distance_nm"] += _haversine_nm(prev[0], prev[1], lat, lon)
        s["points"].append((lat, lon, alt))
        s["last_seen"] = ts
        s["last_ts"] = now_ts
        if alt is not None and (s["max_alt"] is None or alt > s["max_alt"]):
            s["max_alt"] = alt
        if callsign:
            s["callsign"] = callsign
        if icao_type:
            s["icao_type"] = icao_type

    def _close(self, icao: str) -> tuple | None:
        s = self._active.pop(icao, None)
        if not s or len(s["points"]) < 2:
            return None
        lats = [p[0] for p in s["points"]]
        lons = [p[1] for p in s["points"]]
        clat = sum(lats) / len(lats)
        clon = sum(lons) / len(lons)
        max_r = 0.0
        for la, lo, _ in s["points"]:
            max_r = max(max_r, _haversine_nm(clat, clon, la, lo))
        return (
            icao,
            s["first_seen"],
            s["last_seen"],
            s["max_alt"],
            s["distance_nm"],
            s["is_military"],
            s.get("callsign"),
            s.get("icao_type"),
            len(s["points"]),
            s["distance_nm"],
            max_r,
            clat,
            clon,
        )

    def flush_stale(self, max_age_sec: float = 1200):
        now = time.time()
        for icao in list(self._active.keys()):
            if now - self._active[icao]["last_ts"] > max_age_sec:
                closed = self._close(icao)
                if closed:
                    yield closed
