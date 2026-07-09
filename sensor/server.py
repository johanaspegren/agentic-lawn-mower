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
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Runtime config (overridable via env vars)
IMU_HZ = float(os.environ.get("MOWER_IMU_HZ", "25"))
IMU_BUFFER_SECONDS = float(os.environ.get("MOWER_IMU_BUFFER_SECONDS", "60"))
SNAPSHOTS_DIR = Path(os.environ.get("MOWER_SNAPSHOTS_DIR",
                                     PROJECT_ROOT / "snapshots"))
LOG_DIR = Path(os.environ.get("MOWER_SENSOR_LOG_DIR",
                              PROJECT_ROOT / "sensor-logs"))
RETENTION_DAYS = int(os.environ.get("MOWER_SENSOR_RETENTION_DAYS", "14"))
LIVE_VIDEO_SECONDS_DEFAULT = int(os.environ.get("MOWER_LIVE_VIDEO_SECONDS", "120"))
LIVE_VIDEO_FPS_DEFAULT = float(os.environ.get("MOWER_LIVE_VIDEO_FPS", "8"))
LIVE_VIDEO_WIDTH_DEFAULT = int(os.environ.get("MOWER_LIVE_VIDEO_WIDTH", "960"))
LIVE_VIDEO_HEIGHT_DEFAULT = int(os.environ.get("MOWER_LIVE_VIDEO_HEIGHT", "540"))

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


