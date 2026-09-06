"""Tests for run.py's argument validators.

These exist to fail fast. Without them a bad hour or a malformed date survives
into compiled SQL and surfaces minutes later as a Postgres cast error, well
after the point where the cause is obvious.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from run import bounded_int, iso_date  # noqa: E402


@pytest.mark.parametrize("raw,expected", [("0", 0), ("23", 23), ("07", 7)])
def test_bounded_int_accepts_the_range(raw, expected):
    assert bounded_int(0, 23, "--trade-hour")(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "24", "99"])
def test_bounded_int_rejects_out_of_range(raw):
    with pytest.raises(argparse.ArgumentTypeError, match="must be 0-23"):
        bounded_int(0, 23, "--trade-hour")(raw)


@pytest.mark.parametrize("raw", ["", "monday", "1.5", "12x"])
def test_bounded_int_rejects_non_integers(raw):
    with pytest.raises(argparse.ArgumentTypeError, match="whole number"):
        bounded_int(0, 23, "--trade-hour")(raw)


def test_bounded_int_names_the_flag_in_the_error():
    """argparse prefixes the flag too, but the range is the useful half."""
    with pytest.raises(argparse.ArgumentTypeError, match=r"--day-of-week must be 1-7"):
        bounded_int(1, 7, "--day-of-week")("8")


def test_iso_date_normalises():
    assert iso_date("2021-02-24") == "2021-02-24"


@pytest.mark.parametrize(
    "raw",
    [
        "2023-13-01",   # month 13
        "2023-02-30",   # day that does not exist
        "24-02-2021",   # day-first
        "not-a-date",
    ],
)
def test_iso_date_rejects_malformed(raw):
    with pytest.raises(argparse.ArgumentTypeError, match="YYYY-MM-DD"):
        iso_date(raw)
