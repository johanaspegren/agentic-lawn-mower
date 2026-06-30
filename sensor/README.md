# Sensor (Raspberry Pi + Sense HAT)

Code that runs on the Pi inside the mower. Currently just an
accelerometer-prototype script; will grow into the IMU/environment/camera
data collector.

## One-time Pi setup

Tested on Raspberry Pi 4B running Raspberry Pi OS Trixie (Python 3.13
default), Sense HAT attached.

### 1. Base packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip python-is-python3 i2c-tools git
```

`python-is-python3` makes plain `python` resolve to `python3` so we don't
have to remember which is which.

```bash
python --version          # -> Python 3.13.x
```

### 2. Enable I²C and confirm the Sense HAT is alive

```bash
sudo raspi-config nonint do_i2c 0    # enables I²C (no-op if already on)
sudo i2cdetect -y 1                  # should show 0x1c, 0x46, 0x5c, 0x5f, 0x6a
```

The LSM9DS1 accel/gyro is at `0x6a` and the magnetometer at `0x1c`. The
other addresses are pressure (0x5c), humidity (0x5f), and the LED-matrix
MCU (0x46). If `i2cdetect` shows nothing, the Sense HAT isn't seated or
I²C isn't on.

### 3. Project-local venv

```bash
git clone <your-repo-url> ~/robo-lawn-mover
cd ~/robo-lawn-mover

python -m venv .venv
source .venv/bin/activate
python --version          # -> Python 3.13.x

pip install -U pip
pip install -r sensor/requirements.txt
```

### 4. Enable & install the camera stack (optional, for `camera_snap.py`)

If you have a Pi Camera Module attached (CSI ribbon), install picamera2
and make sure the venv can see it. picamera2 depends on the system
`libcamera`, so it must come from apt — pip-installing it in an isolated
venv won't work.

```bash
sudo apt install -y python3-picamera2 python3-libcamera

# Let the existing venv see the system site-packages
sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' \
    .venv/pyvenv.cfg

# Confirm: should print the picamera2 module path
.venv/bin/python -c "import picamera2; print(picamera2.__file__)"
```

Test the camera:

```bash
rpicam-still -o /tmp/test.jpg -t 1000 && ls -lh /tmp/test.jpg
```

### 5. Register the `mower` package (needed by the logger)

The logger reuses `HourlyRotatingCSV` from the main `mower` package, so we
need it importable inside the Pi's venv:

```bash
# from the repo root, with .venv active
pip install -e .
```

`pip install -e .` only installs the core package — fastapi/uvicorn are
optional and not pulled in.

## Run the accelerometer prototype

```bash
source .venv/bin/activate
python sensor/accel_test.py
```

You should see something like (Pi sitting flat on a desk):

```
   t(s)        ax        ay        az      |a|  (m/s²)
--------------------------------------------------------
   0.00    +0.012    -0.041    +9.812    9.812
   0.10    +0.010    -0.039    +9.808    9.809
   0.20    +0.013    -0.040    +9.811    9.811
```

Tilt the board to confirm the gravity vector moves between axes. `|a|`
should stay near 9.81 m/s² in any stationary pose; shaking or dropping
moves it.

If you see *"Errno 121 Remote I/O error"* or all-zero readings, I²C is
disabled or the Sense HAT isn't seated — re-run step 2.

## Run the continuous logger

Streams IMU samples (accel + gyro + magnetometer) to hourly-rotating CSVs
with gzip + 14-day retention, reusing the same `HourlyRotatingCSV`
infrastructure the mower telemetry uses:

```bash
source .venv/bin/activate
python sensor/imu_logger.py --log-dir ./sensor-logs --hz 25
```

Output lands at `sensor-logs/imu-2026-06-29T22.csv`. After each hour
rolls over the previous file gets gzipped automatically; anything past
14 days is deleted.

Storage budget at 25 Hz: ~13 MB/hour uncompressed, ~2–3 MB/hour gzipped,
~400 MB at full 14-day retention. Easy for any SD card.

To run as a long-lived service (survives reboots, restarts on crash), see
the systemd unit instructions further down (TODO once we're happy with
the prototype).

## Run the camera snapshotter

```bash
source .venv/bin/activate
python sensor/camera_snap.py --dir ./snapshots --interval 60
```

Writes one JPEG per minute to `snapshots/YYYY-MM-DD/snap_HH-MM-SS.jpg`,
maintains `snapshots/latest.jpg` as a symlink to the most recent frame,
and prunes day-buckets older than 14 days. Storage at default 1280×720 is
~115 MB/day → ~1.6 GB at full retention.

To peek at the latest frame from your laptop:

```bash
scp <pi-user>@mower-pi.local:~/dev/robo-lawn-mover/snapshots/latest.jpg .
open latest.jpg
```

A live MJPEG stream and a hook in the mower-control UI are TODO once the
snapshots prove useful.
