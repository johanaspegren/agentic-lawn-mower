"""Lyfco / EGROBOT M10 (E1800) local protocol client.

Frame format (reverse-engineered from PCAPDroid captures):

  +---------+----------+----------+
  | "0h"    | length   | 4x 0x00  |   8-byte header
  +---------+----------+----------+
  | 'U' (0x55) sync                |
  | ASCII tag: "00<c>00010000"     |   <c> appears to be a type/checksum byte
  | body...                        |   key=value pairs, XOR 0x30 over text
  | "=:" (0x3d 0x3a) terminator    |
  +--------------------------------+

In the body:
  - All ASCII bytes are XORed with 0x30 (so '=' 0x3d -> 0x0d, '&' 0x26 -> 0x16).
  - Binary blobs live inside `UseBinaryData=##<raw bytes>` and are NOT XORed.
"""

from __future__ import annotations

import argparse
import atexit
import signal
import socket
import struct
import sys
from dataclasses import dataclass

MAGIC = b"0h"
SYNC = 0x55
SEP = 0x16          # '&' XOR 0x30
EQ = 0x0D           # '=' XOR 0x30
TERMINATOR = b"\x3d\x3a"  # "=:" — plaintext, not XORed
BIN_PREFIX = b"\x13\x13"  # "##" XOR 0x30
BIN_FIELD = "UserBinaryData"
DEFAULT_PORT = 9600


def _xor(data: bytes) -> bytes:
    return bytes(b ^ 0x30 for b in data)


@dataclass
class Packet:
    tag: str                       # 12-byte ASCII tag, e.g. "U00x00010000"
    codename: str
    fields: dict[str, str]         # key -> de-XORed value (text)
    binary: bytes | None = None    # contents of UserBinaryData, if present

    def __repr__(self) -> str:
        b = self.binary.hex() if self.binary is not None else None
        return (f"Packet(tag={self.tag!r}, codename={self.codename!r}, "
                f"fields={self.fields}, binary={b})")


def encode(codename: str, fields: dict[str, str] | None = None,
           binary: bytes | None = None, tag_char: str = "x",
           terminator: bool = True) -> bytes:
    """Build a packet ready to send. Field values are XORed automatically."""
    body = bytearray([SYNC])
    body += f"00{tag_char}00010000".encode()
    body += _xor(b"CodeName") + bytes([EQ]) + _xor(codename.encode())
    for k, v in (fields or {}).items():
        body.append(SEP)
        body += _xor(k.encode()) + bytes([EQ]) + _xor(v.encode())
    if binary is not None:
        body.append(SEP)
        body += _xor(BIN_FIELD.encode()) + bytes([EQ]) + BIN_PREFIX + binary
    if terminator:
        body += TERMINATOR
    total = len(body) + 8
    return bytes(MAGIC + struct.pack(">H", total) + b"\x00\x00\x00\x00" + body)


def decode(buf: bytes) -> Packet:
    if buf[:2] != MAGIC:
        raise ValueError(f"bad magic: {buf[:2]!r}")
    total = struct.unpack(">H", buf[2:4])[0]
    body = buf[8:total] if len(buf) >= total else buf[8:]
    if body.endswith(TERMINATOR):
        body = body[:-2]
    if not body or body[0] != SYNC:
        raise ValueError(f"bad sync: {body[:1]!r}")
    tag = body[:12].decode("latin1", errors="replace")
    rest = body[12:]

    fields: dict[str, str] = {}
    binary: bytes | None = None
    codename = ""
    i = 0
    while i < len(rest):
        try:
            j = rest.index(EQ, i)
        except ValueError:
            break
        key = _xor(rest[i:j]).decode("latin1", errors="replace")
        i = j + 1
        if key == BIN_FIELD:
            if rest[i:i + 2] == BIN_PREFIX:
                i += 2
            binary = bytes(rest[i:])
            break
        try:
            k = rest.index(SEP, i)
        except ValueError:
            k = len(rest)
        value = _xor(rest[i:k]).decode("latin1", errors="replace")
        if key == "CodeName":
            codename = value
        else:
            fields[key] = value
        i = k + 1
    return Packet(tag=tag, codename=codename, fields=fields, binary=binary)


