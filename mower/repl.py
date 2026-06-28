"""Interactive REPL with a STOP failsafe on every exit path."""

from __future__ import annotations

import atexit
import signal
import sys

from .client import MowerClient, _drain_frames
from .payloads import UART_PAYLOADS

REPL_ALIASES: dict[str, str] = {
    "f": "forward", "b": "reverse", "l": "left", "r": "right",
    "s": "stop", "h": "home", "a": "auto", "i": "initiate_remote",
    "p": "idle_poll", "x": "blade",
    "?": "query_state", "v": "query_version",
}


def repl(ip: str, port: int = 9600) -> None:
    """Interactive shell with persistent TCP and STOP-on-exit failsafe.

    Failsafe layers (all funnel to the same idempotent stop):
      - context manager via MowerClient.__exit__
      - atexit handler
      - SIGINT and SIGTERM handlers
    """
    m = MowerClient(ip, port=port)
    primed = m.connect()
    for p in primed:
        print(f"  <- {p.codename} binary={p.binary.hex() if p.binary else None}")
    stopped = False

    def safe_stop() -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        try:
            m.stop()
        except Exception as e:
            print(f"[failsafe] stop send failed: {e}", file=sys.stderr)
        finally:
            m.close()

    def on_signal(*_: object) -> None:
        print("\n[failsafe] caught signal, sending STOP", file=sys.stderr)
        safe_stop()
        sys.exit(0)

    atexit.register(safe_stop)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    print("connected. commands: forward/reverse/left/right/stop/home/auto/"
          "initiate-remote/blade/poll/state/version")
    print("aliases: f/b/l/r/s/h/a/i/x/p/?/v   "
          "— quit with 'q' or Ctrl-C   — every exit sends STOP")

    while True:
        try:
            line = input("> ").strip().lower()
        except EOFError:
            print()
            break
        if not line:
            continue
        if line in ("q", "quit", "exit"):
            break
        name = REPL_ALIASES.get(line, line.replace("-", "_"))
        if name not in UART_PAYLOADS:
            print(f"  unknown: {line!r}. known: {sorted(UART_PAYLOADS)}")
            continue
        try:
            replies = m.cmd(name)
        except Exception as e:
            print(f"  send failed: {e}")
            break
        for p in replies:
            print(f"  <- {p.codename} binary={p.binary.hex() if p.binary else None}")
