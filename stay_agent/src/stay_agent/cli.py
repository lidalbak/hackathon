"""stay-agent: serve | simulate | replica-stay | setup-device"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import threading
from datetime import datetime
from pathlib import Path

from stay_agent.channels import ReplicaGuest, StaticBookings, channels_from_settings, create_replica_stay
from stay_agent.config import Settings
from stay_agent.harness import Inline, StayHarness, Worker
from stay_agent.llm import Writer
from stay_agent.server import DEFAULT_PORT, make_server
from stay_agent.simulate import SCENARIOS, demo_booking, readings


ENV_FILES = (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env")  # here, or stay_agent/.env


def load_env_files() -> None:
    """KEY=value lines from .env (git-ignored). The real environment wins; empty values are skipped."""
    for path in dict.fromkeys(ENV_FILES):
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if sep and key and not key.startswith("#") and value:
                os.environ.setdefault(key, value)


def claude_status(settings: Settings) -> str:
    if not settings.use_claude:
        return "off (templates)"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return f"on, {settings.model} (API key found)"
    return f"on, {settings.model}, but no API key in stay_agent/.env or the environment: templates until one is set"


def lan_ip() -> str:
    """The address the device should post to (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def cmd_serve(args: argparse.Namespace) -> int:
    settings = Settings.load(args.config)
    if args.no_claude:
        settings.use_claude = False
    bookings, guest, owner, cleaning = channels_from_settings(settings)
    harness = StayHarness(settings, Writer(settings), bookings, guest, owner, cleaning, executor=Worker())
    server = make_server(harness, args.port)
    if settings.replica_url and settings.answer_guests:
        from stay_agent.replies import InboxWatcher

        InboxWatcher(harness, owner, ReplicaGuest(settings).send_to).start()
        print("Answering guests who write in the Airbnb copy")
    print(f"stay-agent listening on port {args.port}")
    print(f"Claude: {claude_status(settings)}")
    try:
        if args.usb:
            from stay_agent.usb import run_usb

            print("Device: over USB (close `atech monitor` first)")
            threading.Thread(target=server.serve_forever, name="stay-agent-http", daemon=True).start()
            run_usb(harness, args.usb_port)
        else:
            print(f"Device server URL for this laptop: http://{lan_ip()}:{args.port}")
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    settings = Settings.load(args.config)
    if args.no_claude:
        settings.use_claude = False
    _, guest, owner, cleaning = channels_from_settings(settings)
    booking = demo_booking(settings.tz, datetime.now(settings.tz).date())
    if settings.replica_url:  # a real conversation in the Airbnb copy, so the messages show up there
        conv = create_replica_stay(settings, booking.guest_name, booking.guests)
        booking = booking.model_copy(update={"id": str(conv)})
        print(f"Created stay {conv} in the Airbnb copy: {settings.replica_url.rstrip('/')}/hosting/messages/{conv}")
    harness = StayHarness(
        settings, Writer(settings), StaticBookings([booking], settings.tz), guest, owner, cleaning, executor=Inline()
    )
    print(f"Simulating '{args.scenario}': {booking.guest_name}, {booking.check_in:%a %H:%M} to {booking.check_out:%a %H:%M}")
    print(f"Claude: {claude_status(settings)}\n")
    last_level = None
    for r in readings(args.scenario, booking):
        reply = harness.ingest(r)
        if reply.level != last_level:
            print(f"[{r.at:%H:%M}] device: {reply.level.upper():6} {reply.text}")
            last_level = reply.level
    harness.finish(booking.check_out)
    return 0


def cmd_replica_stay(args: argparse.Namespace) -> int:
    settings = Settings.load(args.config)
    if not settings.replica_url:
        print("Set replica_url in the settings first.")
        return 1
    conv = create_replica_stay(settings, args.guest, args.guests, args.nights)
    print(f"Created a confirmed stay for {args.guest} ({args.guests} guests) from today: conversation {conv}")
    print(f"Host inbox: {settings.replica_url.rstrip('/')}/hosting/messages/{conv}")
    print("`stay-agent serve` picks it up within 30 s.")
    return 0


def cmd_setup_device(args: argparse.Namespace) -> int:
    """Stores Wi-Fi and server settings on the device over USB; it then reboots and joins."""
    from atech import Board  # only needed for this command

    value = {"ssid": args.ssid, "pass": args.password, "server": args.server.rstrip("/"), "id": args.id}
    if args.token:
        value["token"] = args.token
    with Board.connect() as board:
        board.call("net_set", value)
    print(f"Saved. The device reboots, joins '{args.ssid}' and posts to {value['server']}/api/readings.")
    print("Its screen shows the connection state on the bottom line. You can unplug USB now.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stay-agent")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the agent: the device posts readings here")
    p.add_argument("--config", help="settings JSON (see config.example.json)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-claude", action="store_true", help="templates only, no API calls")
    p.add_argument("--usb", action="store_true", help="talk to the device over USB instead of waiting for Wi-Fi posts")
    p.add_argument("--usb-port", help="serial port, if several boards are plugged in")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("simulate", help="run a whole stay in seconds, without the device")
    p.add_argument("scenario", choices=SCENARIOS)
    p.add_argument("--config")
    p.add_argument("--no-claude", action="store_true")
    p.set_defaults(fn=cmd_simulate)

    p = sub.add_parser("replica-stay", help="create a stay from today in the Airbnb copy, for the demo")
    p.add_argument("--config", required=True)
    p.add_argument("--guest", default="Anna")
    p.add_argument("--guests", type=int, default=2)
    p.add_argument("--nights", type=int, default=1)
    p.set_defaults(fn=cmd_replica_stay)

    p = sub.add_parser("setup-device", help="store Wi-Fi and server settings on the device over USB")
    p.add_argument("--ssid", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--server", required=True, help="e.g. http://192.168.1.20:8080, or the cloud URL")
    p.add_argument("--token", default="", help="must match device_token in the agent's settings")
    p.add_argument("--id", default="stay-sensor-1")
    p.set_defaults(fn=cmd_setup_device)

    args = parser.parse_args(argv)
    load_env_files()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "anthropic", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
