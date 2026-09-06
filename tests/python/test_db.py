"""Tests for db.statements() -- the psql-script splitter.

Everything else in db.py is a thin wrapper over psycopg2 and is exercised by
every `./run.sh`. `statements()` is the exception: it is real parsing logic,
and it is what keeps `sql/load.sql` runnable *both* through run.py and by
`psql -f /sql/load.sql` inside the container. A regression here would show up
as a confusing mid-load failure, so it is worth pinning directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from db import statements  # noqa: E402


def test_splits_on_semicolons():
    assert list(statements("select 1; select 2")) == ["select 1;", "select 2"]


def test_strips_backslash_meta_commands():
    """`\\set` and friends are psql client directives; psycopg2 chokes on them."""
    out = list(statements("\\set ON_ERROR_STOP on\nselect 1;"))
    assert out == ["select 1;"]


def test_drops_comment_only_chunks():
    """A trailing comment block parses to no tokens and must not be executed."""
    assert list(statements("select 1;\n-- just a trailing note\n")) == ["select 1;"]


def test_ignores_blank_input():
    assert list(statements("   \n\n  ")) == []


def test_expands_named_placeholders():
    out = list(statements("copy t from :'csv_path';", csv_path="/data/x.csv"))
    assert out == ["copy t from '/data/x.csv';"]


def test_placeholder_expansion_quotes_the_value():
    """Adapted through psycopg2, so a quote in the path cannot break out."""
    (out,) = statements("copy t from :'csv_path';", csv_path="/tmp/it's.csv")
    assert "it''s" in out or "it\\'s" in out
    assert out.count("'") % 2 == 0


def test_unreferenced_placeholders_are_left_alone():
    (out,) = statements("select :'other';", csv_path="/data/x.csv")
    assert out == "select :'other';"


# --------------------------------------------------------------- load.sql ---
# The reason this module exists: the real script has to survive the splitter.


def first_keyword(stmt: str) -> str:
    """The leading keyword, ignoring the comment block above it.

    Statements keep their preamble -- run.py relies on that to label each step
    as it executes -- so the first *line* is usually a comment, not SQL.
    """
    for line in stmt.splitlines():
        if line.strip() and not line.lstrip().startswith("--"):
            return line.split(maxsplit=1)[0].lower()
    raise AssertionError(f"no SQL found in {stmt!r}")


@pytest.fixture(scope="module")
def load_sql_statements() -> list[str]:
    text = (ROOT / "sql" / "load.sql").read_text()
    return list(statements(text, csv_path="/data/dataset.csv"))


def test_load_sql_yields_every_step(load_sql_statements):
    assert [first_keyword(s) for s in load_sql_statements] == [
        "create",   # schema raw
        "drop",     # the old landing table
        "create",   # the new one
        "copy",     # the server-side load
        "create",   # the index, after the COPY rather than before
        "analyze",
    ]


def test_load_sql_keeps_each_statement_preamble(load_sql_statements):
    """run.py labels each step from the comment block, so it must survive."""
    copy = next(s for s in load_sql_statements if first_keyword(s) == "copy")
    assert copy.startswith("--")
    assert "server-side" in copy.lower()


def test_load_sql_leaves_nothing_for_psycopg2_to_choke_on(load_sql_statements):
    for stmt in load_sql_statements:
        assert not any(
            line.lstrip().startswith("\\") for line in stmt.splitlines()
        ), f"backslash command survived: {stmt!r}"
        assert ":'" not in stmt, f"unexpanded placeholder: {stmt!r}"


def test_load_sql_drops_with_cascade(load_sql_statements):
    """The staging view depends on the table; a bare drop fails mid---reload."""
    (drop,) = [s for s in load_sql_statements if first_keyword(s) == "drop"]
    assert "cascade" in drop.lower()
