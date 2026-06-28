"""Frame codec for the Lyfco / EGROBOT local protocol.

Frame layout:

    +----------+----------+-----------+-----------+
    | "0h"     | LEN_BE16 | 4 x 0x00  | body...   |
    +----------+----------+-----------+-----------+

Body:

    0x55                              sync byte 'U'
    "00<c>00010000"                   11-byte ASCII tag (<c> is a free slot)
    CodeName=<v>&Key1=<v1>&...        key=value pairs, XOR-0x30 encoded
    [&UserBinaryData=##<raw bytes>]   optional raw binary blob (NOT XORed)
    "=:" (0x3d 0x3a)                  terminator (omitted on UDP Search)

All text bytes in the body are XOR-0x30 encoded — '=' (0x3d) becomes 0x0d,
'&' (0x26) becomes 0x16, etc. The blob inside `UserBinaryData=##...` is raw.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"0h"
SYNC = 0x55
SEP = 0x16            # '&' XOR 0x30
EQ = 0x0D             # '=' XOR 0x30
TERMINATOR = b"\x3d\x3a"   # "=:" — plaintext
BIN_PREFIX = b"\x13\x13"   # "##" XOR 0x30
BIN_FIELD = "UserBinaryData"


def xor(data: bytes) -> bytes:
    """XOR every byte with 0x30 (the body's encoding key). Self-inverse."""
    return bytes(b ^ 0x30 for b in data)


@dataclass
class Packet:
    """A decoded protocol frame."""

    tag: str
    codename: str
    fields: dict[str, str] = field(default_factory=dict)
    binary: bytes | None = None

    def __repr__(self) -> str:
        b = self.binary.hex() if self.binary is not None else None
        return (f"Packet(codename={self.codename!r}, fields={self.fields}, "
                f"binary={b}, tag={self.tag!r})")


def encode(codename: str, fields: dict[str, str] | None = None,
           binary: bytes | None = None, tag_char: str = "x",
           terminator: bool = True) -> bytes:
    """Build a wire-ready packet. Field values are XOR-encoded automatically.

    The `binary` blob is wrapped in `UserBinaryData=##...` and sent raw.
    """
    body = bytearray([SYNC])
    body += f"00{tag_char}00010000".encode()
    body += xor(b"CodeName") + bytes([EQ]) + xor(codename.encode())
    for k, v in (fields or {}).items():
        body.append(SEP)
        body += xor(k.encode()) + bytes([EQ]) + xor(v.encode())
    if binary is not None:
        body.append(SEP)
        body += xor(BIN_FIELD.encode()) + bytes([EQ]) + BIN_PREFIX + binary
    if terminator:
        body += TERMINATOR
    total = len(body) + 8
    return bytes(MAGIC + struct.pack(">H", total) + b"\x00\x00\x00\x00" + body)


def decode(buf: bytes) -> Packet:
    """Parse a complete frame. Lenient about truncated tail / missing terminator."""
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
        key = xor(rest[i:j]).decode("latin1", errors="replace")
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
        value = xor(rest[i:k]).decode("latin1", errors="replace")
        if key == "CodeName":
            codename = value
        else:
            fields[key] = value
        i = k + 1
    return Packet(tag=tag, codename=codename, fields=fields, binary=binary)


def parse_hex_dump(path: str) -> bytes:
    """Pull hex bytes out of a PCAPDroid-style dump file.

    Each line looks like:

        30 68 00 50 00 00 00 00  55 30 30 78 30 30 30 31  0h.P....U00x0001

    Grab the 2-hex-char tokens until we hit the ASCII column or non-hex text.
    """
    out = bytearray()
    with open(path) as f:
        for line in f:
            for tok in line.split():
                if len(tok) == 2 and all(c in "0123456789abcdefABCDEF" for c in tok):
                    out.append(int(tok, 16))
                else:
                    break
    return bytes(out)
