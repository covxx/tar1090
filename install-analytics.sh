#!/bin/bash
# Native analytics install (no Docker) — Ubuntu/Debian
# Strategy: apt for psycopg2, system-wide pip for the rest. No venv, no --target, no PYTHONPATH.
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

# ---- System packages ----
apt_install postgresql postgresql-client python3 python3-pip python3-psycopg2

# Remove any previously pip-installed psycopg2-binary to prevent conflicts
"$PYTHON3" -m pip uninstall -y psycopg2-binary 2>/dev/null || true
"$PYTHON3" -m pip uninstall -y psycopg2 2>/dev/null || true

# Verify apt psycopg2 works
if ! "$PYTHON3" -c "import psycopg2" 2>/dev/null; then
    echo "FATAL: python3-psycopg2 not importable by $PYTHON3"
    exit 1
fi
echo "psycopg2 (apt): $("$PYTHON3" -c 'import psycopg2; print(psycopg2.__file__)')"

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

# ---- Clean old --target lib dir and venv from previous installs ----
rm -rf "$ipath/analytics-lib" "$ipath/analytics-venv"

# ---- Install pip packages system-wide (NO psycopg2) ----
echo "Installing fastapi/uvicorn/httpx/geohash2 system-wide via pip ..."
"$PYTHON3" -m pip install --break-system-packages \
    "fastapi==0.115.0" \
    "uvicorn[standard]==0.30.6" \
    "httpx==0.27.2" \
    "geohash2==1.1.0" \
    2>&1 || {
        echo "pip with --break-system-packages failed, trying without ..."
        "$PYTHON3" -m pip install \
            "fastapi==0.115.0" \
            "uvicorn[standard]==0.30.6" \
            "httpx==0.27.2" \
            "geohash2==1.1.0"
    }

# ---- Verify imports ----
if ! "$PYTHON3" -c "import psycopg2" 2>/dev/null; then
    echo "FATAL: psycopg2 not importable."
    exit 1
fi
if ! "$PYTHON3" -c "import fastapi, uvicorn" 2>/dev/null; then
    echo "FATAL: fastapi/uvicorn not importable."
    exit 1
fi
echo "All Python deps OK."

# ---- Apply schema ----
export PGPASSWORD=tar1090
psql -h 127.0.0.1 -U tar1090 -d tar1090 -f "$analytics_dir/schema-plain.sql" 2>/dev/null || \
    sudo -u postgres psql -d tar1090 -f "$analytics_dir/schema-plain.sql"

# ---- Env file (no PYTHONPATH needed) ----
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
