"""Sense HAT LED animation: tilt-ball + impact flash.

Reads the LSM9DS1 accelerometer at ~20 Hz, projects the gravity vector
onto the 8x8 LED matrix as a bright dot with a fading trail. When the
acceleration magnitude exceeds SHAKE_THRESHOLD (i.e. you bump, tap, or
shake the Pi), the whole matrix briefly flashes red.

Adafruit LSM9DS1 is used for IMU access (matches the rest of the sensor/
scripts). The LED matrix is driven through the `sense_hat` library, which
talks to the rpi-sense kernel framebuffer — no RTIMULib involved as long
as we don't touch sense_hat's IMU/env methods.

Setup once on the Pi:

    sudo apt install -y python3-sense-hat
    # The venv must inherit system site-packages (set earlier for picamera2):
    # .venv/pyvenv.cfg → include-system-site-packages = true

Run:

    python sensor/led_tilt.py

Ctrl-C clears the matrix and exits.

NOTE: don't run this at the same time as sensor/server.py or
sensor/imu_logger.py — both touch the I²C bus.
"""

from __future__ import annotations

import time

SENSEHAT_AG_ADDR = 0x6A
SENSEHAT_MAG_ADDR = 0x1C

GRAVITY = 9.81
SHAKE_THRESHOLD = 12.0      # m/s², spike that triggers the red flash
SHAKE_FLASH_FRAMES = 3      # how many frames the red flash holds
TRAIL_LEN = 5               # how many past positions to draw fading

# Colour ramp for the trail, newest first.
TRAIL_COLORS = [
    (255, 255, 255),   # head (white)
    (180, 220, 255),
    (120, 180, 255),
    ( 60, 140, 220),
    ( 30, 100, 180),
]


def project_xy(ax: float, ay: float) -> tuple[int, int]:
    """Map (ax, ay) in m/s² to 8x8 matrix coordinates."""
    nx = (ax / GRAVITY + 1.0) * 3.5
    ny = (ay / GRAVITY + 1.0) * 3.5
    x = max(0, min(7, int(round(nx))))
    y = max(0, min(7, int(round(ny))))
    return x, y


def main() -> None:
    try:
        import adafruit_lsm9ds1
        import board
        import busio
        from sense_hat import SenseHat
    except ImportError as e:
        raise SystemExit(
            "Missing deps. On the Pi:\n"
            "  pip install -r sensor/requirements.txt\n"
            "  sudo apt install -y python3-sense-hat\n"
            f"({e})"
        ) from e

    i2c = busio.I2C(board.SCL, board.SDA)
    imu = adafruit_lsm9ds1.LSM9DS1_I2C(
        i2c, mag_address=SENSEHAT_MAG_ADDR, xg_address=SENSEHAT_AG_ADDR,
    )

    sense = SenseHat()
    sense.low_light = False
    sense.clear()

    trail: list[tuple[int, int]] = []
    flash_left = 0
    period = 1.0 / 20.0

    try:
        while True:
            t0 = time.monotonic()
            ax, ay, az = imu.acceleration
            mag = (ax * ax + ay * ay + az * az) ** 0.5

            x, y = project_xy(ax, ay)
            trail.append((x, y))
            if len(trail) > TRAIL_LEN:
                trail.pop(0)

            if mag > SHAKE_THRESHOLD:
                flash_left = SHAKE_FLASH_FRAMES

            # Build the 64-pixel buffer.
            pixels = [[0, 0, 0] for _ in range(64)]
            if flash_left > 0:
                pixels = [[180, 30, 30] for _ in range(64)]
                flash_left -= 1
            else:
                # Draw fading trail, oldest first so newest paints on top.
                for i, (tx, ty) in enumerate(reversed(trail)):
                    if i < len(TRAIL_COLORS):
                        pixels[ty * 8 + tx] = list(TRAIL_COLORS[i])

            sense.set_pixels(pixels)
            time.sleep(max(0.0, period - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        sense.clear()


if __name__ == "__main__":
    main()
