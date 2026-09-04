"""Shared Postgres helpers for run.py and query.py.

Connection settings mirror profiles.yml so these scripts and dbt always talk to
the same database, and both honour the standard PG* environment variables.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

import psycopg2
import sqlparse
from psycopg2.extensions import adapt
from tabulate import tabulate

DSN = dict(
    host=os.environ.get("PGHOST", "localhost"),
    port=int(os.environ.get("PGPORT", "5432")),
    user=os.environ.get("PGUSER", "dbt"),
    password=os.environ.get("PGPASSWORD", "dbt"),
    dbname=os.environ.get("PGDATABASE", "bitcoin"),
)


def connect(retries: int = 1, delay: float = 1.0):
    """Open an autocommit connection, retrying while the database boots.

    autocommit mirrors how `psql -f` behaves: each statement stands on its own,
    so a 30-minute COPY is not held open inside a transaction, and a failed
    scratch query does not poison the rest of the session.
    """
    last = None
    for attempt in range(retries):
        try:
            conn = psycopg2.connect(**DSN)
            conn.autocommit = True
            return conn
        except psycopg2.OperationalError as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(delay)
    raise SystemExit(
        f"error: could not connect to Postgres at {DSN['host']}:{DSN['port']}.\n"
        f"Is the container up? Try: ./run.sh\n{last}"
    )


def statements(sql_text: str, **params: str):
    """Yield executable statements from a psql-style script.

    Skips comment-only chunks, strips backslash meta-commands (which only the
    psql client understands) and expands :'name' variables -- so sql/load.sql
    stays runnable both from here and by `psql -f` inside the container.
    """
    for chunk in sqlparse.split(sql_text):
        stmt = "\n".join(
            line for line in chunk.splitlines() if not line.lstrip().startswith("\\")
        ).strip()
        if not stmt:
            continue
        # token_first(skip_cm=True) is None when the chunk is only comments.
        parsed = sqlparse.parse(stmt)
        if not parsed or parsed[0].token_first(skip_cm=True) is None:
            continue
        for name, value in params.items():
            stmt = stmt.replace(f":'{name}'", adapt(value).getquoted().decode())
        yield stmt


def scalar(conn, sql, params=None):
    """Run a query and return the first column of the first row."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return None if row is None else row[0]


def _cell(value):
    # tabulate's own float handling rounds to 6 significant figures, which would
    # quietly mangle prices and PnL. Pre-format numerics to their exact text.
    if isinstance(value, (Decimal, float)):
        return str(value)
    return value


def show(conn, sql, params=None) -> int:
    """Run a statement and print any result set as a table."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description is None:  # DDL, COPY, etc. -- no rows to print
            print(cur.statusmessage)
            return 0
        rows = [[_cell(v) for v in row] for row in cur.fetchall()]
        headers = [d.name for d in cur.description]
    print(tabulate(rows, headers=headers, tablefmt="psql",
                   disable_numparse=True, missingval="NULL"))
    print(f"({len(rows)} row{'' if len(rows) == 1 else 's'})")
    return len(rows)
