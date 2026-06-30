"""Periodic camera snapshots — a JPEG every --interval seconds.

Use to trace where the mower was when it goes quiet: scroll back through
day-bucketed snapshots and find the last one showing it moving.

Layout:
    <dir>/YYYY-MM-DD/snap_HH-MM-SS.jpg       one file per snapshot
    <dir>/latest.jpg                          symlink to most recent

Day-buckets older than --retention-days are deleted.

Storage budget at 1280x720 JPEG, one per minute:
    ~80 KB/frame, 1440 frames/day = ~115 MB/day, ~1.6 GB at 14 days.

Uses picamera2 (Bookworm+ stack). Make sure the venv inherits system
site-packages so picamera2 / libcamera are visible (see sensor/README.md).
"""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="./snapshots",
                    help="snapshot root directory. Default: ./snapshots")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="seconds between snapshots. Default: 60")
    ap.add_argument("--width", type=int, default=1280, help="width. Default: 1280")
    ap.add_argument("--height", type=int, default=720, help="height. Default: 720")
    ap.add_argument("--retention-days", type=int, default=14,
                    help="days to retain. Default: 14")
    args = ap.parse_args()

    try:
        from picamera2 import Picamera2
    except ImportError as e:
        raise SystemExit(
            "Can't import picamera2. Install the apt packages and make sure\n"
            "the venv inherits system site-packages:\n"
            "  sudo apt install -y python3-picamera2 python3-libcamera\n"
            "  # then edit .venv/pyvenv.cfg → include-system-site-packages = true\n"
            f"({e})"
        ) from e

    cam = Picamera2()
    cfg = cam.create_still_configuration(main={"size": (args.width, args.height)})
    cam.configure(cfg)
    cam.start()
    time.sleep(2)  # auto-exposure / white-balance settle

    root = Path(args.dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    latest = root / "latest.jpg"

    stop = {"go": True}

    def shutdown(*_: object) -> None:
        stop["go"] = False
        print("\n[snap] caught signal, stopping after current snapshot",
              file=sys.stderr)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[snap] {args.width}x{args.height} -> {root}/<day>/snap_*.jpg "
          f"every {args.interval:g} s (retain {args.retention_days} d). "
          f"Ctrl-C to stop.", file=sys.stderr)

    try:
        while stop["go"]:
            t0 = time.monotonic()
            now = datetime.now()
            day_dir = root / now.strftime("%Y-%m-%d")
            day_dir.mkdir(exist_ok=True)
            path = day_dir / f"snap_{now.strftime('%H-%M-%S')}.jpg"

            try:
                cam.capture_file(str(path))
            except Exception as e:
                print(f"[snap] capture failed: {e}", file=sys.stderr)
            else:
                # latest.jpg → most recent file (relative symlink, project-relocatable)
                if latest.is_symlink() or latest.exists():
                    try:
                        latest.unlink()
                    except OSError:
                        pass
                try:
                    latest.symlink_to(path.relative_to(root))
                except OSError as e:
                    print(f"[snap] couldn't update latest.jpg: {e}",
                          file=sys.stderr)
                print(f"[snap] {path.relative_to(root.parent)}", file=sys.stderr)

            # Retention pruning — once per snapshot is fine, ~one rmtree/day at most
            cutoff_date = (now - timedelta(days=args.retention_days)).date()
            for d in root.iterdir():
                if not d.is_dir():
                    continue
                try:
                    d_date = date.fromisoformat(d.name)
                except ValueError:
                    continue
                if d_date < cutoff_date:
                    shutil.rmtree(d, ignore_errors=True)
                    print(f"[snap] pruned {d.name}", file=sys.stderr)

            # Sleep until next interval, in small slices so Ctrl-C is responsive
            target = time.monotonic() + args.interval - (time.monotonic() - t0)
            while stop["go"] and time.monotonic() < target:
                time.sleep(min(0.5, max(0.0, target - time.monotonic())))
    finally:
        cam.stop()
        print("[snap] camera stopped", file=sys.stderr)


if __name__ == "__main__":
    main()
