# ADS-B Analytics Stack

This fork adds a Docker-based analytics warehouse alongside the standard tar1090 map.

## Components

| Service | Port | Purpose |
|---------|------|---------|
| tar1090 (host) | 8504 | Live map (existing install) |
| nginx (Docker) | 8505 | Analytics dashboard + static files |
| api (Docker) | 8080 | REST API |
| timescaledb | internal | Position history (90 days raw) |
| ingest | internal | Reads `aircraft.json` from readsb |
| jobs | internal | Hourly path/leaderboard aggregation |

## Quick start

1. Ensure readsb is running and writing `/run/readsb/aircraft.json`.
2. Install tar1090-db (via `install.sh`) so `/usr/local/share/tar1090/html/db2` exists.
3. Copy `.env.example` to `.env` and adjust paths if needed.
4. Start the stack (from the repo checkout, or `/usr/local/share/tar1090` after install):

```bash
cd /path/to/tar1090   # or /usr/local/share/tar1090 if copied by install.sh
docker compose up -d --build
```

**Ubuntu install (map + files):**

```bash
sudo bash -c "$(wget -nv -O - https://github.com/covxx/tar1090/raw/master/install.sh)"
```

5. Open:
   - **Analytics dashboard:** http://localhost:8505/tar1090/analytics.html
   - **API:** http://localhost:8080/health
   - **Live map:** your existing tar1090 URL (port 8504)

## Configuration

Edit `html/config.js`:

```javascript
analyticsApiUrl = "http://127.0.0.1:8080";
analyticsEnabled = true;
showSil = true;
```

When using the nginx bundle on port 8505, set `analyticsApiUrl = "/api"` if the map is served from the same host.

## API endpoints

- `GET /stats/overview?period=day|week|month`
- `GET /stats/leaderboard?category=highest_alt|fastest_gs|largest|smallest|military&period=day`
- `GET /stats/paths/top?period=week`
- `GET /stats/military`
- `GET /history/{icao}?period=day`
- `GET /photo/{icao}` — cached planespotters thumbnail redirect

## Map enhancements

- **A** button — opens analytics dashboard
- **U** button — military-only filter (existing, relabeled)
- Sidebar shows today's highest altitude and military count when API is reachable
- Aircraft photos: planespotters.net with silhouette fallback (`aircraft_sil/`)
- Extended live trail history: `HISTORY_SIZE=4500` (~10 hours) in `default`

## Data retention

- Raw positions: 90 days (TimescaleDB retention policy)
- Military sightings: 365 days
- Path cells and records: 12 months (nightly cleanup job)

## Windows / development

On Windows without readsb, mount a sample `aircraft.json`:

```yaml
# docker-compose.override.yml
services:
  ingest:
    volumes:
      - ./sample-data:/data:ro
```

Place a valid `aircraft.json` in `sample-data/`.
