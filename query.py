#!/usr/bin/env python3
"""Run scratch queries against the backtest database.

  ./query.sh "select count(*) from raw.btc_1s"
  ./query.sh -f sql/final_query.sql
  echo "select 1" | ./query.sh
  ./query.sh                    # interactive prompt

Assumes Postgres is already up (./run.sh starts it).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from db import connect, show, statements


def run_script(conn, text: str) -> None:
    """Execute every statement in a script, printing each result set."""
    for stmt in statements(text):
        show(conn, stmt)


def repl(conn) -> None:
    print("Connected. End a statement with ';' to run it; Ctrl-D to exit.")
    buffer: list[str] = []
    while True:
        try:
            line = input("...> " if buffer else "sql> ")
        except EOFError:
            print()
            return
        buffer.append(line)
        if not line.rstrip().endswith(";"):
            continue
        try:
            run_script(conn, "\n".join(buffer))
        except Exception as exc:
            # autocommit means a failed statement leaves the session usable.
            print(f"\033[31m{exc}\033[0m", file=sys.stderr)
        buffer.clear()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("sql", nargs="?", help="SQL to run")
    parser.add_argument("-f", "--file", type=Path, help="run SQL from a file instead")
    args = parser.parse_args()

    conn = connect(retries=3)

    if args.file:
        run_script(conn, args.file.read_text())
    elif args.sql:
        run_script(conn, args.sql)
    elif not sys.stdin.isatty():
        run_script(conn, sys.stdin.read())
    else:
        repl(conn)


if __name__ == "__main__":
    main()
