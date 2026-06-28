# robo-lawn-mover

Reverse-engineered local control for the Lyfco E1800 / EGROBOT M10 robotic
lawn mower. The mower's app talks to it over the LAN on TCP :9600; this is a
Python client that speaks the same protocol so we can control it from a
computer.

## Status

Working end-to-end against the live mower:

- Frame encode / decode (verified byte-identical against captures)
- UDP discovery (`Search` → `SearchAck`)
- Replay of captured packets over TCP
- Live one-shot commands: `initiate-remote`, `forward`, `reverse`, `left`,
  `right`, `auto`, `home`, `blade`, `stop`, `poll`, `state`, `version`,
  `set-time`
- Synthesize new 0x69-family commands by param byte:
  `python mower.py param <ip> 0x09`
- Interactive REPL with a STOP failsafe on every exit path:
  `python mower.py repl <ip>`

The 0x69 command family uses a single parameter byte plus a one-byte checksum
(`checksum = (0x09 - param) & 0xFF`). Known params:

| Param | Action                |
| ----- | --------------------- |
| 0x00  | stop                  |
| 0x01  | forward               |
| 0x02  | reverse               |
| 0x03  | left                  |
| 0x04  | right                 |
| 0x05  | auto (autonomous mow) |
| 0x06  | enter remote mode     |
| 0x07  | home (return to dock) |
| 0x08  | blade engine on/off   |

Not yet mapped: anything `0x09`+ (likely either unused or maintenance-only).

## Usage

```bash
# Sanity check
ping -c 3 192.168.68.108

# Find the mower on the LAN (broadcast Search)
python mower.py discover

# Single commands
python mower.py initiate-remote 192.168.68.108
python mower.py forward 192.168.68.108
python mower.py stop 192.168.68.108
python mower.py home 192.168.68.108

# Interactive shell — single TCP socket, instant response,
# every exit path (q / EOF / Ctrl-C / kill / crash) sends STOP first
python mower.py repl 192.168.68.108

# Try an unknown param byte against the 0x69 command family
python mower.py param 192.168.68.108 0x0a

# Replay any captured PCAPDroid hex dump
python mower.py replay 192.168.68.108 "logs from phone/connect to klippis 18 31 37.txt"

# Decode a raw hex blob
python mower.py decode "30 68 00 50 ..."
```

REPL aliases: `f` forward, `b` reverse, `l` left, `r` right, `s` stop, `h`
home, `a` auto, `i` initiate-remote, `x` blade, `p` poll, `q` quit.

From Python:

```python
from mower import send_command, repl, remote_cmd

send_command("192.168.68.108", "forward")
# or drive interactively with the failsafe
repl("192.168.68.108")
```

## Protocol

Frame:

```
+----------+----------+-----------+-----------+
| "0h"     | LEN_BE16 | 4 x 0x00  | body...   |
+----------+----------+-----------+-----------+
   2 bytes    2 bytes   4 bytes
```

`LEN_BE16` is the total packet length (header + body), big-endian.

Body:

```
0x55                              sync byte ('U')
"00<c>00010000"                   11-byte ASCII tag, <c> is a free slot
CodeName=<value>&Key1=<v1>&...    key=value pairs, XOR-0x30 encoded
[ &UserBinaryData=##<raw bytes> ] optional raw binary blob (NOT XORed)
"=:" (0x3d 0x3a)                  terminator (omitted on UDP Search)
```

Encoding quirks:

- All text bytes in the body (keys, values, `=`, `&`, `##`) are XORed with `0x30`.
- The blob after `UserBinaryData=##` is raw — that's the actual command being
  relayed to the mower's internal MCU over its UART bus.
- `Len` field equals `len(binary_payload) + 4` in observed `GetUartData` /
  `UartUpLoadData` frames.

Known codenames:

| Codename         | Direction               | Purpose                                          |
| ---------------- | ----------------------- | ------------------------------------------------ |
| `Search`         | phone → broadcast (UDP) | discovery beacon, sent ~1/s                      |
| `SearchAck`      | mower → phone (UDP)     | discovery reply with DevName, Mac, Ip, WiFi info |
| `GetUartData`    | phone → mower (TCP)     | wraps a UART command for the MCU                 |
| `UartUpLoadData` | mower → phone (TCP)     | wraps the MCU's response                         |

The payload inside `UartUpLoadData` is the MCU's telemetry response. Two
query opcodes (positioned at byte 2 of the request) have been seen:

| Request opcode | Reply  | Notes                                                                                  |
| -------------- | ------ | -------------------------------------------------------------------------------------- |
| `0x7f` (idle)  | 12 B   | recurring keep-alive / status. Voltage *probably* lives somewhere here.                |
| `0x67` (state) | 32 B   | mower-state response, mostly zeros. App shows "None" — looks like a parser drift.      |
| `0x66` (ver)   | (none) | mower never replies. App shows "Unknown". The real version is in `SearchAck.DevName`.  |

Inner formats of the responses are not yet decoded.

### Set date and time

A 22-byte UART payload of the form (after XOR-0x30 each byte becomes ASCII):

```
"22" "T" YYYY MM DD W HH MM SS "FC" CC
```

- `W` is the ISO weekday (Mon=1..Sun=7).
- `SS` is sent as `"00"` — the app's picker has no seconds field.
- `CC` is a 1-byte checksum hex-encoded such that
  `sum(digit_values_at_positions_3..17) + CC == 0x32`.

See `set_time_payload()` in [mower.py](mower.py).

## Operational notes

- The mower accepts **one TCP client at a time**. Close the official phone
  app before connecting from here, or its socket freezes.
- The mower's IP must be stable. Set an address reservation in the router
  for the mower's MAC (`00:0E:A3:58:18:FA` in our case).
- WiFi latency on the mower is high after idle — first ping/packet can
  take 1–2 s while the radio wakes; subsequent packets are ~100 ms. TCP
  retransmits handle this transparently, but the REPL is much smoother to
  drive than one-shot CLI commands because it holds the socket open.
- `SearchAck` leaks the home WiFi SSID **and password** in plaintext on the
  LAN. Don't share raw captures publicly; rotate the WiFi password if
  untrusted devices have been on the network.
- The `blade` command spins up a literal cutting blade. Test with the
  mower upside-down or somewhere safe.

## Files

- [mower.py](mower.py) — client library + CLI + REPL
- [logs from phone/](logs%20from%20phone/) — PCAPDroid captures from the phone app
