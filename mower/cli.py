"""Argparse-based CLI. Entry point: ``python -m mower <subcommand>``."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .client import MowerClient, poll, send_command, send_tcp, set_time
from .codec import decode, parse_hex_dump
from .discovery import discover
from .monitor import monitor
from .payloads import UART_PAYLOADS, remote_cmd
from .repl import repl


# Names that are real UART_PAYLOADS keys and can be one-shot via send_command.
# Note: `state` and `version` are user-facing aliases of the query payloads.
SIMPLE_COMMANDS = (
    "initiate-remote", "forward", "reverse", "left", "right",
    "auto", "home", "blade", "stop", "state", "version",
)
CLI_TO_PAYLOAD = {
    "state": "query_state",
    "version": "query_version",
}


def _print_replies(replies):
    print(f"received {len(replies)} packet(s):")
    for p in replies:
        print(f"  {p}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mower",
        description="Lyfco / EGROBOT M10 local protocol tool",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="UDP broadcast Search, print replies")

    p = sub.add_parser("decode", help="Decode a hex string")
    p.add_argument("hex", help="hex bytes (whitespace ok)")

    p = sub.add_parser("raw", help="Send raw hex bytes over TCP")
    p.add_argument("ip"); p.add_argument("hex")

    p = sub.add_parser("replay", help="Replay a captured PCAPDroid hex dump")
    p.add_argument("ip"); p.add_argument("path")

    p = sub.add_parser("poll", help="Send one idle GetUartData poll")
    p.add_argument("ip")

    for name in SIMPLE_COMMANDS:
        ps = sub.add_parser(name, help=f"Send the '{name}' command")
        ps.add_argument("ip")

    p = sub.add_parser("set-time", help="Set mower date+time (default: now)")
    p.add_argument("ip")
    p.add_argument("--datetime", "-d",
                   help="ISO datetime, e.g. 2026-06-29T23:00. Default: now")

    p = sub.add_parser("param", help="Send a synthesized 0x69 command by param byte")
    p.add_argument("ip"); p.add_argument("param", help="byte, e.g. 0x09 or 9")

    p = sub.add_parser("repl", help="Interactive shell (always STOPs on exit)")
    p.add_argument("ip")

    p = sub.add_parser(
        "serve",
        help="Run the FastAPI control UI (requires the [web] extra)",
    )
    p.add_argument("--ip", required=True, help="mower IP, e.g. 192.168.68.108")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address. Default: 127.0.0.1 (localhost only). "
                        "Use 0.0.0.0 to expose to the LAN — no auth in v1.")
    p.add_argument("--port", type=int, default=8000, help="HTTP port. Default: 8000")
    p.add_argument("--mower-port", type=int, default=9600,
                   help="mower TCP port. Default: 9600")
    p.add_argument("--log-dir", help="optional log dir for /api/logs to read from")
    p.add_argument("--poll-interval", type=float, default=5.0,
                   help="seconds between background polls. Default: 5")
    p.add_argument("--no-state", dest="state", action="store_false",
                   help="skip the 32-byte query_state in the background poll")

    p = sub.add_parser(
        "monitor",
        help="Poll the mower forever; log responses to a CSV "
             "(single file or hourly-rotating)",
    )
    p.add_argument("ip")
    g = p.add_mutually_exclusive_group(required=False)
    g.add_argument("--out", "-o",
                   help="single CSV file for ad-hoc captures (one scenario "
                        "per file). Defaults to mower_log.csv if neither "
                        "--out nor --log-dir is given.")
    g.add_argument("--log-dir",
                   help="rotating-channel mode: write to "
                        "<log-dir>/<channel>-<hour>.csv with hourly rotation "
                        "and N-day retention. Recommended for long-running "
                        "monitoring on the Pi.")
    p.add_argument("--channel", default="mower",
                   help="channel name within --log-dir. Default: mower")
    p.add_argument("--retention-days", type=int, default=14,
                   help="days of history to keep when using --log-dir. "
                        "Default: 14")
    p.add_argument("--interval", "-i", type=float, default=60.0,
                   help="seconds between polls. Default: 60")
    p.add_argument("--no-state", dest="state", action="store_false",
                   help="skip the 32-byte query_state, only idle_poll")

    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.cmd == "discover":
        replies = discover()
        if not replies:
            print("no replies", file=sys.stderr)
            sys.exit(1)
        for r in replies:
            print(r)

    elif args.cmd == "decode":
        print(decode(bytes.fromhex("".join(args.hex.split()))))

    elif args.cmd == "raw":
        resp = send_tcp(args.ip, bytes.fromhex("".join(args.hex.split())))
        print(f"received {len(resp)} bytes: {resp.hex()}")
        if resp:
            try:    print(decode(resp))
            except Exception as e: print(f"(decode failed: {e})")

    elif args.cmd == "replay":
        data = parse_hex_dump(args.path)
        print(f"loaded {len(data)} bytes from {args.path}")
        print(f"  -> {decode(data)}")
        resp = send_tcp(args.ip, data)
        print(f"received {len(resp)} bytes: {resp.hex()}")
        if resp:
            try:    print(decode(resp))
            except Exception as e: print(f"(decode failed: {e})")

    elif args.cmd == "poll":
        print(f"reply: {poll(args.ip)}")

    elif args.cmd in SIMPLE_COMMANDS:
        name = CLI_TO_PAYLOAD.get(args.cmd, args.cmd.replace("-", "_"))
        _print_replies(send_command(args.ip, name))

    elif args.cmd == "set-time":
        dt = datetime.fromisoformat(args.datetime) if args.datetime else datetime.now()
        print(f"setting mower clock to {dt.isoformat()} (ISO weekday {dt.isoweekday()})")
        _print_replies(set_time(args.ip, dt))

    elif args.cmd == "param":
        param = int(args.param, 0)
        payload = remote_cmd(param)
        m = MowerClient(args.ip)
        m.connect()
        try:
            _print_replies(m.send_raw(payload))
        finally:
            m.close()

    elif args.cmd == "repl":
        repl(args.ip)

    elif args.cmd == "serve":
        try:
            from .web import run as run_web
        except ImportError as e:
            print(f"error: install the web extra first: pip install -e '.[web]'\n  ({e})",
                  file=sys.stderr)
            sys.exit(1)
        run_web(
            args.ip,
            host=args.host,
            port=args.port,
            mower_port=args.mower_port,
            log_dir=args.log_dir,
            poll_interval=args.poll_interval,
            poll_state=args.state,
        )

    elif args.cmd == "monitor":
        if args.log_dir is None and args.out is None:
            args.out = "mower_log.csv"
        monitor(
            args.ip,
            out_path=args.out,
            log_dir=args.log_dir,
            channel=args.channel,
            retention_days=args.retention_days,
            interval=args.interval,
            poll_state=args.state,
        )


if __name__ == "__main__":
    main()
