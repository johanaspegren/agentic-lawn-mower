"""FastAPI server for the mower control UI."""

from __future__ import annotations

import asyncio
import csv
import gzip
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..client import MowerClient
from ..payloads import UART_PAYLOADS

STATIC_DIR = Path(__file__).parent / "static"


# --- shared mower session ---------------------------------------------------


@dataclass
class TelemetrySample:
    """One snapshot pushed to WebSocket subscribers."""
    ts: str
    codename: str
    binary_hex: str | None
    fields: dict[str, str] = field(default_factory=dict)


class SharedClient:
    """Single MowerClient owned by the server; serialised by an asyncio lock.

    The mower accepts one TCP connection at a time. We hold one persistent
    socket for the server's lifetime so polling and commands share it.
    """

    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port
        self._client: MowerClient | None = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self) -> MowerClient:
        if self._client is None:
            self._client = MowerClient(self.ip, port=self.port, prime=False)
            await asyncio.to_thread(self._client.connect)
        return self._client

    async def _reconnect(self) -> None:
        if self._client is not None:
            try:
                await asyncio.to_thread(self._client.close)
            except Exception:
                pass
            self._client = None

    async def cmd(self, name: str, *, linger: float | None = None) -> list[dict]:
        async with self._lock:
            try:
                c = await self._ensure_connected()
                replies = await asyncio.to_thread(c.cmd, name, linger=linger)
            except (OSError, RuntimeError):
                await self._reconnect()
                c = await self._ensure_connected()
                replies = await asyncio.to_thread(c.cmd, name, linger=linger)
            return [_packet_to_dict(p) for p in replies]

    async def set_time(self, dt: datetime | None) -> list[dict]:
        async with self._lock:
            c = await self._ensure_connected()
            replies = await asyncio.to_thread(c.set_time, dt)
            return [_packet_to_dict(p) for p in replies]

    async def remote(self, param: int) -> list[dict]:
        from ..payloads import remote_cmd
        payload = remote_cmd(param)
        async with self._lock:
            c = await self._ensure_connected()
            replies = await asyncio.to_thread(c.send_raw, payload)
            return [_packet_to_dict(p) for p in replies]

    async def shutdown(self) -> None:
        """Send STOP and close the socket. Best-effort, never raises."""
        async with self._lock:
            if self._client is None:
                return
            try:
                await asyncio.to_thread(self._client.stop)
            except Exception as e:
                print(f"[shutdown] stop failed: {e}", file=sys.stderr)
            try:
                await asyncio.to_thread(self._client.close)
            except Exception:
                pass
            self._client = None


def _packet_to_dict(p) -> dict:
    return {
        "codename": p.codename,
        "fields": p.fields,
        "binary_hex": p.binary.hex() if p.binary is not None else None,
        "tag": p.tag,
    }


# --- WebSocket fan-out ------------------------------------------------------


class TelemetryHub:
    """Broadcasts telemetry samples to all connected WebSocket clients."""

    def __init__(self):
        self._subs: set = set()
        self._lock = asyncio.Lock()
        self.last_sample: dict | None = None

    async def subscribe(self, ws) -> None:
        async with self._lock:
            self._subs.add(ws)
        if self.last_sample is not None:
            try:
                await ws.send_json(self.last_sample)
            except Exception:
                pass

    async def unsubscribe(self, ws) -> None:
        async with self._lock:
            self._subs.discard(ws)

    async def publish(self, sample: dict) -> None:
        self.last_sample = sample
        async with self._lock:
            subs = list(self._subs)
        for ws in subs:
            try:
                await ws.send_json(sample)
            except Exception:
                await self.unsubscribe(ws)


# --- log reading ------------------------------------------------------------


def read_log_rows(log_dir: Path, channel: str, hours: int = 24) -> list[dict]:
    """Read recent CSV rows for a channel from the rotating log dir.

    Walks `<channel>-*.csv[.gz]` files newest-first, parses rows, stops
    after `hours` worth (rough cutoff — we read whole files).
    """
    if not log_dir.exists():
        return []
    files = sorted(
        list(log_dir.glob(f"{channel}-*.csv")) +
        list(log_dir.glob(f"{channel}-*.csv.gz")),
        reverse=True,
    )
    # Limit roughly to the requested hours.
    files = files[:max(1, hours + 2)]
    rows: list[dict] = []
    for f in files:
        opener = gzip.open if f.suffix == ".gz" else open
        try:
            with opener(f, "rt", newline="") as fh:
                rows.extend(csv.DictReader(fh))
        except OSError:
            continue
    return rows


