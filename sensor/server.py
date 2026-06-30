"""Pi-side HTTP/WebSocket server.

Combines:
  - background IMU loop (25 Hz by default), writing samples to a rotating
    CSV via mower.logger.HourlyRotatingCSV
  - in-memory ring buffer of the last N seconds for quick "recent" queries
  - WebSocket fanout so the mower UI can subscribe to live samples
  - static file serving for the latest camera snapshot

The mower's main control UI (running on your laptop) can embed:

    <img src="http://roboworm.local:8001/latest.jpg">

and fetch JSON from /api/imu/recent for plotting.

Run:

    cd ~/dev/robo-lawn-mover
    source .venv/bin/activate
    python -m sensor.server

Or with uvicorn directly:

    uvicorn --host 0.0.0.0 --port 8001 sensor.server:app

NOTE: Only run *one* of (sensor/server.py, sensor/imu_logger.py) at a time.
Both touch the I²C bus and would race.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Runtime config (overridable via env vars)
IMU_HZ = float(os.environ.get("MOWER_IMU_HZ", "25"))
IMU_BUFFER_SECONDS = float(os.environ.get("MOWER_IMU_BUFFER_SECONDS", "60"))
SNAPSHOTS_DIR = Path(os.environ.get("MOWER_SNAPSHOTS_DIR",
                                     PROJECT_ROOT / "snapshots"))
LOG_DIR = Path(os.environ.get("MOWER_SENSOR_LOG_DIR",
                              PROJECT_ROOT / "sensor-logs"))
RETENTION_DAYS = int(os.environ.get("MOWER_SENSOR_RETENTION_DAYS", "14"))

# Sense HAT LSM9DS1 I²C addresses
SENSEHAT_AG_ADDR = 0x6A
SENSEHAT_MAG_ADDR = 0x1C


class _State:
    """Server-wide state: rolling sample buffer + WS subscribers."""

    def __init__(self) -> None:
        self.buffer: deque[dict] = deque(maxlen=int(IMU_HZ * IMU_BUFFER_SECONDS))
        self.subscribers: set = set()
        self.sub_lock = asyncio.Lock()


state = _State()


async def _imu_loop() -> None:
    """Background task: read IMU continuously, log, broadcast."""
    try:
        import adafruit_lsm9ds1
        import board
        import busio
    except ImportError as e:
        print(f"[server] IMU libs unavailable, IMU loop disabled: {e}",
              file=sys.stderr)
        return

    try:
        from mower.logger import IMU_FIELDS, HourlyRotatingCSV
    except ImportError as e:
        print(f"[server] mower.logger unavailable, running without CSV: {e}",
              file=sys.stderr)
        IMU_FIELDS = None
        log = None
    else:
        log = HourlyRotatingCSV(
            LOG_DIR, "imu", IMU_FIELDS, retention_days=RETENTION_DAYS,
        )

    i2c = busio.I2C(board.SCL, board.SDA)
    sensor = adafruit_lsm9ds1.LSM9DS1_I2C(
        i2c, mag_address=SENSEHAT_MAG_ADDR, xg_address=SENSEHAT_AG_ADDR,
    )

    period = 1.0 / IMU_HZ
    print(f"[server] IMU loop @ {IMU_HZ:g} Hz, "
          f"buffer={IMU_BUFFER_SECONDS:g}s, "
          f"log_dir={LOG_DIR}", file=sys.stderr)

    while True:
        t0 = time.monotonic()
        try:
            ax, ay, az = sensor.acceleration
            gx, gy, gz = sensor.gyro
            mx, my, mz = sensor.magnetic
        except OSError as e:
            print(f"[server] IMU read error: {e}", file=sys.stderr)
            await asyncio.sleep(0.5)
            continue

        sample = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "ax": ax, "ay": ay, "az": az,
            "gx": gx, "gy": gy, "gz": gz,
            "mx": mx, "my": my, "mz": mz,
        }
        state.buffer.append(sample)

        if log is not None:
            # Serialise floats with fixed precision to keep the CSV tidy.
            row = {k: f"{v:.4f}" if isinstance(v, float) else v
                   for k, v in sample.items()}
            await asyncio.to_thread(log.write, row)

        # Fan out to all WS subscribers (non-blocking; drop on failure).
        async with state.sub_lock:
            subs = list(state.subscribers)
        for ws in subs:
            try:
                await ws.send_json(sample)
            except Exception:
                pass  # WS handler's finally will clean up

        elapsed = time.monotonic() - t0
        await asyncio.sleep(max(0.0, period - elapsed))


# --- FastAPI app ------------------------------------------------------------


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_imu_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def create_app():
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse

    app = FastAPI(title="Mower sensor", lifespan=lifespan)

    # LAN-only use; permissive CORS so the mower UI on the Mac can fetch us.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def index():
        return JSONResponse({
            "service": "mower-sensor",
            "imu_hz": IMU_HZ,
            "buffer_seconds": IMU_BUFFER_SECONDS,
            "snapshots_dir": str(SNAPSHOTS_DIR),
            "endpoints": [
                "GET  /latest.jpg",
                "GET  /api/imu",
                "GET  /api/imu/recent?seconds=10",
                "WS   /api/imu/ws",
            ],
        })

    @app.get("/latest.jpg")
    async def latest_jpg():
        path = SNAPSHOTS_DIR / "latest.jpg"
        if not path.exists():
            return JSONResponse(
                {"error": "no snapshots yet", "snapshots_dir": str(SNAPSHOTS_DIR)},
                status_code=404,
            )
        # Use resolve() so symlinks (latest.jpg -> day-dir/snap_*.jpg) work.
        return FileResponse(path.resolve(), media_type="image/jpeg")

    @app.get("/api/imu")
    async def imu_latest():
        if not state.buffer:
            return JSONResponse({"error": "no samples yet"}, status_code=503)
        return state.buffer[-1]

    @app.get("/api/imu/recent")
    async def imu_recent(seconds: float = 10.0):
        n = max(1, int(seconds * IMU_HZ))
        return list(state.buffer)[-n:]

    @app.websocket("/api/imu/ws")
    async def imu_ws(ws: WebSocket):
        await ws.accept()
        async with state.sub_lock:
            state.subscribers.add(ws)
        try:
            while True:
                # We don't expect client messages; this just keeps the socket alive.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            async with state.sub_lock:
                state.subscribers.discard(ws)

    return app


app = create_app()


def main() -> None:
    import uvicorn
    host = os.environ.get("MOWER_SENSOR_HOST", "0.0.0.0")
    port = int(os.environ.get("MOWER_SENSOR_PORT", "8001"))
    uvicorn.run(app, host=host, port=port, log_level="info", ws="wsproto")


if __name__ == "__main__":
    main()
