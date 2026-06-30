"""Prototype: print Sense HAT accelerometer readings.

Talks to the LSM9DS1 IMU chip on the Sense HAT directly over I²C, via
Adafruit's pure-Python CircuitPython library. No `sense-hat` / RTIMULib
dependency — works on any Python 3.10+ in a normal venv.

The Sense HAT wires the LSM9DS1 with non-default I²C addresses:
    accel+gyro = 0x6A    (Adafruit's default is 0x6B)
    magneto    = 0x1C    (Adafruit's default is 0x1E)
so we override them below.

With the Pi sitting flat you should see (x ≈ 0, y ≈ 0, z ≈ 9.8 m/s²) and
the magnitude staying near 9.81 m/s² in any stationary pose.
"""

from __future__ import annotations

import argparse
import time

# Sense HAT I²C addresses for the LSM9DS1.
SENSEHAT_AG_ADDR = 0x6A
SENSEHAT_MAG_ADDR = 0x1C


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hz", type=float, default=10.0,
                    help="sample rate. Default: 10")
    args = ap.parse_args()

    try:
        import board
        import busio
        import adafruit_lsm9ds1
    except ImportError as e:
        raise SystemExit(
            "Missing deps. From the project venv on the Pi:\n"
            "  pip install adafruit-circuitpython-lsm9ds1 adafruit-blinka\n"
            "and make sure I²C is enabled:\n"
            "  sudo raspi-config nonint do_i2c 0\n"
            f"({e})"
        ) from e

    i2c = busio.I2C(board.SCL, board.SDA)
    sensor = adafruit_lsm9ds1.LSM9DS1_I2C(
        i2c, mag_address=SENSEHAT_MAG_ADDR, xg_address=SENSEHAT_AG_ADDR,
    )

    period = 1.0 / args.hz
    print(f"Reading LSM9DS1 accelerometer at {args.hz:g} Hz. Ctrl-C to stop.\n")
    print(f"{'t(s)':>7}  {'ax':>8}  {'ay':>8}  {'az':>8}  {'|a|':>7}  (m/s²)")
    print("-" * 56)
    start = time.monotonic()
    try:
        while True:
            t0 = time.monotonic()
            x, y, z = sensor.acceleration  # m/s²
            mag = (x * x + y * y + z * z) ** 0.5
            elapsed = t0 - start
            print(f"{elapsed:7.2f}  {x:+8.3f}  {y:+8.3f}  {z:+8.3f}  {mag:7.3f}")
            time.sleep(max(0.0, period - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
