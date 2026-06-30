# Sensor (Raspberry Pi + Sense HAT)

Code that runs on the Pi inside the mower. Currently just an
accelerometer-prototype script; will grow into the IMU/environment/camera
data collector.

## One-time Pi setup

Tested on Raspberry Pi 4B with the Sense HAT attached.

### 1. Make `python` mean 3.12 system-wide (via pyenv)

The Pi's apt-installed Python tends to lag (3.7 on Buster, 3.11 on Bookworm).
We want 3.12 globally so `python` and `python3` both resolve to it. Pyenv is
the clean way to do this without touching the system Python (which apt
itself depends on).

```bash
# Build dependencies — Python is compiled from source on the Pi
sudo apt update
sudo apt install -y make build-essential libssl-dev zlib1g-dev libbz2-dev \
  libreadline-dev libsqlite3-dev wget curl llvm libncursesw5-dev xz-utils \
  tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev git i2c-tools

# Install pyenv
curl https://pyenv.run | bash

# Wire pyenv into the shell. For bash:
cat >> ~/.bashrc <<'EOF'

# pyenv
export PYENV_ROOT="$HOME/.pyenv"
[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
EOF

# Replace ~/.bashrc with ~/.zshrc / ~/.config/fish/config.fish for those shells.

# Reload the shell so PYENV_ROOT is set
exec $SHELL
```

Now build Python 3.12 from source. This takes ~15–20 minutes on a Pi 4:

```bash
pyenv install 3.12.7      # or whichever 3.12.x is current
pyenv global 3.12.7
```

Verify:

```bash
which python              # -> /home/<you>/.pyenv/shims/python
python --version          # -> Python 3.12.7
python3 --version         # -> Python 3.12.7
```

Both `python` and `python3` now point to 3.12.7. The system Python is still
at `/usr/bin/python3.7` (or whatever) and unchanged.

### 2. Enable I²C and confirm the Sense HAT is alive

```bash
sudo raspi-config nonint do_i2c 0    # enables I²C (no-op if already on)
sudo i2cdetect -y 1                  # should show 0x1c, 0x46, 0x5c, 0x5f, 0x6a
```

The LSM9DS1 accel/gyro is at `0x6a`; magnetometer at `0x1c`. The other
addresses are pressure (0x5c), humidity (0x5f), and the LED-matrix MCU (0x46).
If `i2cdetect` shows nothing, the Sense HAT isn't seated or I²C isn't on.

### 3. Project-local venv

```bash
# Clone (or pull) the repo on the Pi
git clone <your-repo-url> ~/robo-lawn-mover
cd ~/robo-lawn-mover

# Create the venv — picks up Python 3.12 from pyenv
python -m venv .venv
source .venv/bin/activate
python --version          # -> Python 3.12.7

pip install -U pip
pip install -r sensor/requirements.txt
```

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

## Optional setup: camera + mower package

The IMU logger and the Pi-side server import `mower.logger.HourlyRotatingCSV`,
so the `mower` package needs to be installed in the venv:

```bash
# From the repo root, with .venv active
pip install -e .
```

The camera scripts (`camera_snap.py`, `server.py`'s snapshot serving) use
the Bookworm picamera2 stack. picamera2 depends on the system `libcamera`
and is awkward to install via pip, so install via apt and let the venv
inherit:

```bash
sudo apt install -y python3-picamera2 python3-libcamera

# Let the existing venv see system site-packages
sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' \
    .venv/pyvenv.cfg

# Confirm importable
.venv/bin/python -c "import picamera2; print(picamera2.__file__)"
```

Quick camera sanity (no Python needed):

```bash
rpicam-hello --list-cameras
rpicam-still -o /tmp/test.jpg -t 2000 --nopreview
```

## Run the continuous IMU logger

Standalone IMU-only mode — writes hourly-rotating CSVs to `./sensor-logs/`
with gzip and 14-day retention. Useful if you only want data collection
without serving anything.

```bash
python sensor/imu_logger.py --log-dir ./sensor-logs --hz 25
```

## Run the camera snapshotter

```bash
python sensor/camera_snap.py --dir ./snapshots --interval 60
```

Writes one JPEG per minute to `snapshots/YYYY-MM-DD/snap_HH-MM-SS.jpg` and
maintains `snapshots/latest.jpg` as a symlink to the most recent frame.
~1.6 GB at 14-day retention.

## Run the Pi-side server

Combined service: reads IMU at 25 Hz, logs to CSV (same format as
`imu_logger.py`), and serves HTTP/WebSocket endpoints the mower UI can
fetch:

| Endpoint | What |
| --- | --- |
| `GET /` | small JSON status page |
| `GET /latest.jpg` | most recent camera snapshot |
| `GET /api/imu` | latest IMU sample |
| `GET /api/imu/recent?seconds=10` | last N seconds as JSON |
| `WS  /api/imu/ws` | live IMU stream |

```bash
python -m sensor.server
```

Listens on `0.0.0.0:8001`. From the Mac browser:
<http://roboworm.local:8001/> for the status page,
<http://roboworm.local:8001/latest.jpg> for the live frame.

Configurable via env vars: `MOWER_IMU_HZ`, `MOWER_SENSOR_PORT`,
`MOWER_SENSOR_LOG_DIR`, `MOWER_SNAPSHOTS_DIR`,
`MOWER_SENSOR_RETENTION_DAYS`.

**Don't run `sensor/server.py` and `sensor/imu_logger.py` at the same
time.** Both touch the I²C bus and would fight; the server already
includes the logger's functionality. `sensor/camera_snap.py` runs as a
separate process (it owns the camera; the server only serves the latest
file it produced).

## Fun: tilt-ball LED animation

For when you want to confirm the IMU is alive without staring at a
terminal — drives the 8×8 LED matrix as a tilt-ball with a fading trail
and an impact flash when you bump the Pi.

```bash
sudo apt install -y python3-sense-hat   # only the LED matrix is used; no RTIMULib paths invoked
python sensor/led_tilt.py
```

Tilt the Pi — a white dot rolls around. Tap or shake — the matrix
flashes red. Ctrl-C clears the matrix and exits. Don't run this at the
same time as `server.py` / `imu_logger.py` (shared I²C bus).
