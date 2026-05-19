# ADS-B Analytics

Analytics runs **automatically with `install.sh`** on Ubuntu/Debian — no Docker required.

## What install.sh sets up

| Component | How |
|-----------|-----|
| Database | PostgreSQL (`tar1090` database) |
| Ingest | `tar1090-analytics-ingest.service` — reads `aircraft.json` |
| API | `tar1090-analytics-api.service` — port **9056** |
| Jobs | `tar1090-analytics-jobs.service` — hourly leaderboards / paths |
| UI | `analytics.html` served with the map (button **A**) |

## Install (one command)

```bash
sudo bash -c "$(wget -nv -O - https://github.com/covxx/tar1090/raw/master/install.sh)"
```

Disable analytics:

```bash
# In /etc/default/tar1090 before install, or:
export ENABLE_ANALYTICS=no
sudo bash -c "$(wget -nv -O - https://github.com/covxx/tar1090/raw/master/install.sh)"
```

## After install

- **Live map:** `http://YOUR_IP:8504/tar1090/` (or your usual URL)
- **Analytics page:** same host, path `/tar1090/analytics.html`, or click **A** on the map
- **API health:** `http://YOUR_IP:9056/health`

Hard-refresh the browser (Ctrl+Shift+R) if you still see the old UI.

## Check services

```bash
systemctl status tar1090-analytics-api tar1090-analytics-ingest tar1090-analytics-jobs
curl -s http://127.0.0.1:9056/health
```

Python deps: **psycopg2 from apt** (`python3-psycopg2`), everything else via pip into `/usr/local/share/tar1090/analytics-lib` (no venv, no `psycopg2-binary` wheel build). `PYTHONPATH` is set in `/etc/default/tar1090-analytics`.

```bash
source /etc/default/tar1090-analytics
python3 -c "import psycopg2, fastapi; print('ok')"
```

## Configuration

`/etc/default/tar1090-analytics`:

- `AIRCRAFT_JSON` — path to readsb `aircraft.json`
- `TAR1090_DB_PATH` — tar1090-db shards for aircraft metadata
- `DATABASE_URL` — PostgreSQL connection

## API endpoints

**Overview & leaderboards**
- `GET /stats/overview?period=day|week|month`
- `GET /stats/leaderboard?category=highest_alt|fastest_gs|largest|smallest|military`
- `GET /stats/paths/top?period=week` — top corridor cells
- `GET /stats/military?period=week`
- `GET /history/{icao}?period=day`
- `GET /photo/{icao}`
- `GET /health/details`

**Traffic dashboard**
- `GET /stats/traffic/trends?granularity=hour|day&period=week`
- `GET /stats/traffic/peak-hours?period=month`
- `GET /stats/paths/heatmap?period=week&limit=200`
- `GET /stats/altitude/histogram?period=day`
- `GET /stats/overnight?night=YYYY-MM-DD`

**Military & special**
- `GET /stats/military/by-role?period=week`
- `GET /stats/privacy?flag=pia|ladd&period=week`
- `GET /stats/alerts/squawk?code=7700&active=true&period=week`
- `GET /stats/government?period=week`

**Behavior patterns**
- `GET /stats/patterns?type=loiter|racetrack|alt_oscillation&period=week`
- `GET /stats/patterns/summary?period=week`
- `GET /stats/patterns/repeat-visits?period=month`
- `GET /stats/patterns/{event_id}` — event detail + track GeoJSON

## Optional: Docker

Docker Compose is still in the repo for advanced setups or running analytics on a separate machine:

```bash
docker compose up -d --build
```

For a normal feeder on Ubuntu, **use `install.sh` only** — Docker is not required.

## Data retention

- Positions: 90 days
- Military sightings: 12 months  
- Path cells / records: 12 months  

Managed by the jobs service (plain PostgreSQL, no TimescaleDB required).
