#!/bin/bash
# Native analytics install (no Docker) — Ubuntu/Debian
# Uses pg8000 (pure Python PostgreSQL driver) — no C compiler or libpq needed.
set -e
trap 'echo "[ERROR] install-analytics.sh line $LINENO: $BASH_COMMAND"' ERR

ipath="${1:-/usr/local/share/tar1090}"
srcdir="${2:-/run/readsb}"
git_dir="${3:-$ipath/git}"

if ! command -v systemctl &>/dev/null; then
    echo "Analytics: systemd required, skipping."
    exit 0
fi

if ! command -v apt-get &>/dev/null; then
    echo "Analytics: apt-get required, skipping."
    exit 0
fi

PYTHON3="$(command -v python3)"

echo "--------------"
echo "Installing tar1090 analytics (native)..."
echo "Python: $PYTHON3 ($($PYTHON3 --version 2>&1))"

apt_install() {
    apt-get install -y --no-install-recommends "$@" 2>/dev/null || {
        apt-get update || true
        apt-get install -y --no-install-recommends "$@"
    }
}

# ---- System packages (no psycopg2, no libpq-dev, no python3-dev) ----
apt_install postgresql postgresql-client python3 python3-pip

# ---- Clean leftovers from previous psycopg2-based installs ----
"$PYTHON3" -m pip uninstall -y psycopg2-binary 2>/dev/null || true
"$PYTHON3" -m pip uninstall -y psycopg2 2>/dev/null || true
rm -rf "$ipath/analytics-lib" "$ipath/analytics-venv"

# ---- PostgreSQL setup ----
systemctl enable postgresql
systemctl start postgresql
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "CREATE USER tar1090 WITH PASSWORD 'tar1090';" 2>/dev/null || true
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "CREATE DATABASE tar1090 OWNER tar1090;" 2>/dev/null || true
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "GRANT ALL PRIVILEGES ON DATABASE tar1090 TO tar1090;" 2>/dev/null || true

# ---- Copy service files ----
analytics_dir="$ipath/analytics"
mkdir -p "$analytics_dir/ingest" "$analytics_dir/api" "$analytics_dir/jobs"
mkdir -p /var/lib/tar1090/photo-cache

cp "$git_dir/services/ingest/"*.py "$analytics_dir/ingest/"
cp "$git_dir/services/api/"*.py   "$analytics_dir/api/"
cp "$git_dir/services/jobs/"*.py  "$analytics_dir/jobs/"
cp "$git_dir/services/schema-plain.sql" "$analytics_dir/schema-plain.sql"

# ---- Install ALL pip packages system-wide (pure Python, no wheels to build) ----
echo "Installing pg8000 + fastapi + uvicorn + deps via pip ..."
"$PYTHON3" -m pip install --break-system-packages \
    "pg8000>=1.31.2" \
    "fastapi==0.115.0" \
    "uvicorn[standard]==0.30.6" \
    "httpx==0.27.2" \
    "geohash2==1.1.0" \
    2>&1 || {
        echo "Retrying pip without --break-system-packages ..."
        "$PYTHON3" -m pip install \
            "pg8000>=1.31.2" \
            "fastapi==0.115.0" \
            "uvicorn[standard]==0.30.6" \
            "httpx==0.27.2" \
            "geohash2==1.1.0"
    }

# ---- Verify imports ----
"$PYTHON3" -c "import pg8000; print('pg8000 OK:', pg8000.__version__)"
"$PYTHON3" -c "import fastapi, uvicorn; print('fastapi+uvicorn OK')"
echo "All Python deps OK."

# ---- Apply schema ----
export PGPASSWORD=tar1090
psql -h 127.0.0.1 -U tar1090 -d tar1090 -f "$analytics_dir/schema-plain.sql" 2>/dev/null || \
    sudo -u postgres psql -d tar1090 -f "$analytics_dir/schema-plain.sql"

# ---- Env file ----
db_path=""
for d in "$ipath"/html/db-* "$ipath"/html-*/db-*; do
    [[ -d "$d" ]] && db_path="$d" && break
done

aircraft_json="$srcdir/aircraft.json"
[[ -f "$aircraft_json" ]] || aircraft_json="/run/readsb/aircraft.json"

cat > /etc/default/tar1090-analytics <<EOF
DATABASE_URL=postgresql://tar1090:tar1090@127.0.0.1/tar1090
AIRCRAFT_JSON=$aircraft_json
TAR1090_DB_PATH=${db_path:-$ipath/html/db2}
SCHEMA_PATH=$analytics_dir/schema-plain.sql
PHOTO_CACHE_DIR=/var/lib/tar1090/photo-cache
INGEST_INTERVAL=2
BATCH_INTERVAL=8
JOB_INTERVAL=3600
EOF
chmod 644 /etc/default/tar1090-analytics

# ---- Systemd services ----
cp "$git_dir/tar1090-analytics-api.service" /lib/systemd/system/
cp "$git_dir/tar1090-analytics-ingest.service" /lib/systemd/system/
cp "$git_dir/tar1090-analytics-jobs.service" /lib/systemd/system/

systemctl daemon-reload
systemctl enable tar1090-analytics-api tar1090-analytics-ingest tar1090-analytics-jobs
systemctl restart tar1090-analytics-api tar1090-analytics-ingest tar1090-analytics-jobs || true

echo "Analytics API: http://127.0.0.1:9056/health"
echo "--------------"
