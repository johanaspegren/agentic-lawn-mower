"""Lyfco / EGROBOT M10 (E1800) local protocol client.

High-level API:

    >>> from mower import MowerClient
    >>> with MowerClient("192.168.68.108") as m:
    ...     m.initiate_remote()
    ...     m.forward()
    ...     time.sleep(2)
    ...     # exiting the context manager always sends STOP

One-shot helpers:

    >>> from mower import send_command, set_time, poll, discover
    >>> send_command("192.168.68.108", "home")

Low-level codec:

    >>> from mower import Packet, encode, decode
"""

from .client import (
    DEFAULT_PORT,
    MowerClient,
    poll,
    send_command,
    send_tcp,
    set_time,
)
from .codec import Packet, decode, encode, parse_hex_dump
from .discovery import discover
from .payloads import (
    UART_PAYLOADS,
    decode_state,
    remote_cmd,
    set_time_payload,
    wrap_uart,
)
from .repl import REPL_ALIASES, repl

__all__ = [
    "DEFAULT_PORT",
    "MowerClient",
    "Packet",
    "REPL_ALIASES",
    "UART_PAYLOADS",
    "decode",
    "decode_state",
    "discover",
    "encode",
    "parse_hex_dump",
    "poll",
    "remote_cmd",
    "repl",
    "send_command",
    "send_tcp",
    "set_time",
    "set_time_payload",
    "wrap_uart",
]
