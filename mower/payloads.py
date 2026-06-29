"""UART payloads that live inside `GetUartData.UserBinaryData`.

These are the raw byte sequences the mower's WiFi board relays to its internal
MCU. Each is verified byte-identical against PCAPDroid captures.
"""

from __future__ import annotations

from datetime import datetime

from .codec import encode


def remote_cmd(param: int) -> bytes:
    """Build a 0x69-family remote-control UART payload.

    Frame: ``00 08 69 <param> 76 75 73 <checksum>``. The 8 bytes sum to
    ``0x1D8``; equivalently ``checksum = (0x09 - param) & 0xFF``.
    """
    if not 0 <= param <= 0xFF:
        raise ValueError("param must be a byte")
    checksum = (0x09 - param) & 0xFF
    return bytes([0x00, 0x08, 0x69, param, 0x76, 0x75, 0x73, checksum])


def set_time_payload(dt: datetime) -> bytes:
    """Build the 22-byte UART payload that sets the mower's date and time.

    After XOR-0x30 the bytes spell::

        "22" "T" YYYY MM DD W HH MM SS "FC" CC

    where W is the ISO weekday (Mon=1..Sun=7), SS is sent as "00" (the picker
    has no seconds field), and CC is a 1-byte checksum hex-encoded such that
    ``sum(digit_values_at_positions_3..17) + CC == 0x32``.
    """
    s = (
        "22T"
        f"{dt.year:04d}{dt.month:02d}{dt.day:02d}"
        f"{dt.isoweekday()}"
        f"{dt.hour:02d}{dt.minute:02d}00"
    )
    digit_sum = sum(int(c) for c in s[3:18])
    checksum = (0x32 - digit_sum) & 0xFF
    s += "FC" + f"{checksum:02X}"
    if len(s) != 22:
        raise AssertionError(f"set_time payload wrong length: {len(s)}")
    return bytes(b ^ 0x30 for b in s.encode())


# Canonical UART payloads. Names are the same ones the CLI / REPL / Client
# accept as command identifiers.
UART_PAYLOADS: dict[str, bytes] = {
    "idle_poll":       bytes.fromhex("00077f76760004"),
    "query_state":     bytes.fromhex("00076776757673"),  # 32-byte reply
    "query_version":   bytes.fromhex("00076676757674"),  # no reply (firmware drift)
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


def decode_state(binary: bytes) -> dict:
    """Decode known fields from a 0x67 ``query_state`` response.

    Known so far (M10 firmware 1.5.4, captures from 2026-06-29):

    - **voltage_v** (positions 13..16) is the battery voltage in volts.
      Each of the four bytes is a single decimal digit, concatenated to a
      4-digit centivolt value (`pos13 * 1000 + pos14 * 100 + pos15 * 10 +
      pos16`) divided by 100. Same one-digit-per-byte convention used by
      the set-time packet.

      Verified against the app showing 24.90 V → bytes ``02 04 09 00`` and
      24.75 V → bytes ``02 04 07 05``.

    Returns an empty dict for unrecognised payload shapes rather than
    raising. The rest of the response bytes are not yet mapped.
    """
    out: dict = {}
    if len(binary) < 17 or binary[:3] != b"\x03\x05\x67":
        return out
    digits = binary[13:17]
    if all(d <= 9 for d in digits):
        out["voltage_v"] = (
            digits[0] * 1000 + digits[1] * 100 + digits[2] * 10 + digits[3]
        ) / 100
    return out


def wrap_uart(payload: bytes, chn: str = "0", tag_char: str = "y") -> bytes:
    """Wrap a UART payload in a `GetUartData` packet ready for TCP send."""
    return encode(
        "GetUartData",
        fields={"Chn": chn, "Len": str(len(payload) + 4)},
        binary=payload,
        tag_char=tag_char,
    )
