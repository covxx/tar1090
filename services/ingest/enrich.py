"""Aircraft metadata enrichment and military ICAO detection."""
import json
import os
import re
from pathlib import Path

# Approximate dimensions by ICAO type (length_m, wingspan_m)
TYPE_DIMENSIONS = {
    "A388": (72.7, 79.8),
    "A359": (66.8, 64.8),
    "A35K": (66.8, 64.8),
    "A333": (63.7, 60.3),
    "A332": (58.8, 56.4),
    "A321": (44.5, 35.8),
    "A320": (37.6, 34.1),
    "A319": (33.8, 34.1),
    "B748": (76.3, 68.4),
    "B744": (70.6, 64.4),
    "B789": (62.8, 60.1),
    "B788": (56.7, 60.1),
    "B77W": (73.9, 64.8),
    "B772": (63.7, 60.9),
    "B763": (54.9, 47.6),
    "B752": (47.3, 38.1),
    "B739": (42.1, 35.8),
    "B738": (39.5, 35.8),
    "B737": (33.6, 35.8),
    "E190": (38.7, 28.7),
    "E170": (31.7, 26.0),
    "C172": (8.2, 11.0),
    "C152": (7.3, 10.2),
    "GLID": (8.0, 15.0),
    "ZZZZ": (20.0, 20.0),
}

MIL_RANGES = []
GOV_RANGES = []
MIL_TYPE_ROLE = {}


def load_mil_ranges(db_path: str) -> None:
    global MIL_RANGES
    ranges_file = Path(db_path) / "ranges.js"
    if not ranges_file.exists():
        return
    text = ranges_file.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'"military"\s*:\s*\[([\s\S]*?)\]', text)
    if not m:
        return
    for pair in re.findall(r'\["([0-9a-fA-F]+)"\s*,\s*"([0-9a-fA-F]+)"\]', m.group(1)):
        MIL_RANGES.append((int(pair[0], 16), int(pair[1], 16)))


def is_military_icao(icao: str, db_flags: int = 0) -> bool:
    if db_flags & 1:
        return True
    try:
        n = int(icao, 16)
    except ValueError:
        return False
    for lo, hi in MIL_RANGES:
        if lo <= n <= hi:
            return True
    return False


def lookup_db_shard(db_path: str, icao: str) -> dict | None:
    """Load ICAO record from tar1090-db shard JS files."""
    if not db_path or icao[0] == "~":
        return None
    icao = icao.upper()
    for level in range(1, 7):
        bkey = icao[:level]
        shard = Path(db_path) / f"{bkey}.js"
        if not shard.exists():
            continue
        text = shard.read_text(encoding="utf-8", errors="ignore")
        # shard format: var key_db_data = { "ABC123": [...], ...}
        dkey = icao[level:]
        pattern = rf'"{re.escape(dkey)}"\s*:\s*(\[[^\]]*\])'
        m = re.search(pattern, text)
        if m:
            try:
                arr = json.loads(m.group(1))
                return _parse_db_entry(arr)
            except json.JSONDecodeError:
                pass
    return None


def _parse_db_entry(arr: list) -> dict:
    """Parse tar1090-db array entry (format varies by version)."""
    meta = {}
    if len(arr) > 0 and isinstance(arr[0], str):
        meta["registration"] = arr[0]
    if len(arr) > 1 and isinstance(arr[1], str):
        meta["icao_type"] = arr[1]
    if len(arr) > 2 and isinstance(arr[2], str):
        meta["model"] = arr[2]
    if len(arr) > 7 and isinstance(arr[7], int):
        meta["db_flags"] = arr[7]
    return meta


def load_gov_ranges_json() -> None:
    global GOV_RANGES
    path = Path(__file__).parent / "gov_ranges.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data:
        GOV_RANGES.append((
            int(entry["range_start"], 16),
            int(entry["range_end"], 16),
            entry.get("country"),
            entry.get("agency"),
        ))


def load_military_type_map() -> None:
    global MIL_TYPE_ROLE
    path = Path(__file__).parent / "military_type_map.json"
    if not path.exists():
        return
    MIL_TYPE_ROLE = {k.upper(): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def is_government_icao(icao: str) -> tuple[bool, str | None, str | None]:
    try:
        n = int(icao, 16)
    except ValueError:
        return False, None, None
    for lo, hi, country, agency in GOV_RANGES:
        if lo <= n <= hi:
            return True, country, agency
    return False, None, None


def military_role_for(icao_type: str | None, callsign: str | None) -> str:
    if icao_type:
        t = icao_type.upper()
        if t in MIL_TYPE_ROLE:
            return MIL_TYPE_ROLE[t]
        for key, role in MIL_TYPE_ROLE.items():
            if t.startswith(key) or key in t:
                return role
    if callsign:
        cs = callsign.upper()
        if any(x in cs for x in ("TANK", "NCHO", "NCHO")):
            return "tanker"
        if any(x in cs for x in ("RCH", "REACH", "EVAC")):
            return "cargo"
        if any(x in cs for x in ("NAVY", "MARINE", "AF")):
            return "fighter"
    return "other"


def dimensions_for_type(icao_type: str | None) -> tuple[float | None, float | None]:
    if not icao_type:
        return None, None
    t = icao_type.upper()
    if t in TYPE_DIMENSIONS:
        return TYPE_DIMENSIONS[t]
    return TYPE_DIMENSIONS.get("ZZZZ", (20.0, 20.0))