def discover(timeout: float = 2.0, broadcast: str = "255.255.255.255",
             port: int = DEFAULT_PORT) -> list[Packet]:
    """UDP-broadcast a Search request and collect SearchAck replies."""
    pkt = encode("Search", tag_char="+", terminator=False)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    s.bind(("", 0))
    s.sendto(pkt, (broadcast, port))
    print(f"[discover] sent {len(pkt)} bytes to {broadcast}:{port}", file=sys.stderr)
    results: list[Packet] = []
    while True:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            break
        try:
            p = decode(data)
            print(f"[discover] {addr[0]} -> {p}", file=sys.stderr)
            results.append(p)
        except Exception as e:
            print(f"[discover] bad reply from {addr}: {e}", file=sys.stderr)
    return results


# Captured UART payloads from PCAPDroid (the inner binary blob inside
# UserBinaryData=##...). Add more entries here as we capture them.
def set_time_payload(dt) -> bytes:
    """Build the 22-byte UART payload that sets the mower's date and time.

    Format (after XOR-0x30 the bytes spell ASCII):
        "22" "T" YYYY MM DD W HH MM SS "FC" CC

    where W is the ISO weekday (Mon=1..Sun=7), SS is sent as "00" (the picker
    has no seconds field), and CC is a 1-byte checksum hex-encoded such that
    the sum of the 15 digit values at positions 3..17 plus CC equals 0x32.
    """
    s = (
        f"22T"
        f"{dt.year:04d}{dt.month:02d}{dt.day:02d}"
        f"{dt.isoweekday()}"
        f"{dt.hour:02d}{dt.minute:02d}00"
    )
    digit_sum = sum(int(c) for c in s[3:18])  # positions 3..17 are all digits
    checksum = (0x32 - digit_sum) & 0xFF
    s += "FC" + f"{checksum:02X}"
    if len(s) != 22:
        raise AssertionError(f"set_time payload wrong length: {len(s)}")
    return bytes(b ^ 0x30 for b in s.encode())


def remote_cmd(param: int) -> bytes:
    """Build a 0x69-family remote-control UART payload for the given param byte.

    Frame: 00 08 69 <param> 76 75 73 <checksum>, where the 8 bytes sum to 0x1D8
    (equivalently, checksum = 0x09 - param mod 256).
    """
    if not 0 <= param <= 0xFF:
        raise ValueError("param must be a byte")
    checksum = (0x09 - param) & 0xFF
    return bytes([0x00, 0x08, 0x69, param, 0x76, 0x75, 0x73, checksum])


UART_PAYLOADS = {
    "idle_poll":       bytes.fromhex("00077f76760004"),
    "query_state":     bytes.fromhex("00076776757673"),  # mower state query (32-byte reply)
    "query_version":   bytes.fromhex("00076676757674"),  # software version (no reply — broken in firmware)
    "stop":            remote_cmd(0x00),
    "forward":         remote_cmd(0x01),
    "reverse":         remote_cmd(0x02),
    "left":            remote_cmd(0x03),
    "right":           remote_cmd(0x04),
    "auto":            remote_cmd(0x05),
    "initiate_remote": remote_cmd(0x06),
    "home":            remote_cmd(0x07),
    "blade":           remote_cmd(0x08),
}


def get_uart_data(payload: bytes, chn: str = "0", tag_char: str = "y") -> bytes:
    """Build a GetUartData packet wrapping the given UART payload."""
    return encode(
        "GetUartData",
        fields={"Chn": chn, "Len": str(len(payload) + 4)},
        binary=payload,
        tag_char=tag_char,
    )


def poll(ip: str, port: int = DEFAULT_PORT) -> Packet | None:
    """Send one idle GetUartData poll and return the mower's reply."""
    pkt = get_uart_data(UART_PAYLOADS["idle_poll"], tag_char="x")
    resp = send_tcp(ip, pkt, port=port)
    return decode(resp) if resp else None


def set_time(ip: str, dt=None, port: int = DEFAULT_PORT) -> list[Packet]:
    """Set the mower's date and time. Defaults to local now()."""
    import datetime
    dt = dt or datetime.datetime.now()
    payload = set_time_payload(dt)
    idle = get_uart_data(UART_PAYLOADS["idle_poll"], tag_char="x")
    cmd = get_uart_data(payload, tag_char="g")
    replies: list[Packet] = []
    with socket.create_connection((ip, port), timeout=5) as s:
        s.settimeout(2.0)
        s.sendall(idle)
        replies += _drain_frames(s)
        s.sendall(cmd)
        s.settimeout(1.0)
        replies += _drain_frames(s)
    return replies


