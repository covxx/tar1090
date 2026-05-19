#!/bin/bash
# Native analytics install (no Docker) — Ubuntu/Debian
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
PYLIB="$ipath/analytics-lib"

echo "--------------"
echo "Installing tar1090 analytics (native)..."
echo "Python: $PYTHON3 ($($PYTHON3 --version 2>&1))"

apt-get install -y --no-install-recommends \
    postgresql postgresql-client \
    python3 python3-pip python3-venv python3-dev \
    libpq-dev \
    || { apt-get update || true; apt-get install -y --no-install-recommends postgresql postgresql-client python3 python3-pip python3-dev libpq-dev; }

# Optional apt psycopg2 (adds dist-packages path for PYTHONPATH fallback)
apt-get install -y --no-install-recommends python3-psycopg2 2>/dev/null || true

systemctl enable postgresql
systemctl start postgresql

sudo -u postgres psql -v ON_ERROR_STOP=0 -c "CREATE USER tar1090 WITH PASSWORD 'tar1090';" 2>/dev/null || true
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "CREATE DATABASE tar1090 OWNER tar1090;" 2>/dev/null || true
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "GRANT ALL PRIVILEGES ON DATABASE tar1090 TO tar1090;" 2>/dev/null || true

analytics_dir="$ipath/analytics"
mkdir -p "$analytics_dir/ingest" "$analytics_dir/api" "$analytics_dir/jobs"
mkdir -p /var/lib/tar1090/photo-cache
mkdir -p "$PYLIB"

cp "$git_dir/services/ingest/"*.py "$analytics_dir/ingest/"
cp "$git_dir/services/api/"*.py "$analytics_dir/api/"
cp "$git_dir/services/jobs/"*.py "$analytics_dir/jobs/"
cp "$git_dir/services/schema-plain.sql" "$analytics_dir/schema-plain.sql"
cp "$git_dir/services/requirements.txt" "$analytics_dir/requirements.txt"
[[ -f "$git_dir/services/requirements-native.txt" ]] && \
    cp "$git_dir/services/requirements-native.txt" "$analytics_dir/requirements-native.txt"

# Drop old venv (source of psycopg2 import failures)
rm -rf "$ipath/analytics-venv"

# Install all Python deps into a single lib dir (no venv — same python3 as systemd)
echo "Installing Python packages into $PYLIB ..."
"$PYTHON3" -m pip install --upgrade pip wheel setuptools 2>/dev/null || true

if ! "$PYTHON3" -m pip install --target "$PYLIB" -r "$analytics_dir/requirements.txt"; then
    echo "pip install --target failed; trying with --break-system-packages ..."
    "$PYTHON3" -m pip install --break-system-packages --target "$PYLIB" -r "$analytics_dir/requirements.txt"
fi

# Build PYTHONPATH: our libs + system dist-packages (for apt python3-psycopg2 fallback)
PYTHONPATH_EXTRA="$PYLIB"
if "$PYTHON3" -c "import psycopg2" 2>/dev/null; then
    SITE=$("$PYTHON3" -c "import site; print(site.getsitepackages()[0] if site.getsitepackages() else '')" 2>/dev/null || true)
    if [[ -n "$SITE" && -d "$SITE" ]]; then
        PYTHONPATH_EXTRA="$PYLIB:$SITE"
    fi
fi

export PYTHONPATH="$PYTHONPATH_EXTRA"

if ! "$PYTHON3" -c "import psycopg2" 2>/dev/null; then
    echo "FATAL: psycopg2 not importable."
    echo "  PYTHONPATH=$PYTHONPATH"
    echo "  Try: sudo $PYTHON3 -m pip install --target $PYLIB psycopg2-binary"
    exit 1
fi
echo "psycopg2 OK: $("$PYTHON3" -c 'import psycopg2; print(psycopg2.__file__)')"

if ! "$PYTHON3" -c "import fastapi, uvicorn" 2>/dev/null; then
    echo "FATAL: fastapi/uvicorn not importable with PYTHONPATH=$PYTHONPATH"
    exit 1
fi

chown -R tar1090:tar1090 "$PYLIB" "$analytics_dir" /var/lib/tar1090/photo-cache 2>/dev/null || true
chmod -R a+rX "$PYLIB" 2>/dev/null || true

export PGPASSWORD=tar1090
psql -h 127.0.0.1 -U tar1090 -d tar1090 -f "$analytics_dir/schema-plain.sql" 2>/dev/null || \
    sudo -u postgres psql -d tar1090 -f "$analytics_dir/schema-plain.sql"

db_path=""
for d in "$ipath"/html/db-* "$ipath"/html-*/db-*; do
    [[ -d "$d" ]] && db_path="$d" && break
done

aircraft_json="$srcdir/aircraft.json"
[[ -f "$aircraft_json" ]] || aircraft_json="/run/readsb/aircraft.json"

cat > /etc/default/tar1090-analytics <<EOF
PYTHONPATH=$PYTHONPATH_EXTRA
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

cp "$git_dir/tar1090-analytics-api.service" /lib/systemd/system/
cp "$git_dir/tar1090-analytics-ingest.service" /lib/systemd/system/
cp "$git_dir/tar1090-analytics-jobs.service" /lib/systemd/system/

systemctl daemon-reload
systemctl enable tar1090-analytics-api tar1090-analytics-ingest tar1090-analytics-jobs
systemctl restart tar1090-analytics-api tar1090-analytics-ingest tar1090-analytics-jobs || true

echo "Analytics API: http://127.0.0.1:8080/health"
echo "Test: sudo -u tar1090 env PYTHONPATH=$PYTHONPATH_EXTRA $PYTHON3 -c 'import psycopg2; print(\"ok\")'"
echo "--------------"
