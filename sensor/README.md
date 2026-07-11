# Sensor (Raspberry Pi + Sense HAT)

Code that runs on the Pi inside the mower. Currently just an
accelerometer-prototype script; will grow into the IMU/environment/camera
data collector.

## Quick start on roboworm (no mower required)

If you only want to verify that the Raspberry Pi side is healthy right now,
use this shorter path first.

```bash
ssh <pi-user>@192.168.68.122
hostname
python3 --version
```

From the Pi shell:

```bash
cd ~/robo-lawn-mover || git clone <your-repo-url> ~/robo-lawn-mover
cd ~/robo-lawn-mover

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r sensor/requirements.txt
pip install -e .
```

Start the Pi-side API server:

```bash
python -m sensor.server
```

In a second SSH session:

```bash
curl -s http://127.0.0.1:8001/ | python -m json.tool
curl -i http://127.0.0.1:8001/api/imu
```

Expected:

- `/` returns JSON describing the service and endpoints.
- `/api/imu` can be `503 no samples yet` until IMU reads start.
- `latest.jpg` is `404` until `sensor/camera_snap.py` has produced snapshots.

If this works, the roboworm software stack is up and network-reachable.
You can add Sense HAT / camera validation later when hardware is available.

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

Important: for a live camera in the mower UI, keep this process running.
`sensor.server` does not capture images; it only serves `latest.jpg` that
`camera_snap.py` writes.

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

For live camera updates, run `camera_snap.py` and `sensor.server` at the same
time (separate terminals/processes).

## Optional: on-demand live video stream (from UI)

`sensor.server` now also supports a temporary MJPEG live feed that the UI can
start/stop on demand.

- `POST /api/camera/live/start?seconds=120` starts capture
- `POST /api/camera/live/stop` stops capture
- `GET /api/camera/live/status` returns running/error/ttl status
- `GET /live.mjpg` serves MJPEG frames while running

The mower UI exposes these controls in the Camera panel (`start video` /
`stop video`).

Important: this live mode owns the camera directly via `rpicam-vid`, and only
one process can own the camera device at a time. If you're running
`camera_snap.py` by hand (not as the `roboworm-camera-snap` systemd unit —
see below), stop it yourself before starting a live session. If it *is*
running as the systemd unit, `sensor.server` will pause it automatically for
the duration of the session (best-effort — needs the sudoers rule from "Run
at boot" below) and resume it when the session ends or times out.

Listens on `0.0.0.0:8001`. From the Mac browser:
<http://roboworm.local:8001/> for the status page,
<http://roboworm.local:8001/latest.jpg> for the live frame.

Configurable via env vars: `MOWER_IMU_HZ`, `MOWER_SENSOR_PORT`,
`MOWER_SENSOR_LOG_DIR`, `MOWER_SNAPSHOTS_DIR`,
`MOWER_SENSOR_RETENTION_DAYS`, `MOWER_CAMERA_SNAP_SERVICE` (the systemd unit
name `sensor.server` pauses/resumes around live video; default
`roboworm-camera-snap.service`).

**Don't run `sensor/server.py` and `sensor/imu_logger.py` at the same
time.** Both touch the I²C bus and would fight; the server already
includes the logger's functionality. `sensor/camera_snap.py` runs as a
separate process (it owns the camera; the server only serves the latest
file it produced).

## Run at boot (systemd)

Unit files for both long-running Pi processes live in `sensor/systemd/`:

- `roboworm-camera-snap.service` — periodic snapshotter (`camera_snap.py`)
- `roboworm-sensor.service` — the sensor API (`sensor.server`)

They're set up to run continuously and coordinate over the camera: when the
UI starts a live-video session, `sensor.server` stops the snapshot unit for
the duration and starts it again afterward (or on timeout/crash), so you get
standing snapshots most of the time and can still pull up live video on
demand.

Install (unit files are pre-filled for the `johan` user at
`/home/johan/dev/agentic-lawn-mower/mower` — adjust if that ever changes):

```bash
sudo cp sensor/systemd/roboworm-camera-snap.service /etc/systemd/system/
sudo cp sensor/systemd/roboworm-sensor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now roboworm-camera-snap.service
sudo systemctl enable --now roboworm-sensor.service
```

For `sensor.server` to be able to pause/resume the snapshotter, it needs
passwordless permission to run exactly two `systemctl` commands (nothing
broader). Install the scoped sudoers rule:

```bash
sudo cp sensor/systemd/roboworm-camera-control.sudoers \
    /etc/sudoers.d/roboworm-camera-control
sudo chmod 0440 /etc/sudoers.d/roboworm-camera-control
sudo visudo -c   # validates syntax before it takes effect
```

Without that rule, live video still works — `sensor.server` just logs that
it couldn't pause the snapshotter and lets `rpicam-vid` fail on its own if
the camera is actually busy.

Useful commands once installed:

```bash
sudo systemctl status roboworm-sensor.service roboworm-camera-snap.service
journalctl -u roboworm-sensor.service -f       # tail logs
sudo systemctl restart roboworm-sensor.service
```

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
