"""Hourly-rotating CSV logger with gzip-and-prune retention.

Used for any continuous time-series channel — mower telemetry today, IMU
samples and environment data once we have a Pi inside the mower. Each
channel writes to its own prefix under a shared log root::

    <root>/
        mower-2026-06-29T14.csv         # current hour, plain
        mower-2026-06-29T13.csv.gz      # previous hours, gzipped
        ...
        imu-2026-06-29T14.csv           # different channel, same root

On hour rollover:
    1. Close and flush the current file.
    2. Gzip every closed file in the channel (idempotent).
    3. Delete files older than `retention_days`.

Schema is fixed per logger instance; rows are written via dicts.
"""

from __future__ import annotations

import csv
import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path


HOUR_FMT = "%Y-%m-%dT%H"


class HourlyRotatingCSV:
    """One channel of hourly CSVs with gzip+retention."""

    def __init__(self, root: str | Path, channel: str,
                 fieldnames: list[str], retention_days: int = 14):
        self.root = Path(root)
        self.channel = channel
        self.fieldnames = fieldnames
        self.retention_days = retention_days
        self.root.mkdir(parents=True, exist_ok=True)
        self._hour: str | None = None
        self._file = None
        self._writer: csv.DictWriter | None = None

    # ---------- public API ------------------------------------------------

    def write(self, row: dict) -> None:
        """Append a row, rotating if the hour boundary has been crossed."""
        hour = datetime.now().strftime(HOUR_FMT)
        if hour != self._hour:
            self._close_current()
            self._gzip_and_prune()
            self._open(hour)
        assert self._writer is not None and self._file is not None
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        """Close the current file. Call before exit if you want clean rotation."""
        self._close_current()
        self._gzip_and_prune()

    # ---------- internals -------------------------------------------------

    def _path_for(self, hour: str) -> Path:
        return self.root / f"{self.channel}-{hour}.csv"

    def _open(self, hour: str) -> None:
        path = self._path_for(hour)
        new = not path.exists()
        self._file = path.open("a", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=self.fieldnames)
        if new:
            self._writer.writeheader()
            self._file.flush()
        self._hour = hour

    def _close_current(self) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None
        self._writer = None
        self._hour = None

    def _gzip_and_prune(self) -> None:
        """Gzip every closed .csv in this channel; delete past retention."""
        now = datetime.now()
        retention_cutoff = now - timedelta(days=self.retention_days)
        # Closed files are anything not from the current hour. Since we just
        # closed the previous file, every .csv we see now is fair game.
        current_hour = now.strftime(HOUR_FMT)
        current_csv = self._path_for(current_hour).name

        for f in self.root.glob(f"{self.channel}-*.csv"):
            if f.name == current_csv:
                continue
            gz = f.with_name(f.name + ".gz")
            try:
                with f.open("rb") as src, gzip.open(gz, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                f.unlink()
            except OSError:
                # If gzip fails (disk full, etc.), keep the .csv.
                if gz.exists():
                    try:
                        gz.unlink()
                    except OSError:
                        pass

        # Delete anything older than retention.
        for f in self.root.glob(f"{self.channel}-*.csv*"):
            hour_str = self._hour_from_path(f)
            if hour_str is None:
                continue
            try:
                hour_dt = datetime.strptime(hour_str, HOUR_FMT)
            except ValueError:
                continue
            if hour_dt < retention_cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass

    def _hour_from_path(self, path: Path) -> str | None:
        # path.name like "mower-2026-06-29T14.csv" or ".csv.gz"
        prefix = f"{self.channel}-"
        name = path.name
        if not name.startswith(prefix):
            return None
        rest = name[len(prefix):]
        # Strip any trailing ".csv" or ".csv.gz"
        if rest.endswith(".csv.gz"):
            return rest[:-len(".csv.gz")]
        if rest.endswith(".csv"):
            return rest[:-len(".csv")]
        return None


# Schemas for the channels we know about today.

MOWER_FIELDS = ["ts", "codename", "len", "binary_hex"]
"""Schema for mower telemetry polling: timestamp + raw hex of response."""

IMU_FIELDS = ["ts", "ax", "ay", "az", "gx", "gy", "gz", "mx", "my", "mz"]
"""Schema for 9-DoF IMU samples (planned, used by the Pi-side logger)."""

ENV_FIELDS = ["ts", "temp_c", "pressure_hpa", "humidity_pct"]
"""Schema for environment samples (planned, used by the Pi-side logger)."""
