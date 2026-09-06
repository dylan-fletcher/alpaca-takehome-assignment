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
  ./run.sh --day-of-week 1         # ...to Mondays only
  ./run.sh --start-date 2023-01-01 # ...to a narrower window
  ./run.sh --csv ./fixture.csv --reload    # run against a smaller fixture
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

from db import connect, scalar, show, statements

ROOT = Path(__file__).resolve().parent
SERVICE = "postgres"
# Everything Python-side runs through uv, so there is no venv to activate.
UV_RUN = ("uv", "run", "--")
CSV_IN_CONTAINER = "/data/dataset.csv"
# Stands in for the dataset when there is none to mount; see preflight().
NULL_MOUNT = "/dev/null"
LOAD_SQL = ROOT / "sql" / "load.sql"
FINAL_SQL = ROOT / "sql" / "final_query.sql"
CHART_SCRIPT = ROOT / "scripts" / "make_charts.py"


def log(msg: str) -> None:
    print(f"\n\033[1m==> {msg}\033[0m", flush=True)


def fail(msg: str):
    raise SystemExit(f"\033[31merror: {msg}\033[0m")


def sh(*cmd: str) -> None:
    """Run a command, streaming its output; abort the script if it fails."""
    print(f"$ {' '.join(cmd)}", flush=True)
    if subprocess.run(cmd).returncode != 0:
        fail(f"`{' '.join(cmd)}` failed")


def bounded_int(low: int, high: int, label: str):
    """An argparse type that reports the accepted range, not a stack trace."""

    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{label} must be a whole number, not {raw!r}"
            ) from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{label} must be {low}-{high}, not {value}")
        return value

    return parse


def iso_date(raw: str) -> str:
    """An argparse type for YYYY-MM-DD.

    Worth catching here: a malformed date otherwise survives all the way into
    the compiled SQL and surfaces ten minutes later as a Postgres cast error.
    """
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a date as YYYY-MM-DD, not {raw!r}"
        ) from None


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
    # The brief asks for the analysis to be repeatable "for different hours and
    # days of entering the market". Each of these maps to the dbt var of the
    # same name; leaving one unset keeps the dbt_project.yml default.
    window = parser.add_argument_group("backtest window")
    window.add_argument(
        "--trade-hour",
        type=bounded_int(0, 23, "--trade-hour"),
        help="restrict the backtest to one hour of the day (0-23, UTC)",
    )
    window.add_argument(
        "--day-of-week",
        type=bounded_int(1, 7, "--day-of-week"),
        help="restrict the backtest to one weekday (1=Monday .. 7=Sunday)",
    )
    window.add_argument(
        "--start-date",
        type=iso_date,
        metavar="YYYY-MM-DD",
        help="first date to trade, inclusive",
    )
    window.add_argument(
        "--end-date",
        type=iso_date,
        metavar="YYYY-MM-DD",
        help="last date to trade, inclusive",
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
    have_csv = csv.is_file()
    if not args.skip_load and not have_csv:
        fail(f"expected the dataset at {csv} (download it from Kaggle, or pass --skip-load)")

    # dbt is invoked as `uv run dbt`, which syncs the environment from uv.lock
    # first -- so uv being present is the only thing worth checking here.
    if not shutil.which("uv"):
        fail("uv is not installed -- see https://docs.astral.sh/uv/")

    # docker-compose.yml interpolates CSV_FILE to decide which file to bind-mount.
    # It must be absolute: Compose reads a source with no leading "./" or "/" as
    # a *named volume*, and Path() strips the "./" off a relative path.
    #
    # NULL_MOUNT covers `--skip-load` on a machine without the dataset, which is
    # the normal way to work on models. Pointing the mount at the missing path
    # instead would have Compose create a root-owned *directory* there -- which
    # needs sudo to remove, and makes the next real load fail with a confusing
    # "is a directory". /dev/null always exists and is never read: the load path
    # is unreachable without a real file, per the check above.
    os.environ["CSV_FILE"] = str(csv.resolve()) if have_csv else NULL_MOUNT
    os.environ["DBT_PROFILES_DIR"] = str(ROOT)

    print(f"dataset : {csv}" if have_csv else "dataset : none (--skip-load)")


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

    print(f"COPYing {args.csv} -- about 10 minutes for the full 13.6 GB file")
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

    # Only the flags actually given are forwarded. Passing an unset one as null
    # would override the dbt_project.yml default rather than defer to it.
    overrides = {
        "trade_hour": args.trade_hour,
        "trade_day_of_week": args.day_of_week,
        "backtest_start_date": args.start_date,
        "backtest_end_date": args.end_date,
    }
    given = {name: value for name, value in overrides.items() if value is not None}
    if given:
        # JSON is valid YAML, so dbt takes this as-is and no quoting rules of
        # our own are needed around dates.
        cmd += ["--vars", json.dumps(given)]
        for name, value in given.items():
            print(f"  {name}: {value}")
    sh(*cmd)


def final_query(conn) -> None:
    log("4/4  Answering the analyst's questions")
    if not FINAL_SQL.exists():
        rel = FINAL_SQL.relative_to(ROOT)
        print(f"no {rel} yet -- add it once the fact models are in place")
        return
    for stmt in statements(FINAL_SQL.read_text()):
        show(conn, stmt)

    # Regenerated here rather than left to a manual step, so the charts in
    # README.md cannot drift from the numbers printed above. Output is
    # byte-stable on unchanged data, so a re-run leaves git clean.
    print()
    sh(*UV_RUN, "python", str(CHART_SCRIPT))


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
