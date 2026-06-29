"""Client API for the mower.

The main entry point is :class:`MowerClient`. It owns the TCP socket and
exposes one method per known command:

    >>> from mower import MowerClient
    >>> with MowerClient("192.168.68.108") as m:
    ...     m.initiate_remote()
    ...     m.forward()
    ...     time.sleep(2)
    ...     m.stop()

`with` activates the failsafe: a `stop` is sent on any exit path
(normal close, exception, ``Ctrl-C`` if you installed signal handlers).

For one-shot scripts the module-level :func:`send_command` is fine too.
"""

from __future__ import annotations

import socket
import struct
import sys
from datetime import datetime
from typing import Any, Iterable

from .codec import Packet, decode
from .payloads import UART_PAYLOADS, remote_cmd, set_time_payload, wrap_uart

DEFAULT_PORT = 9600


def _drain_frames(sock: socket.socket) -> list[Packet]:
    """Read whole frames from sock until it idles. Decode-failures are logged."""
    out: list[Packet] = []
    buf = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        while len(buf) >= 4:
            total = struct.unpack(">H", buf[2:4])[0]
            if len(buf) < total:
                break
            try:
                out.append(decode(buf[:total]))
            except Exception as e:
                print(f"[drain] decode failed: {e}", file=sys.stderr)
            buf = buf[total:]
    return out


def send_tcp(ip: str, pkt: bytes, port: int = DEFAULT_PORT,
             read_timeout: float = 2.0) -> bytes:
    """Open TCP, send one packet, read until idle, return raw response bytes."""
    with socket.create_connection((ip, port), timeout=5) as s:
        s.sendall(pkt)
        s.settimeout(read_timeout)
        buf = b""
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(buf) >= 4:
                    total = struct.unpack(">H", buf[2:4])[0]
                    if len(buf) >= total:
                        break
        except socket.timeout:
            pass
    return buf


class MowerClient:
    """Persistent-socket client for the Lyfco / EGROBOT M10 protocol.

    Use as a context manager when possible — that activates the failsafe
    that sends ``stop`` on exit. Without `with`, call :meth:`close` yourself.
    """

    def __init__(self, ip: str, port: int = DEFAULT_PORT, *,
                 connect_timeout: float = 5.0, read_timeout: float = 2.0,
                 prime: bool = True):
        self.ip = ip
        self.port = port
        self.read_timeout = read_timeout
        self.connect_timeout = connect_timeout
        self.prime = prime
        self._sock: socket.socket | None = None

    # --- lifecycle ----------------------------------------------------------

    def __enter__(self) -> "MowerClient":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self.stop()
        finally:
            self.close()

    def connect(self) -> list[Packet]:
        """Open the TCP socket and (by default) send an idle poll to prime it.

        Returns any frames received during priming.
        """
        if self._sock is not None:
            return []
        self._sock = socket.create_connection((self.ip, self.port),
                                              timeout=self.connect_timeout)
        self._sock.settimeout(self.read_timeout)
        if self.prime:
            self._send_payload(UART_PAYLOADS["idle_poll"], tag_char="x")
            return _drain_frames(self._sock)
        return []

    def close(self) -> None:
        """Close the socket. Idempotent."""
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # --- core sending -------------------------------------------------------

    def _send_payload(self, payload: bytes, *, tag_char: str = "y") -> None:
        if self._sock is None:
            raise RuntimeError("MowerClient is not connected")
        self._sock.sendall(wrap_uart(payload, tag_char=tag_char))

    def send_raw(self, payload: bytes, *, tag_char: str = "y",
                 linger: float | None = None) -> list[Packet]:
        """Send an arbitrary UART payload and return drained reply frames."""
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        self._send_payload(payload, tag_char=tag_char)
        if linger is not None:
            self._sock.settimeout(linger)
        return _drain_frames(self._sock)

    # Tag-char convention used by the EGROBOT app (confirmed via PCAPDroid
    # captures of every command type). The tag char encodes the command
    # category; the mower silently drops requests sent with the wrong tag.
    #   x  = queries (idle_poll, query_state, query_version)
    #   y  = 0x69-family control commands (forward, stop, home, blade, ...)
    #   g  = set-time (handled separately via set_time())
    _QUERY_NAMES = frozenset({"idle_poll", "query_state", "query_version"})

    def cmd(self, name: str, *, linger: float | None = None) -> list[Packet]:
        """Send a named UART command from `UART_PAYLOADS`."""
        payload = UART_PAYLOADS[name]
        tag = "x" if name in self._QUERY_NAMES else "y"
        return self.send_raw(payload, tag_char=tag, linger=linger)

    # --- convenience: control commands -------------------------------------

    def forward(self):         return self.cmd("forward")
    def reverse(self):         return self.cmd("reverse")
    def left(self):            return self.cmd("left")
    def right(self):           return self.cmd("right")
    def auto(self):            return self.cmd("auto")
    def home(self):            return self.cmd("home")
    def blade(self):           return self.cmd("blade")
    def initiate_remote(self): return self.cmd("initiate_remote")
    def stop(self):            return self.cmd("stop")

    # --- convenience: queries ----------------------------------------------

    def poll(self):            return self.cmd("idle_poll")
    def state(self):           return self.cmd("query_state")
    def version(self):         return self.cmd("query_version")

    # --- richer commands ----------------------------------------------------

    def remote(self, param: int) -> list[Packet]:
        """Send an arbitrary 0x69-family command by param byte."""
        return self.send_raw(remote_cmd(param))

    def set_time(self, dt: datetime | None = None) -> list[Packet]:
        """Set the mower's date and time. Defaults to local now()."""
        dt = dt or datetime.now()
        return self.send_raw(set_time_payload(dt), tag_char="g", linger=1.0)


# --- module-level one-shot helpers ------------------------------------------


def send_command(ip: str, name: str, port: int = DEFAULT_PORT,
                 linger: float = 3.0) -> list[Packet]:
    """One-shot helper: open, prime, send, drain, close. Returns reply frames.

    Does NOT use the context-manager failsafe (which would send `stop`) — that
    would defeat the purpose of one-shotting `forward`, `home`, etc.
    """
    m = MowerClient(ip, port=port)
    m.connect()
    try:
        return m.cmd(name, linger=linger)
    finally:
        m.close()


def set_time(ip: str, dt: datetime | None = None,
             port: int = DEFAULT_PORT) -> list[Packet]:
    """One-shot helper to set mower date+time."""
    m = MowerClient(ip, port=port)
    m.connect()
    try:
        return m.set_time(dt)
    finally:
        m.close()


def poll(ip: str, port: int = DEFAULT_PORT) -> Packet | None:
    """One-shot idle poll. Returns the single reply packet or None."""
    m = MowerClient(ip, port=port, prime=False)
    m.connect()
    try:
        replies = m.cmd("idle_poll")
        return replies[0] if replies else None
    finally:
        m.close()
