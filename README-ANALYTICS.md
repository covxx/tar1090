# ADS-B Analytics

Analytics runs **automatically with `install.sh`** on Ubuntu/Debian — no Docker required.

## What install.sh sets up

| Component | How |
|-----------|-----|
| Database | PostgreSQL (`tar1090` database) |
| Ingest | `tar1090-analytics-ingest.service` — reads `aircraft.json` |
| API | `tar1090-analytics-api.service` — port **8080** |
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
- **API health:** `http://YOUR_IP:8080/health`

Hard-refresh the browser (Ctrl+Shift+R) if you still see the old UI.

## Check services

```bash
systemctl status tar1090-analytics-api tar1090-analytics-ingest tar1090-analytics-jobs
curl -s http://127.0.0.1:8080/health
```

## Configuration

`/etc/default/tar1090-analytics`:

- `AIRCRAFT_JSON` — path to readsb `aircraft.json`
- `TAR1090_DB_PATH` — tar1090-db shards for aircraft metadata
- `DATABASE_URL` — PostgreSQL connection

## API endpoints

- `GET /stats/overview?period=day|week|month`
- `GET /stats/leaderboard?category=highest_alt|fastest_gs|largest|smallest|military`
- `GET /stats/paths/top?period=week`
- `GET /stats/military`
- `GET /history/{icao}?period=day`
- `GET /photo/{icao}`

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