# --- background poller ------------------------------------------------------


async def _poll_loop(shared: SharedClient, hub: TelemetryHub,
                     interval: float, poll_state: bool) -> None:
    """Periodically poll the mower and broadcast each reply."""
    while True:
        ts = datetime.now()
        try:
            replies = await shared.cmd("idle_poll", linger=0.5)
            if poll_state:
                replies += await shared.cmd("query_state", linger=1.0)
            for r in replies:
                await hub.publish({"ts": ts.isoformat(timespec="seconds"), **r})
        except Exception as e:
            await hub.publish({
                "ts": ts.isoformat(timespec="seconds"),
                "codename": "ERROR",
                "fields": {"error": str(e)},
                "binary_hex": None,
                "tag": "",
            })
        await asyncio.sleep(interval)


# --- app factory ------------------------------------------------------------


def create_app(ip: str, port: int = 9600, *,
               log_dir: str | None = None,
               poll_interval: float = 5.0,
               poll_state: bool = True):
    """Build and return a configured FastAPI app."""
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    shared = SharedClient(ip, port)
    hub = TelemetryHub()
    log_root = Path(log_dir) if log_dir else None
    poll_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal poll_task
        poll_task = asyncio.create_task(
            _poll_loop(shared, hub, poll_interval, poll_state)
        )
        try:
            yield
        finally:
            if poll_task is not None:
                poll_task.cancel()
                try:
                    await poll_task
                except (asyncio.CancelledError, Exception):
                    pass
            await shared.shutdown()

    app = FastAPI(title="Mower control", lifespan=lifespan)

    @app.get("/api/commands")
    async def list_commands() -> dict[str, list[str]]:
        return {"commands": sorted(UART_PAYLOADS.keys())}

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        out = {
            "ip": ip,
            "last_sample": hub.last_sample,
        }
        return out

    @app.post("/api/cmd/{name}")
    async def cmd(name: str) -> dict[str, Any]:
        if name not in UART_PAYLOADS:
            raise HTTPException(404, f"unknown command: {name}")
        replies = await shared.cmd(name)
        return {"replies": replies}

    @app.post("/api/param/{value}")
    async def param(value: str) -> dict[str, Any]:
        try:
            n = int(value, 0)
        except ValueError as e:
            raise HTTPException(400, f"bad param byte: {e}")
        replies = await shared.remote(n)
        return {"replies": replies}

    @app.post("/api/set-time")
    async def set_time(body: dict | None = None) -> dict[str, Any]:
        dt = None
        if body and "datetime" in body:
            try:
                dt = datetime.fromisoformat(body["datetime"])
            except ValueError as e:
                raise HTTPException(400, f"bad datetime: {e}")
        replies = await shared.set_time(dt)
        return {"replies": replies}

    @app.get("/api/logs")
    async def logs(channel: str = "mower", hours: int = 6) -> dict[str, Any]:
        if log_root is None:
            return {"rows": [], "note": "no --log-dir configured"}
        rows = read_log_rows(log_root, channel, hours=hours)
        return {"rows": rows}

    @app.websocket("/api/telemetry")
    async def telemetry_ws(ws: WebSocket) -> None:
        await ws.accept()
        await hub.subscribe(ws)
        try:
            while True:
                # We don't expect client messages; just keep the socket alive.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.unsubscribe(ws)

    # Serve static frontend from /static, with / serving the index.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


def run(ip: str, *, host: str = "127.0.0.1", port: int = 8000,
        mower_port: int = 9600,
        log_dir: str | None = None,
        poll_interval: float = 5.0,
        poll_state: bool = True) -> None:
    """Start the server. Convenience wrapper around uvicorn.run."""
    import uvicorn
    app = create_app(ip, port=mower_port, log_dir=log_dir,
                     poll_interval=poll_interval, poll_state=poll_state)
    print(f"[serve] mower @ {ip}:{mower_port}", file=sys.stderr)
    print(f"[serve] UI at http://{host}:{port}", file=sys.stderr)
    if log_dir:
        print(f"[serve] reading logs from {log_dir}", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")
