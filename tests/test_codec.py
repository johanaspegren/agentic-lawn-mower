"""Frame encode/decode round-trips against captured PCAPDroid hex dumps."""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from mower import codec
from mower.codec import (
    BIN_FIELD,
    Packet,
    decode,
    encode,
    parse_hex_dump,
)

REPO = Path(__file__).resolve().parent.parent
CAPTURES = REPO / "logs from phone"


def _all_captures() -> list[Path]:
    return sorted(Path(p) for p in glob.glob(str(CAPTURES / "**/*.txt"),
                                              recursive=True))


def test_xor_is_self_inverse():
    src = b"\x73\x5f\x54\x55"            # "CodeN" XORed
    assert codec.xor(src) == b"Code"
    assert codec.xor(codec.xor(src)) == src


def test_encode_minimal_packet_round_trips():
    p = encode("Search", tag_char="+", terminator=False)
    decoded = decode(p)
    assert decoded.codename == "Search"
    assert decoded.tag.startswith("U00+")


def test_encode_with_fields_and_binary_round_trips():
    p = encode(
        "GetUartData",
        fields={"Chn": "0", "Len": "11"},
        binary=b"\x00\x07\x7f\x76\x76\x00\x04",
        tag_char="x",
    )
    decoded = decode(p)
    assert decoded.codename == "GetUartData"
    assert decoded.fields == {"Chn": "0", "Len": "11"}
    assert decoded.binary == b"\x00\x07\x7f\x76\x76\x00\x04"


@pytest.mark.parametrize("capture_path", _all_captures(),
                         ids=lambda p: p.name)
def test_every_capture_decodes(capture_path):
    """Every captured PCAPDroid hex dump in the repo should decode."""
    data = parse_hex_dump(str(capture_path))
    if not data:
        pytest.skip(f"{capture_path.name} parsed to zero bytes")
    p = decode(data)
    assert isinstance(p, Packet)
    assert p.codename, f"{capture_path.name}: empty codename"


def test_decode_strips_optional_terminator():
    p1 = encode("Search", tag_char="+", terminator=True)
    p2 = encode("Search", tag_char="+", terminator=False)
    # Even though one has the "=:" terminator and the other doesn't,
    # both should decode to the same logical packet.
    assert decode(p1).codename == decode(p2).codename


def test_decode_rejects_bad_magic():
    with pytest.raises(ValueError, match="bad magic"):
        decode(b"XX\x00\x10" + b"\x00" * 16)
