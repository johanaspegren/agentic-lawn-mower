# robo-lawn-mover

Reverse-engineered local control for the Lyfco E1800 / EGROBOT M10 robotic
lawn mower. The mower's app talks to it over the LAN on TCP :9600; this is a
Python client that speaks the same protocol so we can control it from a
computer.

This repo has two parts:

- `mower/` (Mac/Linux laptop): mower protocol client, CLI, REPL, and web UI
- `sensor/` (Raspberry Pi "roboworm"): IMU + camera collection server

## Start here (without the mower hardware)

If the mower is unavailable, you can still verify your dev setup and the Pi side.

### 1) Local dev setup on your Mac

```bash
cd ~/dev
git clone <your-repo-url> robo-lawn-mover
cd robo-lawn-mover

python -m venv .venv
source .venv/bin/activate
pip install -U pip

# Core package (CLI/API)
pip install -e .

# Optional: web UI dependencies for `python -m mower serve ...`
pip install -r requirements.txt

# Quick sanity checks
python -m mower --help
pytest -q
```

### 2) Check the Raspberry Pi (roboworm) over SSH

```bash
ssh <pi-user>@192.168.68.122
hostname
python3 --version
cd ~/robo-lawn-mover
```

If the repo is not present on the Pi yet:

```bash
git clone <your-repo-url> ~/robo-lawn-mover
cd ~/robo-lawn-mover
```

Prepare the Pi venv and deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r sensor/requirements.txt
pip install -e .
```

Run Pi-side service checks (these work even if the mower itself is offline):

```bash
# Starts the sensor API on :8001
python -m sensor.server
```

Important for camera in the UI: `sensor.server` only serves
`/latest.jpg`; it does not capture new frames. To keep the camera image
updating, also run the snapshot producer on the Pi in a second terminal:

```bash
python sensor/camera_snap.py --dir ./snapshots --interval 30
```

In a second SSH session on the Pi:

```bash
curl -s http://127.0.0.1:8001/ | python -m json.tool
curl -i http://127.0.0.1:8001/api/imu
```

Expected: `/` returns service JSON. `/api/imu` may return 503 until the IMU
loop has produced samples.

### 3) Run the UI on your Mac (with Pi sensor panels)

From your Mac, in the repo root:

```bash
source .venv/bin/activate

# if needed in this venv
pip install -e .
pip install -r requirements.txt

# Replace with your mower IP. The Pi URL points at roboworm's sensor.server.
python -m mower serve --ip 192.168.68.108 --pi-url http://192.168.68.122:8001
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

If the mower hardware is offline, the UI still starts; mower command/polling
calls will fail while Pi camera/IMU panels can still load from `--pi-url`.

Optional: in the Camera panel you can now start/stop an on-demand live video
session. This uses the Pi camera directly while active. The UI also lets you
pick live-video duration, resolution, and FPS before starting.

## Status

Working end-to-end against the live mower:

- Frame encode / decode (verified byte-identical against captures)
- UDP discovery (`Search` → `SearchAck`)
- Replay of captured packets over TCP
- Live one-shot commands: `initiate-remote`, `forward`, `reverse`, `left`,
  `right`, `auto`, `home`, `blade`, `stop`, `poll`
- Synthesize new 0x69-family commands by param byte:
  `python -m mower param <ip> 0x09`
- Interactive REPL with a STOP failsafe on every exit path:
  `python -m mower repl <ip>`

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

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

# only needed for the web UI (`python -m mower serve ...`)
pip install -r requirements.txt
```

The core protocol client and CLI work without `requirements.txt` — the third-party
deps are only required for `python -m mower serve` (the FastAPI control UI).

## Usage

### CLI

```bash
# (one-time, every shell:) source .venv/bin/activate
ping -c 3 192.168.68.108

# Find the mower on the LAN (broadcast Search)
python -m mower discover

# Single commands
python -m mower initiate-remote 192.168.68.108
python -m mower forward 192.168.68.108
python -m mower stop 192.168.68.108
python -m mower home 192.168.68.108

# Set the mower's clock
python -m mower set-time 192.168.68.108
python -m mower set-time 192.168.68.108 -d 2026-06-29T23:00

# Interactive shell — single TCP socket, instant response,
# every exit path (q / EOF / Ctrl-C / kill / crash) sends STOP first
python -m mower repl 192.168.68.108

# Try an unknown param byte against the 0x69 command family
python -m mower param 192.168.68.108 0x0a

# Replay any captured PCAPDroid hex dump
python -m mower replay 192.168.68.108 "logs from phone/old/PCAPdroid_28_Jun_20_19_35.txt"

# Decode a raw hex blob
python -m mower decode "30 68 00 50 ..."
```

REPL aliases: `f` forward, `b` reverse, `l` left, `r` right, `s` stop, `h`
home, `a` auto, `i` initiate-remote, `x` blade, `p` poll, `?` state,
`v` version, `q` quit.

### Python API

The main entry point is `MowerClient`, a persistent-socket client. As a
context manager it always sends `stop` on exit — including on exceptions
and `KeyboardInterrupt` — which is the recommended way to drive the mower
from scripts and AI agents:

```python
import time
from mower import MowerClient

with MowerClient("192.168.68.108") as m:
    m.initiate_remote()
    m.forward()
    time.sleep(2)
    m.left()
    time.sleep(1)
    # exiting the `with` block sends STOP
```

For one-shot scripts:

```python
from mower import send_command, set_time, poll, discover

send_command("192.168.68.108", "home")
set_time("192.168.68.108")        # defaults to local now()
reply = poll("192.168.68.108")    # single idle-poll reply Packet
beacons = discover()              # UDP-broadcast Search, collect replies
```

Low-level codec:

```python
from mower import Packet, encode, decode, UART_PAYLOADS, remote_cmd, set_time_payload

# Synthesize an arbitrary 0x69-family payload
payload = remote_cmd(0x0a)
```

### Package layout

```
mower/
  __init__.py     # public API
  codec.py        # Packet, encode(), decode(), parse_hex_dump()
  payloads.py     # UART_PAYLOADS, remote_cmd(), set_time_payload(), wrap_uart()
  client.py       # MowerClient, send_command(), set_time(), poll(), send_tcp()
  discovery.py    # discover()
  repl.py         # interactive REPL with STOP failsafe
  cli.py          # argparse entry point
  __main__.py     # `python -m mower`
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

| Codename           | Direction                | Purpose                                          |
| ------------------ | ------------------------ | ------------------------------------------------ |
| `Search`         | phone → broadcast (UDP) | discovery beacon, sent ~1/s                      |
| `SearchAck`      | mower → phone (UDP)     | discovery reply with DevName, Mac, Ip, WiFi info |
| `GetUartData`    | phone → mower (TCP)     | wraps a UART command for the MCU                 |
| `UartUpLoadData` | mower → phone (TCP)     | wraps the MCU's response                         |

The 12-byte payload inside `UartUpLoadData` is the MCU's telemetry response.
Its inner format is not yet decoded — opaque bytes for now.

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

- [mower/](mower/) — Python package (library + CLI + REPL)
- [logs from phone/](<logs%20from%20phone/>) — PCAPDroid captures from the phone app
