"""HourlyRotatingCSV: rotation, gzip, retention behaviour with a fake clock."""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path

import pytest

from mower import logger as logger_mod
from mower.logger import MOWER_FIELDS, HourlyRotatingCSV


@pytest.fixture
def fake_clock(monkeypatch):
    """Patch logger_mod.datetime so we can advance time deterministically."""
    real_dt = logger_mod.datetime
    box = {"now": dt.datetime(2026, 6, 20, 10, 0, 0)}

    class FakeDT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return box["now"]

    monkeypatch.setattr(logger_mod, "datetime", FakeDT)
    return box


def _make(tmp_path: Path):
    return HourlyRotatingCSV(tmp_path, "mower", MOWER_FIELDS, retention_days=2)


def test_writes_open_csv_in_current_hour(tmp_path, fake_clock):
    log = _make(tmp_path)
    log.write({"ts": "x", "codename": "y", "len": "0", "binary_hex": "row0"})
    assert (tmp_path / "mower-2026-06-20T10.csv").exists()


def test_rolls_over_to_new_hour_and_gzips_old(tmp_path, fake_clock):
    log = _make(tmp_path)
    log.write({"ts": "x", "codename": "y", "len": "0", "binary_hex": "row0"})

    fake_clock["now"] = dt.datetime(2026, 6, 20, 11, 0, 0)
    log.write({"ts": "x", "codename": "y", "len": "0", "binary_hex": "row1"})

    names = sorted(p.name for p in tmp_path.iterdir())
    assert "mower-2026-06-20T10.csv.gz" in names
    assert "mower-2026-06-20T11.csv" in names

    with gzip.open(tmp_path / "mower-2026-06-20T10.csv.gz", "rt") as f:
        assert "row0" in f.read()


def test_retention_prunes_old_buckets(tmp_path, fake_clock):
    log = _make(tmp_path)            # retention_days=2
    log.write({"ts": "x", "codename": "y", "len": "0", "binary_hex": "row0"})

    fake_clock["now"] = dt.datetime(2026, 6, 23, 10, 0, 0)   # 3 days later
    log.write({"ts": "x", "codename": "y", "len": "0", "binary_hex": "row-later"})

    names = sorted(p.name for p in tmp_path.iterdir())
    assert all("2026-06-20" not in n for n in names), (
        f"day-old files should be pruned but found: {names}"
    )
    assert "mower-2026-06-23T10.csv" in names


def test_close_is_idempotent(tmp_path, fake_clock):
    log = _make(tmp_path)
    log.write({"ts": "x", "codename": "y", "len": "0", "binary_hex": "r"})
    log.close()
    log.close()       # second close should not raise
