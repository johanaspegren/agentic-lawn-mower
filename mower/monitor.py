"""Polling logger for the mower.

Repeatedly queries the mower at a configurable interval and writes every
response to either a single CSV (ad-hoc captures) or an hourly-rotating
channel under a log root (long-running monitoring).

Single-file mode (`out_path` set) is good for the short labeled captures we
use to reverse-engineer bytes: one CSV per scenario, easy to diff.

Rotating mode (`log_dir` set) is the production mode: drop the monitor on
the Pi (or any always-on machine), get 14 days of history broken into
hourly files, gzipped after rollover. Use this when you want to be able to
post-mortem an incident hours or days later.

CSV schema (both modes): ``ts, codename, len, binary_hex``.
"""

from __future__ import annotations

import csv
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from .client import MowerClient
from .logger import MOWER_FIELDS, HourlyRotatingCSV


def _row(ts: datetime, codename: str, binary: bytes | None) -> dict:
    return {
        "ts": ts.isoformat(timespec="seconds"),
        "codename": codename,
        "len": str(len(binary)) if binary is not None else "",
        "binary_hex": binary.hex() if binary is not None else "",
    }


def monitor(ip: str, *, out_path: str | None = None,
            log_dir: str | None = None, channel: str = "mower",
            retention_days: int = 14,
            interval: float = 60.0, poll_state: bool = True,
            port: int = 9600) -> None:
    """Poll the mower forever.

    Exactly one of `out_path` (single file) or `log_dir` (rotating) must be
    set. The rotating channel writes to `<log_dir>/<channel>-<hour>.csv`,
    gzips closed hours, and prunes files past `retention_days`.

    Ctrl-C exits cleanly without sending stop (we're observing, not driving).
    """
    if (out_path is None) == (log_dir is None):
        raise ValueError("monitor: pass exactly one of out_path or log_dir")

    if log_dir is not None:
        sink = _RotatingSink(log_dir, channel, retention_days)
    else:
        assert out_path is not None
        sink = _SingleFileSink(out_path)

    stop_flag = {"go": True}

    def handle_sigint(*_):
        stop_flag["go"] = False
        print("\n[monitor] stopping at next sample", file=sys.stderr)

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    print(f"[monitor] {sink} every {interval}s (poll_state={poll_state})",
          file=sys.stderr)

    while stop_flag["go"]:
        ts = datetime.now()
        try:
            # Avoid the `with`-block stop-on-exit failsafe: we're observing,
            # not driving, and we don't want to nudge the mower with a stop
            # command on every polling cycle.
            client = MowerClient(ip, port=port, prime=False)
            client.connect()
            try:
                idle = client.cmd("idle_poll", linger=0.5)
                for r in idle:
                    sink.write(_row(ts, r.codename, r.binary))
                state = []
                if poll_state:
                    state = client.cmd("query_state", linger=1.0)
                    for r in state:
                        sink.write(_row(ts, r.codename, r.binary))
                got = len(idle) + len(state)
                print(f"[monitor] {ts.isoformat(timespec='seconds')} "
                      f"got {got} packet(s)", file=sys.stderr)
            finally:
                client.close()
        except Exception as e:
            print(f"[monitor] error at {ts.isoformat()}: {e}", file=sys.stderr)
            sink.write({
                "ts": ts.isoformat(timespec="seconds"),
                "codename": "ERROR",
                "len": "",
                "binary_hex": str(e),
            })

        # Sleep in small slices so Ctrl-C is responsive.
        target = time.monotonic() + interval
        while stop_flag["go"] and time.monotonic() < target:
            time.sleep(min(0.5, max(0.0, target - time.monotonic())))

    sink.close()
    print("[monitor] closed", file=sys.stderr)


# --- sinks ------------------------------------------------------------------


class _SingleFileSink:
    def __init__(self, path: str):
        self.path = Path(path)
        new = not self.path.exists()
        self._file = self.path.open("a", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=MOWER_FIELDS)
        if new:
            self._writer.writeheader()
            self._file.flush()

    def write(self, row: dict) -> None:
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __str__(self) -> str:
        return f"writing to {self.path}"


class _RotatingSink:
    def __init__(self, log_dir: str, channel: str, retention_days: int):
        self._log = HourlyRotatingCSV(
            root=log_dir, channel=channel,
            fieldnames=MOWER_FIELDS, retention_days=retention_days,
        )
        self._desc = (f"rotating into {log_dir}/{channel}-*.csv "
                      f"(retain {retention_days}d)")

    def write(self, row: dict) -> None:
        self._log.write(row)

    def close(self) -> None:
        self._log.close()

    def __str__(self) -> str:
        return self._desc
