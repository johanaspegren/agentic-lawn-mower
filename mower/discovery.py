"""UDP discovery — broadcast `Search`, collect `SearchAck` replies."""

from __future__ import annotations

import socket
import sys

from .codec import Packet, decode, encode

DEFAULT_PORT = 9600


def discover(timeout: float = 2.0, broadcast: str = "255.255.255.255",
             port: int = DEFAULT_PORT, *, verbose: bool = True) -> list[Packet]:
    """Broadcast a `Search` packet on UDP, listen `timeout` seconds for replies.

    NOTE: replies may need a `port=<n>` field in the request to come back to
    us — see the README. For now this matches the firmware's free-form Search.
    """
    pkt = encode("Search", tag_char="+", terminator=False)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    s.bind(("", 0))
    s.sendto(pkt, (broadcast, port))
    if verbose:
        print(f"[discover] sent {len(pkt)} bytes to {broadcast}:{port}",
              file=sys.stderr)
    results: list[Packet] = []
    while True:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            break
        try:
            p = decode(data)
            if verbose:
                print(f"[discover] {addr[0]} -> {p}", file=sys.stderr)
            results.append(p)
        except Exception as e:
            if verbose:
                print(f"[discover] bad reply from {addr}: {e}", file=sys.stderr)
    return results