class LiveVideo:
    """On-demand MJPEG capture from rpicam-vid.

    Starts only when explicitly requested by API/UI. Frames are decoded from
    rpicam-vid stdout and exposed via /live.mjpg.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._frame: bytes | None = None
        self._frame_seq = 0
        self._frame_ts: str | None = None
        self._active = False
        self._stop_deadline: float | None = None
        self._started_at: str | None = None
        self._last_error: str | None = None
        self._fps = LIVE_VIDEO_FPS_DEFAULT
        self._width = LIVE_VIDEO_WIDTH_DEFAULT
        self._height = LIVE_VIDEO_HEIGHT_DEFAULT

    async def start(self, *, seconds: float, fps: float,
                    width: int, height: int) -> dict[str, Any]:
        async with self._lock:
            self._active = True
            self._fps = max(1.0, min(30.0, fps))
            self._width = max(320, min(1920, width))
            self._height = max(240, min(1080, height))
            self._stop_deadline = (time.monotonic() + max(1.0, seconds)
                                   if seconds > 0 else None)

            if self._proc is not None and self._proc.returncode is None:
                print("[server][video] already running", file=sys.stderr)
                return self.status()

            cmd = [
                "rpicam-vid",
                "--timeout", "0",
                "--codec", "mjpeg",
                "--width", str(self._width),
                "--height", str(self._height),
                "--framerate", f"{self._fps:g}",
                "--nopreview",
                "-o", "-",
            ]
            print(f"[server][video] starting: {' '.join(cmd)}", file=sys.stderr)
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                self._active = False
                self._last_error = "rpicam-vid not found on this system"
                print(f"[server][video] {self._last_error}", file=sys.stderr)
                return self.status()

            self._started_at = datetime.now().isoformat(timespec="seconds")
            self._last_error = None
            self._reader_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._read_stderr())
            self._watchdog_task = asyncio.create_task(self._watchdog())
            return self.status()

    async def stop(self, *, reason: str = "requested") -> dict[str, Any]:
        async with self._lock:
            self._active = False
            self._stop_deadline = None
            if self._proc is not None and self._proc.returncode is None:
                print(f"[server][video] stopping ({reason})", file=sys.stderr)
                self._proc.terminate()
            proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        return self.status()

    def status(self) -> dict[str, Any]:
        running = self._proc is not None and self._proc.returncode is None
        ttl = None
        if self._stop_deadline is not None:
            ttl = max(0.0, self._stop_deadline - time.monotonic())
        return {
            "active": self._active,
            "running": running,
            "started_at": self._started_at,
            "frame_ts": self._frame_ts,
            "frames": self._frame_seq,
            "seconds_remaining": ttl,
            "fps": self._fps,
            "width": self._width,
            "height": self._height,
            "last_error": self._last_error,
        }

    def latest_frame(self) -> tuple[int, bytes | None]:
        return self._frame_seq, self._frame

    async def shutdown(self) -> None:
        await self.stop(reason="shutdown")
        for task in (self._reader_task, self._stderr_task, self._watchdog_task):
            if task is not None and not task.done():
                task.cancel()

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            if not self._active:
                return
            if self._stop_deadline is not None and time.monotonic() >= self._stop_deadline:
                await self.stop(reason="timeout")
                return
            if self._proc is not None and self._proc.returncode is not None:
                self._active = False
                if self._proc.returncode != 0 and self._last_error is None:
                    self._last_error = f"rpicam-vid exited ({self._proc.returncode})"
                return

    async def _read_stdout(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        buf = bytearray()
        try:
            while True:
                chunk = await self._proc.stdout.read(32768)
                if not chunk:
                    break
                buf.extend(chunk)
                while True:
                    soi = buf.find(b"\xff\xd8")
                    if soi < 0:
                        if len(buf) > 2:
                            del buf[:-2]
                        break
                    eoi = buf.find(b"\xff\xd9", soi + 2)
                    if eoi < 0:
                        if soi > 0:
                            del buf[:soi]
                        break
                    frame = bytes(buf[soi:eoi + 2])
                    del buf[:eoi + 2]
                    self._frame = frame
                    self._frame_seq += 1
                    self._frame_ts = datetime.now().isoformat(timespec="milliseconds")
        finally:
            if self._proc is not None:
                await self._proc.wait()

    async def _read_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            print(f"[server][video] {text}", file=sys.stderr)
            low = text.lower()
            if "failed" in low or "error" in low or "busy" in low:
                self._last_error = text


live_video = LiveVideo()


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
        await live_video.shutdown()


def create_app():
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

    app = FastAPI(title="Mower sensor", lifespan=lifespan)

    # LAN-only use; permissive CORS so the mower UI on the Mac can fetch us.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def index():
        return JSONResponse({
            "service": "mower-sensor",
            "imu_hz": IMU_HZ,
            "buffer_seconds": IMU_BUFFER_SECONDS,
            "snapshots_dir": str(SNAPSHOTS_DIR),
            "live_video": live_video.status(),
            "endpoints": [
                "GET  /latest.jpg",
                "POST /api/camera/live/start",
                "POST /api/camera/live/stop",
                "GET  /api/camera/live/status",
                "GET  /live.mjpg",
                "GET  /api/imu",
                "GET  /api/imu/recent?seconds=10",
                "WS   /api/imu/ws",
            ],
        })

    @app.get("/latest.jpg")
    async def latest_jpg():
        path = SNAPSHOTS_DIR / "latest.jpg"
        if not path.exists():
            print(f"[server] latest.jpg missing at {path}", file=sys.stderr)
            return JSONResponse(
                {"error": "no snapshots yet", "snapshots_dir": str(SNAPSHOTS_DIR)},
                status_code=404,
            )
        # Use resolve() so symlinks (latest.jpg -> day-dir/snap_*.jpg) work.
        resolved = path.resolve()
        print(f"[server] serving latest.jpg from {resolved}", file=sys.stderr)
        return FileResponse(resolved, media_type="image/jpeg")

    @app.post("/api/camera/live/start")
    async def camera_live_start(seconds: float = LIVE_VIDEO_SECONDS_DEFAULT,
                                fps: float = LIVE_VIDEO_FPS_DEFAULT,
                                width: int = LIVE_VIDEO_WIDTH_DEFAULT,
                                height: int = LIVE_VIDEO_HEIGHT_DEFAULT):
        status = await live_video.start(seconds=seconds, fps=fps,
                                        width=width, height=height)
        return JSONResponse(status)

    @app.post("/api/camera/live/stop")
    async def camera_live_stop():
        status = await live_video.stop(reason="api stop")
        return JSONResponse(status)

    @app.get("/api/camera/live/status")
    async def camera_live_status():
        return JSONResponse(live_video.status())

    @app.get("/live.mjpg")
    async def live_mjpg():
        # Caller should start the live session first via /api/camera/live/start.
        if not live_video.status().get("running"):
            return JSONResponse(
                {"error": "live video not running; call /api/camera/live/start"},
                status_code=409,
            )

        async def stream():
            boundary = b"--frame"
            last_seq = -1
            while True:
                if not live_video.status().get("running"):
                    return
                seq, frame = live_video.latest_frame()
                if frame is None or seq == last_seq:
                    await asyncio.sleep(0.03)
                    continue
                last_seq = seq
                yield (boundary + b"\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Cache-Control: no-store\r\n"
                       b"Pragma: no-cache\r\n"
                       b"\r\n" + frame + b"\r\n")

        return StreamingResponse(
            stream(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

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
    print(f"[server] snapshots_dir={SNAPSHOTS_DIR}", file=sys.stderr)
    print("[server] live video defaults: "
          f"seconds={LIVE_VIDEO_SECONDS_DEFAULT}, "
          f"fps={LIVE_VIDEO_FPS_DEFAULT:g}, "
          f"size={LIVE_VIDEO_WIDTH_DEFAULT}x{LIVE_VIDEO_HEIGHT_DEFAULT}",
          file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info", ws="wsproto")


if __name__ == "__main__":
    main()
