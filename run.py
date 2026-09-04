#!/usr/bin/env python3
"""Bitcoin backtesting engine -- end-to-end entrypoint.

  1. Starts a Postgres instance (Docker Compose) and waits for it to be healthy
  2. Loads the 1-second BTCUSDT dataset into the raw landing table
  3. Runs `dbt build` to construct and test the models
  4. Runs the final query that answers the analyst's two questions

The dataset is assumed to be present in the repo root at run time and is never
committed (see the assignment brief).

Usage:
  ./run.sh                         # full run; skips the load if data is there
  ./run.sh --reload                # force a fresh COPY of the dataset
  ./run.sh --skip-load             # iterate on dbt models without reloading
  ./run.sh --reload --load-only    # just load the data, skip dbt
  ./run.sh --trade-hour 15         # restrict the backtest to a single hour
  ./run.sh --csv ./fixture.csv --reload    # run against a smaller fixture
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path

from db import connect, scalar, show, statements

ROOT = Path(__file__).resolve().parent
SERVICE = "postgres"
# Everything Python-side runs through uv, so there is no venv to activate.
UV_RUN = ("uv", "run", "--")
CSV_IN_CONTAINER = "/data/dataset.csv"
LOAD_SQL = ROOT / "sql" / "load.sql"
FINAL_SQL = ROOT / "sql" / "final_query.sql"


def log(msg: str) -> None:
    print(f"\n\033[1m==> {msg}\033[0m", flush=True)


def fail(msg: str):
    raise SystemExit(f"\033[31merror: {msg}\033[0m")


def sh(*cmd: str) -> None:
    """Run a command, streaming its output; abort the script if it fails."""
    print(f"$ {' '.join(cmd)}", flush=True)
    if subprocess.run(cmd).returncode != 0:
        fail(f"`{' '.join(cmd)}` failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--reload", action="store_true", help="force a fresh COPY of the dataset"
    )
    group.add_argument(
        "--skip-load",
        action="store_true",
        help="iterate on dbt models without touching the load",
    )
    parser.add_argument(
        "--load-only",
        action="store_true",
        help="stop after the load; skip dbt build and the final query",
    )
    parser.add_argument(
        "--trade-hour", type=int, help="restrict the backtest to a single hour (0-23)"
    )
    parser.add_argument(
        "--csv",
        default=os.environ.get("CSV_FILE", "./half2_BTCUSDT_1s.csv"),
        help="dataset to load (default: %(default)s)",
    )
    return parser.parse_args()


def preflight(args: argparse.Namespace) -> None:
    log("Preflight")

    if not shutil.which("docker"):
        fail("docker is not installed or not on PATH")
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        fail("the docker compose plugin is required")

    # This check must happen before `compose up`: a bind mount whose source does
    # not exist causes Docker to silently create a *directory* at that path, and
    # the COPY then fails with a confusing "is a directory" error.
    csv = Path(args.csv)
    if not args.skip_load and not csv.is_file():
        fail(f"expected the dataset at {csv} (download it from Kaggle, or pass --skip-load)")

    # dbt is invoked as `uv run dbt`, which syncs the environment from uv.lock
    # first -- so uv being present is the only thing worth checking here.
    if not shutil.which("uv"):
        fail("uv is not installed -- see https://docs.astral.sh/uv/")

    # docker-compose.yml interpolates CSV_FILE to decide which file to bind-mount.
    # It must be absolute: Compose reads a source with no leading "./" or "/" as
    # a *named volume*, and Path() strips the "./" off a relative path.
    os.environ["CSV_FILE"] = str(csv.resolve())
    os.environ["DBT_PROFILES_DIR"] = str(ROOT)

    print(f"dataset : {csv}")


def start_db() -> None:
    log("1/4  Starting Postgres")
    # --wait blocks on the healthcheck already defined in docker-compose.yml, so
    # we never race the load against a database that is still initialising.
    sh("docker", "compose", "up", "-d", "--wait", SERVICE)


def load_data(args: argparse.Namespace, conn) -> None:
    log("2/4  Loading the dataset")

    if args.skip_load:
        print("--skip-load given; leaving raw.btc_1s as-is")
        return

    # Two round-trips on purpose: Postgres resolves table references at parse
    # time, so a single CASE/to_regclass guard still fails on the first run when
    # raw.btc_1s does not exist yet.
    table_exists = scalar(conn, "SELECT to_regclass('raw.btc_1s') IS NOT NULL")
    existing = scalar(conn, "SELECT count(*) FROM raw.btc_1s") if table_exists else 0
    if existing and not args.reload:
        print(
            f"raw.btc_1s already holds {existing:,} rows; "
            "skipping the load (use --reload to force)"
        )
        return

    print(f"COPYing {args.csv} -- this takes 10-30 minutes for the full 13.6 GB file")
    start = time.monotonic()
    with conn.cursor() as cur:
        # The COPY reads the bind-mounted file from inside the container rather
        # than streaming 13.6 GB through this client connection.
        for stmt in statements(LOAD_SQL.read_text(), csv_path=CSV_IN_CONTAINER):
            # Label with the first line that is not a comment -- these
            # statements each carry a paragraph of preamble.
            label = next(
                (ln.strip() for ln in stmt.splitlines()
                 if ln.strip() and not ln.lstrip().startswith("--")),
                stmt.splitlines()[0],
            )
            print(f"  {label[:72]}", flush=True)
            cur.execute(stmt)

    rows = scalar(conn, "SELECT count(*) FROM raw.btc_1s")
    print(f"loaded {rows:,} rows in {time.monotonic() - start:.0f}s")


def build_models(args: argparse.Namespace) -> None:
    log("3/4  Building dbt models")
    cmd = [*UV_RUN, "dbt", "build"]
    if args.trade_hour is not None:
        cmd += ["--vars", f"{{trade_hour: {args.trade_hour}}}"]
        print(f"restricting the backtest to hour {args.trade_hour}")
    sh(*cmd)


def final_query(conn) -> None:
    log("4/4  Answering the analyst's questions")
    if not FINAL_SQL.exists():
        rel = FINAL_SQL.relative_to(ROOT)
        print(f"no {rel} yet -- add it once the fact models are in place")
        return
    for stmt in statements(FINAL_SQL.read_text()):
        show(conn, stmt)


def main() -> None:
    os.chdir(ROOT)
    args = parse_args()
    preflight(args)

    start_db()
    # The healthcheck runs inside the container, so the published port can take
    # a moment longer to start accepting connections from the host.
    conn = connect(retries=15, delay=1.0)

    load_data(args, conn)
    if args.load_only:
        log("Done (--load-only; skipping dbt build)")
        return
    build_models(args)
    final_query(conn)

    log("Done")


if __name__ == "__main__":
    main()
