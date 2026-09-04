#!/usr/bin/env bash
#
# Bitcoin backtesting engine -- end-to-end entrypoint.
#
#   1. Starts a Postgres instance (Docker Compose) and waits for it to be healthy
#   2. Loads the 1-second BTCUSDT dataset into the raw landing table
#   3. Runs `dbt build` to construct and test the models
#   4. Runs the final query that answers the analyst's two questions
#
# The dataset is assumed to be present in the repo root at run time and is never
# committed (see the assignment brief).
#
# Usage:
#   ./run.sh                       # full run; skips the load if data is already there
#   ./run.sh --reload              # force a fresh COPY of the dataset
#   ./run.sh --skip-load           # iterate on dbt models without touching the load
#   ./run.sh --trade-hour 15       # restrict the backtest to a single hour
#   CSV_FILE=./fixture.csv ./run.sh --reload   # run against a smaller fixture
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# CSV_FILE is exported because docker-compose.yml interpolates it to decide
# which file gets bind-mounted into the container.
export CSV_FILE="${CSV_FILE:-./half2_BTCUSDT_1s.csv}"
CSV_IN_CONTAINER="/data/dataset.csv"
SERVICE="postgres"
PG_USER="dbt"
PG_DB="bitcoin"

RELOAD=false
SKIP_LOAD=false
TRADE_HOUR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reload)      RELOAD=true; shift ;;
    --skip-load)   SKIP_LOAD=true; shift ;;
    --trade-hour)  TRADE_HOUR="${2:?--trade-hour requires a value}"; shift 2 ;;
    -h|--help)     sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)             echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

psql_q() {  # quiet, tuples-only psql for scripted reads
  docker compose exec -T "$SERVICE" \
    psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -tAc "$1"
}

# ---------------------------------------------------------------- preflight --
log "Preflight"

command -v docker >/dev/null || fail "docker is not installed or not on PATH"
docker compose version >/dev/null 2>&1 || fail "the docker compose plugin is required"

# This check must happen before `compose up`: a bind mount whose source does not
# exist causes Docker to silently create a *directory* at that path, and the
# COPY then fails with a confusing "is a directory" error.
if [[ "$SKIP_LOAD" == false && ! -f "$CSV_FILE" ]]; then
  fail "expected the dataset at $CSV_FILE (download it from Kaggle, or pass --skip-load)"
fi

if [[ -x .venv/bin/dbt ]]; then
  DBT=".venv/bin/dbt"
elif command -v dbt >/dev/null; then
  DBT="dbt"
else
  fail "dbt not found; run 'uv sync' first"
fi
export DBT_PROFILES_DIR="$PWD"

echo "dataset : $CSV_FILE"
echo "dbt     : $DBT"

# ------------------------------------------------------------ 1. start db  --
log "1/4  Starting Postgres"

# --wait blocks on the healthcheck already defined in docker-compose.yml, so we
# never race the load against a database that is still initialising.
docker compose up -d --wait "$SERVICE"

# ------------------------------------------------------------ 2. load data --
log "2/4  Loading the dataset"

if [[ "$SKIP_LOAD" == true ]]; then
  echo "--skip-load given; leaving raw.btc_1s as-is"
else
  existing_rows="$(psql_q "SELECT CASE
                             WHEN to_regclass('raw.btc_1s') IS NULL THEN 0
                             ELSE (SELECT count(*) FROM raw.btc_1s)
                           END")"

  if [[ "$existing_rows" -gt 0 && "$RELOAD" == false ]]; then
    echo "raw.btc_1s already holds $existing_rows rows; skipping the load (use --reload to force)"
  else
    echo "COPYing $CSV_FILE -- this takes 10-30 minutes for the full 13.6 GB file"
    load_start=$SECONDS
    docker compose exec -T "$SERVICE" \
      psql -U "$PG_USER" -d "$PG_DB" \
           -v ON_ERROR_STOP=1 \
           -v csv_path="$CSV_IN_CONTAINER" \
           -f /sql/load.sql
    echo "loaded $(psql_q 'SELECT count(*) FROM raw.btc_1s') rows in $(( SECONDS - load_start ))s"
  fi
fi

# ------------------------------------------------------------- 3. dbt build --
log "3/4  Building dbt models"

dbt_args=(build)
if [[ -n "$TRADE_HOUR" ]]; then
  dbt_args+=(--vars "{trade_hour: $TRADE_HOUR}")
  echo "restricting the backtest to hour $TRADE_HOUR"
fi

"$DBT" "${dbt_args[@]}"

# ------------------------------------------------------------ 4. final query --
log "4/4  Answering the analyst's questions"

FINAL_QUERY="sql/final_query.sql"
if [[ -f "$FINAL_QUERY" ]]; then
  docker compose exec -T "$SERVICE" \
    psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -f "/$FINAL_QUERY"
else
  echo "no $FINAL_QUERY yet -- add it once the fact models are in place"
fi

log "Done"
