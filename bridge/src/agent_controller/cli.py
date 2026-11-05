"""agent-controller: run the bridge, or act as an agent's hook.

  agent-controller serve [--port 8765] [--board /dev/cu.usbmodem...] [--wait 60]
  agent-controller hook claude          # called by Claude Code, reads its hook JSON on stdin
  agent-controller claude-settings      # prints the hooks block for ~/.claude/settings.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import sys
import threading
import urllib.error
import urllib.request

from agent_controller import claude
from agent_controller.server import DEFAULT_PORT

log = logging.getLogger("agent_controller")
DEFAULT_WAIT = 60


# ---------------- hook side: must never break the agent ----------------

def _post(port: int, path: str, body: dict, timeout: float) -> dict | None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None  # bridge not running, or too slow: stay out of the way


def hook_claude(port: int, wait: float, stdin=sys.stdin, stdout=sys.stdout) -> int:
    try:
        payload = json.load(stdin)
    except ValueError:
        return 0
    event = claude.to_event(payload)
    if event is None:
        return 0
    body = {
        "agent": event.agent.value,
        "session_id": event.session_id,
        "state": event.state.value,
        "summary": event.summary,
        "project": event.project,
    }
    if payload.get("hook_event_name") == claude.PERMISSION:
        reply = _post(port, "/permission", body, timeout=wait + 5)
        out = claude.decision_output((reply or {}).get("decision"))
        if out:
            stdout.write(out + "\n")
    else:
        body["ended"] = payload.get("hook_event_name") == claude.ENDED
        _post(port, "/event", body, timeout=1.0)
    return 0


# ---------------- bridge side ----------------

def serve(port: int, board_port: str | None, wait: float) -> int:
    from atech import Board

    from agent_controller.controller import Controller
    from agent_controller.server import make_server

    try:
        board = Board.connect(port=board_port)
    except Exception as exc:  # no board, or the port is held by `atech monitor`
        print(f"Could not open the board: {exc}\nClose `atech monitor` if it is running, then try again.", file=sys.stderr)
        return 1
    controller = Controller(board)
    server = make_server(controller, port=port, wait_seconds=wait)

    def buttons() -> None:
        for event in board.events(keys=["approve", "deny", "knob", "knob_btn"]):
            if event.key == "knob":
                controller.on_knob(int(event.value))
            elif event.key == "knob_btn" and event.value == 1:
                controller.on_knob_press()
            elif event.value == 1:
                answered = controller.on_button(event.key)
                log.info("%s pressed%s", event.key, f" -> request {answered}" if answered else " (nothing waiting)")

    threading.Thread(target=buttons, daemon=True).start()
    controller.start()  # show the idle screen

    def stop(*_args) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"Agent Controller bridge on http://127.0.0.1:{port}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    finally:
        controller.shutdown()
        board.close()
        print("Bridge stopped. Agents fall back to their own prompts.", flush=True)
    return 0


def claude_settings(wait: int) -> int:
    exe = shutil.which("agent-controller") or os.path.abspath(sys.argv[0])
    print(json.dumps(claude.settings_snippet(exe, wait), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-controller")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="local bridge port")
    parser.add_argument("--wait", type=int, default=DEFAULT_WAIT, help="seconds to wait for a button press")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_serve = sub.add_parser("serve", help="run the bridge (keeps the board's USB port)")
    p_serve.add_argument("--board", default=None, help="serial port, default: first Atech board found")
    p_serve.add_argument("-v", "--verbose", action="store_true")
    p_hook = sub.add_parser("hook", help="called by an agent's hook")
    p_hook.add_argument("agent", choices=["claude"])
    sub.add_parser("claude-settings", help="print the hooks block for ~/.claude/settings.json")
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")
        return serve(args.port, args.board, args.wait)
    if args.cmd == "hook":
        return hook_claude(args.port, args.wait)
    return claude_settings(args.wait)


if __name__ == "__main__":
    sys.exit(main())
