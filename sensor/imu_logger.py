"""Continuous IMU logger.

Streams accel + gyro + magnetometer from the LSM9DS1 on the Sense HAT into
``<log-dir>/imu-<hour>.csv``, rotated hourly via the project's
HourlyRotatingCSV. Closed-hour files are gzipped; anything past
``--retention-days`` is deleted.

Requires the project's `mower` package to be importable. From the repo root:

    pip install -e .            # registers `mower` in the venv

Run with:

    python sensor/imu_logger.py --log-dir ./sensor-logs --hz 25
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime

# Sense HAT I²C addresses for the LSM9DS1 (overrides Adafruit defaults).
SENSEHAT_AG_ADDR = 0x6A
SENSEHAT_MAG_ADDR = 0x1C


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hz", type=float, default=25.0,
                    help="sample rate. Default: 25")
    ap.add_argument("--log-dir", default="./sensor-logs",
                    help="directory for rotating CSV output. Default: ./sensor-logs")
    ap.add_argument("--retention-days", type=int, default=14,
                    help="days to retain. Default: 14")
    ap.add_argument("--status-interval", type=float, default=10.0,
                    help="seconds between '[imu-logger] N samples' lines. "
                         "Default: 10")
    args = ap.parse_args()

    try:
        from mower.logger import IMU_FIELDS, HourlyRotatingCSV
    except ImportError as e:
        raise SystemExit(
            "Can't import mower.logger. From the repo root, run:\n"
            "  pip install -e .\n"
            f"({e})"
        ) from e

    try:
        import adafruit_lsm9ds1
        import board
        import busio
    except ImportError as e:
        raise SystemExit(
            "Missing CircuitPython libs. From the project venv on the Pi:\n"
            "  pip install -r sensor/requirements.txt\n"
            f"({e})"
        ) from e

    i2c = busio.I2C(board.SCL, board.SDA)
    sensor = adafruit_lsm9ds1.LSM9DS1_I2C(
        i2c, mag_address=SENSEHAT_MAG_ADDR, xg_address=SENSEHAT_AG_ADDR,
    )

    log = HourlyRotatingCSV(
        args.log_dir, "imu", IMU_FIELDS, args.retention_days,
    )

    stop = {"go": True}

    def shutdown(*_: object) -> None:
        stop["go"] = False
        print("\n[imu-logger] caught signal, stopping at next sample",
              file=sys.stderr)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    period = 1.0 / args.hz
    samples = 0
    last_status = time.monotonic()
    print(f"[imu-logger] {args.hz:g} Hz -> {args.log_dir}/imu-*.csv "
          f"(retain {args.retention_days}d). Ctrl-C to stop.",
          file=sys.stderr)

    try:
        while stop["go"]:
            t0 = time.monotonic()
            ax, ay, az = sensor.acceleration   # m/s²
            gx, gy, gz = sensor.gyro           # rad/s
            mx, my, mz = sensor.magnetic       # µT
            log.write({
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "ax": f"{ax:.4f}", "ay": f"{ay:.4f}", "az": f"{az:.4f}",
                "gx": f"{gx:.4f}", "gy": f"{gy:.4f}", "gz": f"{gz:.4f}",
                "mx": f"{mx:.4f}", "my": f"{my:.4f}", "mz": f"{mz:.4f}",
            })
            samples += 1
            now = time.monotonic()
            if now - last_status >= args.status_interval:
                rate = samples / (now - last_status) if samples else 0.0
                print(f"[imu-logger] +{samples} samples "
                      f"(~{rate:.1f} Hz)", file=sys.stderr)
                samples = 0
                last_status = now
            time.sleep(max(0.0, period - (now - t0)))
    finally:
        log.close()
        print("[imu-logger] closed", file=sys.stderr)


if __name__ == "__main__":
    main()
