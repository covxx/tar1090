#!/bin/bash
# Native analytics install (no Docker) — Ubuntu/Debian
# PostgreSQL driver: apt python3-psycopg2 only (never pip psycopg2-binary on native).
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

apt_install() {
    apt-get install -y --no-install-recommends "$@" || {
        apt-get update || true
        apt-get install -y --no-install-recommends "$@"
    }
}

# psycopg2 from apt — avoids pip wheel/source builds entirely
apt_install postgresql postgresql-client python3 python3-pip python3-psycopg2

if ! "$PYTHON3" -c "import psycopg2" 2>/dev/null; then
    echo "FATAL: python3-psycopg2 installed but not importable by $PYTHON3"
    echo "  Check: $PYTHON3 -c 'import psycopg2'"
    exit 1
fi
echo "psycopg2 (apt): $("$PYTHON3" -c 'import psycopg2; print(psycopg2.__file__)')"

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

REQ_NATIVE="$analytics_dir/requirements-native.txt"
if [[ -f "$git_dir/services/requirements-native.txt" ]]; then
    cp "$git_dir/services/requirements-native.txt" "$REQ_NATIVE"
else
    cat > "$REQ_NATIVE" <<'EOF'
geohash2==1.1.0
fastapi==0.115.0
uvicorn[standard]==0.30.6
httpx==0.27.2
EOF
fi

rm -rf "$ipath/analytics-venv"

pip_install_target() {
    if "$PYTHON3" -m pip install --target "$PYLIB" "$@"; then
        return 0
    fi
    echo "pip install --target failed; retrying with --break-system-packages ..."
    "$PYTHON3" -m pip install --break-system-packages --target "$PYLIB" "$@"
}

echo "Installing Python packages (no psycopg2 via pip) into $PYLIB ..."
"$PYTHON3" -m pip install --upgrade pip wheel setuptools 2>/dev/null || true
pip_install_target -r "$REQ_NATIVE"

# PYLIB for fastapi/uvicorn; psycopg2 stays on system site-packages
PYTHONPATH_EXTRA="$PYLIB"
export PYTHONPATH="$PYTHONPATH_EXTRA"

if ! "$PYTHON3" -c "import psycopg2" 2>/dev/null; then
    echo "FATAL: psycopg2 not importable after install."
    exit 1
fi
if ! "$PYTHON3" -c "import fastapi, uvicorn, geohash2" 2>/dev/null; then
    echo "FATAL: analytics pip packages not importable with PYTHONPATH=$PYTHONPATH"
    "$PYTHON3" -m pip install --target "$PYLIB" -r "$REQ_NATIVE" -v || true
    exit 1
fi
echo "Analytics Python deps OK (psycopg2=apt, rest=pip -> $PYLIB)"

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

echo "Analytics API: http://127.0.0.1:9056/health"
echo "Test: sudo -u tar1090 env PYTHONPATH=$PYTHONPATH_EXTRA $PYTHON3 -c 'import psycopg2, fastapi; print(\"ok\")'"
echo "--------------"