def send_command(ip: str, payload_name: str, port: int = DEFAULT_PORT,
                 linger: float = 3.0) -> list[Packet]:
    """Open TCP, prime with an idle poll (like the app does), then send the
    named UART command. Return every packet the mower sends back."""
    idle = get_uart_data(UART_PAYLOADS["idle_poll"], tag_char="x")
    cmd = get_uart_data(UART_PAYLOADS[payload_name], tag_char="y")
    replies: list[Packet] = []
    with socket.create_connection((ip, port), timeout=5) as s:
        s.settimeout(2.0)
        s.sendall(idle)
        replies += _drain_frames(s)
        s.sendall(cmd)
        s.settimeout(linger)
        replies += _drain_frames(s)
    return replies


def initiate_remote(ip: str, **kw) -> list[Packet]:
    return send_command(ip, "initiate_remote", **kw)


def forward(ip: str, **kw) -> list[Packet]:
    return send_command(ip, "forward", **kw)


def stop(ip: str, **kw) -> list[Packet]:
    return send_command(ip, "stop", **kw)


def _drain_frames(s: socket.socket) -> list[Packet]:
    """Read whole frames from socket until it idles, returning decoded packets."""
    out: list[Packet] = []
    buf = b""
    while True:
        try:
            chunk = s.recv(4096)
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
    """Open TCP, send one packet, read until socket idle, return raw response."""
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
                # if we have a complete frame, stop
                if len(buf) >= 4:
                    total = struct.unpack(">H", buf[2:4])[0]
                    if len(buf) >= total:
                        break
        except socket.timeout:
            pass
    return buf


REPL_ALIASES = {
    "f": "forward", "b": "reverse", "l": "left", "r": "right",
    "s": "stop", "h": "home", "a": "auto", "i": "initiate_remote",
    "p": "idle_poll", "x": "blade",
    "?": "query_state", "v": "query_version",
}


def repl(ip: str, port: int = DEFAULT_PORT) -> None:
    """Interactive shell over a single persistent TCP socket.

    Always sends 'stop' on exit (atexit + SIGINT handler) so the mower never
    keeps driving if the script crashes or you Ctrl-C.
    """
    s = socket.create_connection((ip, port), timeout=5)
    s.settimeout(0.2)
    closed = False

    def send(name: str) -> None:
        payload = UART_PAYLOADS[name]
        s.sendall(get_uart_data(payload, tag_char="y" if name != "idle_poll" else "x"))

    def safe_stop() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            send("stop")
            s.settimeout(0.3)
            try:
                s.recv(4096)
            except Exception:
                pass
        except Exception as e:
            print(f"[failsafe] stop send failed: {e}", file=sys.stderr)
        finally:
            try:
                s.close()
            except Exception:
                pass

    def on_signal(*_):
        print("\n[failsafe] caught signal, sending STOP", file=sys.stderr)
        safe_stop()
        sys.exit(0)

    atexit.register(safe_stop)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    print("connected. commands: forward/reverse/left/right/stop/home/auto/initiate-remote/poll")
    print("aliases: f/b/l/r/s/h/a/i/p   — quit with 'q' or Ctrl-C   — every exit sends STOP")
    # prime the channel like the app does
    send("idle_poll")
    for p in _drain_frames(s):
        print(f"  <- {p.codename} binary={p.binary.hex() if p.binary else None}")

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
            send(name)
        except Exception as e:
            print(f"  send failed: {e}")
            break
        # drain any reply that arrived, but don't block
        for p in _drain_frames(s):
            print(f"  <- {p.codename} binary={p.binary.hex() if p.binary else None}")


