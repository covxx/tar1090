#!/bin/bash
# Native analytics install (no Docker) — called from install.sh
set -e
trap 'echo "[ERROR] install-analytics.sh line $LINENO: $BASH_COMMAND"' ERR

ipath="${1:-/usr/local/share/tar1090}"
srcdir="${2:-/run/readsb}"
git_dir="${3:-$ipath/git}"

if ! command -v systemctl &>/dev/null; then
    echo "Analytics: systemd required, skipping native analytics install."
    exit 0
fi

if ! command -v apt-get &>/dev/null; then
    echo "Analytics: apt-get required for native install, skipping."
    exit 0
fi

echo "--------------"
echo "Installing tar1090 analytics (native, no Docker)..."

packages=(
    postgresql postgresql-client
    python3-venv python3-pip python3-dev
    libpq-dev build-essential
)
use_system_psycopg2=no
if apt-cache show python3-psycopg2 &>/dev/null; then
    packages+=(python3-psycopg2)
    use_system_psycopg2=yes
fi
echo "Installing packages: ${packages[*]}"
apt-get install -y --no-install-recommends "${packages[@]}" || {
    apt-get update || true
    apt-get install -y --no-install-recommends "${packages[@]}"
}

systemctl enable postgresql
systemctl start postgresql

# Database user and database
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "CREATE USER tar1090 WITH PASSWORD 'tar1090';" 2>/dev/null || true
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "CREATE DATABASE tar1090 OWNER tar1090;" 2>/dev/null || true
sudo -u postgres psql -v ON_ERROR_STOP=0 -c "GRANT ALL PRIVILEGES ON DATABASE tar1090 TO tar1090;" 2>/dev/null || true

analytics_dir="$ipath/analytics"
mkdir -p "$analytics_dir/ingest" "$analytics_dir/api" "$analytics_dir/jobs"
mkdir -p /var/lib/tar1090/photo-cache
chown tar1090:tar1090 /var/lib/tar1090/photo-cache 2>/dev/null || true

cp "$git_dir/services/ingest/"*.py "$analytics_dir/ingest/"
cp "$git_dir/services/api/"*.py "$analytics_dir/api/"
cp "$git_dir/services/jobs/"*.py "$analytics_dir/jobs/"
cp "$git_dir/services/schema-plain.sql" "$analytics_dir/schema-plain.sql"
cp "$git_dir/services/requirements.txt" "$analytics_dir/requirements.txt"

# Python virtualenv (system-site-packages when using apt psycopg2 — avoids pip wheel build on ARM/Pi)
if [[ ! -x "$ipath/analytics-venv/bin/python" ]]; then
    if [[ "$use_system_psycopg2" == yes ]]; then
        python3 -m venv --system-site-packages "$ipath/analytics-venv"
    else
        python3 -m venv "$ipath/analytics-venv"
    fi
fi
PIP="$ipath/analytics-venv/bin/pip"
"$PIP" install -q --upgrade pip wheel setuptools

if [[ -f "$git_dir/services/requirements-native.txt" ]]; then
    reqfile="$git_dir/services/requirements-native.txt"
    cp "$reqfile" "$analytics_dir/requirements-native.txt"
elif [[ "$use_system_psycopg2" == yes ]]; then
    grep -vE '^\s*psycopg2' "$analytics_dir/requirements.txt" > "$analytics_dir/requirements-native.txt"
    reqfile="$analytics_dir/requirements-native.txt"
else
    reqfile="$analytics_dir/requirements.txt"
fi

echo "Installing Python packages from $reqfile ..."
if ! "$PIP" install -r "$reqfile"; then
    echo "pip install failed; retrying with full requirements (may compile psycopg2 — slow on Pi) ..."
    "$PIP" install -r "$analytics_dir/requirements.txt" || {
        echo "FATAL: could not install Python dependencies. Try: sudo apt install python3-psycopg2 libpq-dev python3-dev build-essential"
        exit 1
    }
fi

if ! "$ipath/analytics-venv/bin/python" -c "import psycopg2" 2>/dev/null; then
    echo "FATAL: psycopg2 not available. Run: sudo apt install python3-psycopg2 libpq-dev"
    exit 1
fi

# Apply schema
export PGPASSWORD=tar1090
psql -h 127.0.0.1 -U tar1090 -d tar1090 -f "$analytics_dir/schema-plain.sql" || {
    echo "Analytics: applying schema as postgres user..."
    sudo -u postgres psql -d tar1090 -f "$analytics_dir/schema-plain.sql"
}

# tar1090-db path for enrichment
db_path=""
for d in "$ipath"/html/db-* "$ipath"/html-*/db-*; do
    if [[ -d "$d" ]]; then
        db_path="$d"
        break
    fi
done

aircraft_json="$srcdir/aircraft.json"
if [[ ! -f "$aircraft_json" ]]; then
    aircraft_json="/run/readsb/aircraft.json"
fi

cat > /etc/default/tar1090-analytics <<EOF
DATABASE_URL=postgresql://tar1090:tar1090@127.0.0.1/tar1090
AIRCRAFT_JSON=$aircraft_json
TAR1090_DB_PATH=${db_path:-/usr/local/share/tar1090/html/db2}
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
echo "Analytics dashboard: open /tar1090/analytics.html on your map URL"
echo "--------------"
