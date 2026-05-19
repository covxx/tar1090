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
VENV_DIR="$ipath/analytics-venv"
VENV_PY="$VENV_DIR/bin/python"

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
apt_install postgresql postgresql-client python3 python3-pip python3-venv

# ---- Ensure local service account exists ----
if ! getent group tar1090 >/dev/null; then
    groupadd --system tar1090
fi
if ! id -u tar1090 >/dev/null 2>&1; then
    useradd --system --gid tar1090 --home-dir /nonexistent --shell /usr/sbin/nologin tar1090
fi

# ---- Nuke old analytics python environments/leftovers ----
rm -rf "$ipath/analytics-lib" "$ipath/analytics-venv"
rm -f "$ipath/analytics/requirements.txt" "$ipath/analytics/requirements-native.txt"

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
cp "$git_dir/services/schema-v2.sql" "$analytics_dir/schema-v2.sql" 2>/dev/null || true
cp "$git_dir/services/ingest/"*.json "$analytics_dir/ingest/" 2>/dev/null || true
chown -R tar1090:tar1090 "$analytics_dir" /var/lib/tar1090/photo-cache 2>/dev/null || true

# ---- Create dedicated virtualenv and install python deps there ----
echo "Creating analytics virtualenv at $VENV_DIR ..."
"$PYTHON3" -m venv "$VENV_DIR"
"$VENV_PY" -m pip install --no-cache-dir --upgrade pip setuptools wheel
echo "Installing pg8000 + fastapi + uvicorn + deps into virtualenv ..."
"$VENV_PY" -m pip install --no-cache-dir \
    "pg8000>=1.31.2" \
    "fastapi==0.115.0" \
    "uvicorn[standard]==0.30.6" \
    "httpx==0.27.2" \
    "geohash2==1.1.0"

# ---- Verify imports ----
"$VENV_PY" -c "import pg8000.native; print('pg8000 OK:', pg8000.__version__)" || {
    echo "FATAL: pg8000 not importable. pip output above should explain why."
    exit 1
}
"$VENV_PY" -c "import fastapi, uvicorn, httpx, geohash2; print('fastapi+uvicorn+httpx+geohash2 OK')" || {
    echo "FATAL: Python deps not importable."
    exit 1
}
echo "All Python deps OK."

# ---- Quick DB connection test ----
"$VENV_PY" -c "
import pg8000.native
try:
    c = pg8000.native.Connection(user='tar1090', password='tar1090', host='127.0.0.1', port=5432, database='tar1090')
    c.run('SELECT 1')
    c.close()
    print('DB connection OK')
except Exception as e:
    print(f'DB connection test failed: {e}')
" || true

# ---- Apply schema ----
export PGPASSWORD=tar1090
psql -h 127.0.0.1 -U tar1090 -d tar1090 -f "$analytics_dir/schema-plain.sql" 2>/dev/null || \
    sudo -u postgres psql -d tar1090 -f "$analytics_dir/schema-plain.sql"
if [[ -f "$analytics_dir/schema-v2.sql" ]]; then
    psql -h 127.0.0.1 -U tar1090 -d tar1090 -f "$analytics_dir/schema-v2.sql" 2>/dev/null || \
        sudo -u postgres psql -d tar1090 -f "$analytics_dir/schema-v2.sql"
fi

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
sed -i "s#^ExecStart=.*#ExecStart=$VENV_PY -m uvicorn main:app --host 0.0.0.0 --port 9056#" /lib/systemd/system/tar1090-analytics-api.service
sed -i "s#^ExecStart=.*#ExecStart=$VENV_PY main.py#" /lib/systemd/system/tar1090-analytics-ingest.service
sed -i "s#^ExecStart=.*#ExecStart=$VENV_PY main.py#" /lib/systemd/system/tar1090-analytics-jobs.service

systemctl daemon-reload
systemctl enable tar1090-analytics-api tar1090-analytics-ingest tar1090-analytics-jobs
systemctl restart tar1090-analytics-api tar1090-analytics-ingest tar1090-analytics-jobs || true

echo "Analytics API: http://127.0.0.1:9056/health"
echo "--------------"