# --- CLI ---------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Lyfco M10 local protocol tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="UDP broadcast Search, print replies")

    p_decode = sub.add_parser("decode", help="Decode a hex string")
    p_decode.add_argument("hex", help="hex bytes (whitespace ok)")

    p_raw = sub.add_parser("raw", help="Send raw hex bytes over TCP")
    p_raw.add_argument("ip")
    p_raw.add_argument("hex")

    p_replay = sub.add_parser("replay", help="Replay a captured file's hex dump")
    p_replay.add_argument("ip")
    p_replay.add_argument("path", help="path to a PCAPDroid hex-dump txt")

    p_poll = sub.add_parser("poll", help="Send one idle GetUartData poll")
    p_poll.add_argument("ip")

    p_remote = sub.add_parser("initiate-remote",
                              help="Send the 'initiate remote mode' command")
    p_remote.add_argument("ip")

    p_fwd = sub.add_parser("forward", help="Drive forward (param 0x01)")
    p_fwd.add_argument("ip")

    p_rev = sub.add_parser("reverse", help="Drive reverse (param 0x02)")
    p_rev.add_argument("ip")

    p_left = sub.add_parser("left", help="Turn left (param 0x03)")
    p_left.add_argument("ip")

    p_right = sub.add_parser("right", help="Turn right (param 0x04)")
    p_right.add_argument("ip")

    p_auto = sub.add_parser("auto", help="Start autonomous mowing (param 0x05)")
    p_auto.add_argument("ip")

    p_home = sub.add_parser("home", help="Return to dock (param 0x07)")
    p_home.add_argument("ip")

    p_blade = sub.add_parser("blade", help="Toggle blade engine on/off (param 0x08)")
    p_blade.add_argument("ip")

    p_state = sub.add_parser("state",
                             help="Query mower state (32-byte response; format not fully decoded)")
    p_state.add_argument("ip")

    p_ver = sub.add_parser("version",
                           help="Query software version (no reply — opcode is dead in firmware)")
    p_ver.add_argument("ip")

    p_settime = sub.add_parser("set-time",
                               help="Set mower date+time (default: local now)")
    p_settime.add_argument("ip")
    p_settime.add_argument("--datetime", "-d",
                           help="ISO datetime, e.g. 2026-06-29T23:00. Default: now")

    p_stop = sub.add_parser("stop", help="Stop motion (param 0x00)")
    p_stop.add_argument("ip")

    p_param = sub.add_parser("param", help="Send a synthesized 0x69 command by param byte")
    p_param.add_argument("ip")
    p_param.add_argument("param", help="param byte, e.g. 0x03 or 3")

    p_repl = sub.add_parser("repl", help="Interactive shell (always STOPs on exit)")
    p_repl.add_argument("ip")

    args = ap.parse_args()

    if args.cmd == "discover":
        replies = discover()
        if not replies:
            print("no replies", file=sys.stderr)
            sys.exit(1)
        for r in replies:
            print(r)

    elif args.cmd == "decode":
        data = bytes.fromhex("".join(args.hex.split()))
        print(decode(data))

    elif args.cmd == "raw":
        data = bytes.fromhex("".join(args.hex.split()))
        resp = send_tcp(args.ip, data)
        print(f"received {len(resp)} bytes: {resp.hex()}")
        if resp:
            try:
                print(decode(resp))
            except Exception as e:
                print(f"(decode failed: {e})")

    elif args.cmd == "poll":
        print(f"reply: {poll(args.ip)}")

    elif args.cmd in ("initiate-remote", "forward", "reverse", "left", "right",
                      "auto", "home", "blade", "stop", "state", "version"):
        name_map = {"state": "query_state", "version": "query_version"}
        name = name_map.get(args.cmd, args.cmd.replace("-", "_"))
        replies = send_command(args.ip, name)
        print(f"received {len(replies)} packet(s):")
        for p in replies:
            print(f"  {p}")

    elif args.cmd == "set-time":
        import datetime
        dt = datetime.datetime.fromisoformat(args.datetime) if args.datetime else datetime.datetime.now()
        print(f"setting mower clock to {dt.isoformat()} (ISO weekday {dt.isoweekday()})")
        replies = set_time(args.ip, dt)
        print(f"received {len(replies)} packet(s):")
        for p in replies:
            print(f"  {p}")

    elif args.cmd == "param":
        param = int(args.param, 0)
        UART_PAYLOADS["_ad_hoc"] = remote_cmd(param)
        replies = send_command(args.ip, "_ad_hoc")
        print(f"received {len(replies)} packet(s):")
        for p in replies:
            print(f"  {p}")

    elif args.cmd == "repl":
        repl(args.ip)

    elif args.cmd == "replay":
        hex_bytes = _parse_hex_dump(args.path)
        print(f"loaded {len(hex_bytes)} bytes from {args.path}")
        print(f"  -> {decode(hex_bytes)}")
        resp = send_tcp(args.ip, hex_bytes)
        print(f"received {len(resp)} bytes: {resp.hex()}")
        if resp:
            try:
                print(decode(resp))
            except Exception as e:
                print(f"(decode failed: {e})")


def _parse_hex_dump(path: str) -> bytes:
    """Pull hex bytes out of a PCAPDroid-style dump file.

    Each line looks like:
      30 68 00 50 00 00 00 00  55 30 30 78 30 30 30 31  0h.P....U00x0001
    We grab tokens that are exactly 2 hex chars until we hit the ASCII column.
    """
    out = bytearray()
    with open(path) as f:
        for line in f:
            tokens = line.split()
            for tok in tokens:
                if len(tok) == 2 and all(c in "0123456789abcdefABCDEF" for c in tok):
                    out.append(int(tok, 16))
                else:
                    break  # rest of line is the ASCII column
    return bytes(out)


if __name__ == "__main__":
    main()
