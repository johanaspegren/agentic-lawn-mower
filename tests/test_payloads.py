"""Verify payload synthesisers and the state/voltage decoder against the
exact captured byte sequences."""

from __future__ import annotations

from datetime import datetime

import pytest

from mower import codec
from mower.payloads import (
    UART_PAYLOADS,
    decode_state,
    remote_cmd,
    set_time_payload,
    wrap_uart,
)


# --- 0x69-family commands ---------------------------------------------------


@pytest.mark.parametrize("param, expected_hex", [
    (0x00, "0008690076757309"),  # stop
    (0x01, "0008690176757308"),  # forward
    (0x02, "0008690276757307"),  # reverse
    (0x03, "0008690376757306"),  # left
    (0x04, "0008690476757305"),  # right
    (0x05, "0008690576757304"),  # auto
    (0x06, "0008690676757303"),  # initiate_remote
    (0x07, "0008690776757302"),  # home
    (0x08, "0008690876757301"),  # blade
])
def test_remote_cmd_byte_exact(param, expected_hex):
    assert remote_cmd(param).hex() == expected_hex


def test_remote_cmd_rejects_oversize_param():
    with pytest.raises(ValueError):
        remote_cmd(0x100)


# --- set_time payload -------------------------------------------------------


@pytest.mark.parametrize("dt, expected_hex", [
    (datetime(2026, 6, 29, 23, 0),
     "02026402000206000602090102030000000076730101"),  # Mon 23:00
    (datetime(2026, 6, 27, 10, 3),
     "02026402000206000602070601000003000076730076"),  # Sat 10:03
])
def test_set_time_payload_byte_exact(dt, expected_hex):
    assert set_time_payload(dt).hex() == expected_hex


def test_set_time_checksum_target():
    """Sum of digit positions 3..17 plus checksum byte must equal 0x32."""
    pkt = set_time_payload(datetime(2026, 6, 29, 23, 0))
    s = codec.xor(pkt).decode("latin1")
    assert len(s) == 22
    digit_sum = sum(int(c) for c in s[3:18])
    checksum = int(s[20:22], 16)
    assert digit_sum + checksum == 0x32


# --- state-query decoder ----------------------------------------------------


_STATE_SAMPLES = [
    # (label, raw bytes hex, expected voltage, expected state label)
    ("mowing 24.90",
     "0305670000000000020000000702040900000000000000000000000000000076097103",
     24.90, "mowing"),
    ("mowing 24.75",
     "0305670000000000020000000702040705000000000000000000000000000076097100",
     24.75, "mowing"),
    ("mowing 24.62",
     "0305670000000000020000000702040602000000000000000000000000000076097104",
     24.62, "mowing"),
    ("mowing 24.32",
     "0305670000000000020000000702040302000000000000000000000000000076097107",
     24.32, "mowing"),
    ("charging 26.79",
     "0305670000000000000000000002060709000000000000000000000000000076097103",
     26.79, "charging"),
    ("charging 26.78",
     "0305670000000000000000000002060708000000000000000000000000000076097104",
     26.78, "charging"),
    ("docked_full 27.78",
     "0305670000000000010000000102070708000000000000000000000000000076097102",
     27.78, "docked_full"),
    ("docked_full 27.79",
     "0305670000000000010000000102070709000000000000000000000000000076097101",
     27.79, "docked_full"),
    ("idle off dock 26.86",
     "0305670000000000000000000102060806000000000000000000000000000076097104",
     26.86, "idle_off_dock"),
    ("idle off dock 27.35",
     "0305670000000000000000000102070305000000000000000000000000000076097109",
     27.35, "idle_off_dock"),
]


@pytest.mark.parametrize("label, hx, expected_v, expected_state",
                         _STATE_SAMPLES,
                         ids=lambda *args: args[0])
def test_decode_state(label, hx, expected_v, expected_state):
    d = decode_state(bytes.fromhex(hx))
    assert d["state"] == expected_state, label
    assert d["voltage_v"] == pytest.approx(expected_v, abs=0.005), label


def test_decode_state_returns_empty_for_unrecognised_payload():
    assert decode_state(b"") == {}
    assert decode_state(b"\xff" * 20) == {}     # wrong magic
    assert decode_state(b"\x03\x05\x67") == {}  # too short


# --- wrap_uart envelope -----------------------------------------------------


def test_wrap_uart_query_state_matches_phone_capture():
    """Our wrap_uart(query_state, tag='x') must equal the exact 80 bytes the
    EGROBOT app sent in the captured docked-state poll."""
    phone_rows = [
        "30 68 00 50 00 00 00 00  55 30 30 78 30 30 30 31",
        "30 30 30 30 73 5f 54 55  7e 51 5d 55 0d 77 55 44",
        "65 51 42 44 74 51 44 51  16 73 58 5e 0d 00 16 7c",
        "55 5e 0d 01 01 16 65 43  55 42 72 59 5e 51 42 49",
        "74 51 44 51 0d 13 13 00  07 67 76 75 76 73 3d 3a",
    ]
    phone = bytes.fromhex("".join(phone_rows).replace(" ", ""))
    ours = wrap_uart(UART_PAYLOADS["query_state"], tag_char="x")
    assert ours == phone
