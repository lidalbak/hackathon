"""agent-controller: run the bridge, or act as an agent's hook.

  agent-controller serve [--board PORT] [--language no] [--whisper-model base]
  agent-controller hook claude             # called by Claude Code, reads its hook JSON on stdin
  agent-controller install-claude-hooks    # puts the hooks into ~/.claude/settings.json (with a backup)
  agent-controller claude-settings         # prints the hooks block instead
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
from pathlib import Path

from agent_controller import claude
from agent_controller.server import DEFAULT_PORT, Waits

log = logging.getLogger("agent_controller")
WAITS = Waits()


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


def hook_claude(port: int, waits: Waits = WAITS, stdin=sys.stdin, stdout=sys.stdout) -> int:
    try:
        payload = json.load(stdin)
    except ValueError:
        return 0
    event = claude.to_event(payload)
    if event is None:
        return 0
    name = payload.get("hook_event_name")
    body = {
        "agent": event.agent.value,
        "session_id": event.session_id,
        "state": event.state.value,
        "summary": event.summary,
        "project": event.project,
    }
    out = ""
    if name == claude.PERMISSION and payload.get("tool_name") == claude.CHOICE_TOOL:
        tool_input = payload.get("tool_input") or {}
        body["questions"] = tool_input.get("questions") or []
        reply = _post(port, "/choice", body, timeout=waits.choice + 5)
        out = claude.choice_output(tool_input, (reply or {}).get("answers"))
    elif name == claude.PERMISSION:
        reply = _post(port, "/permission", body, timeout=waits.permission + 5)
        out = claude.decision_output((reply or {}).get("decision"))
    elif name == claude.STOP and not payload.get("stop_hook_active") and claude.trailing_question(
        str(payload.get("last_assistant_message") or "")
    ):
        body["prompt"] = event.summary
        reply = _post(port, "/text", body, timeout=waits.text_window + waits.text_answer + 5)
        out = claude.text_output((reply or {}).get("text"))
    else:
        body["ended"] = name == claude.ENDED
        _post(port, "/event", body, timeout=1.0)
        if name == claude.START:
            out = claude.session_start_output()
    if out:
        stdout.write(out + "\n")
    return 0


# ---------------- bridge side ----------------

def serve(port: int, board_port: str | None, model: str, language: str | None, voice: bool) -> int:
    from atech import Board

    from agent_controller.controller import Controller
    from agent_controller.server import make_server

    try:
        board = Board.connect(port=board_port)
    except Exception as exc:  # no board, or the port is held by `atech monitor`
        print(f"Could not open the board: {exc}\nClose `atech monitor` if it is running, then try again.", file=sys.stderr)
        return 1

    transcribe = None
    if voice:
        from agent_controller.voice import Whisper

        transcribe = Whisper(model, language)
        transcribe.load_in_background()

    controller = Controller(board, transcribe)
    server = make_server(controller, port=port, waits=WAITS)

    def board_events() -> None:
        for event in board.events(keys=["approve", "deny", "knob", "knob_btn", "ptt", "pcm"]):
            try:
                if event.key == "pcm":
                    controller.on_pcm(str(event.value))
                elif event.key == "knob":
                    controller.on_knob(int(event.value))
                elif event.key == "knob_btn" and event.value == 1:
                    controller.on_knob_press()
                elif event.key == "ptt":
                    log.info("push-to-talk %s", "on" if event.value == 1 else "off")
                    controller.on_ptt(event.value == 1)
                elif event.key in ("approve", "deny") and event.value == 1:
                    answered = controller.on_button(event.key)
                    log.info("%s pressed%s", event.key, f" -> request {answered}" if answered else " (nothing waiting)")
            except Exception:
                log.exception("board event %s failed", event.key)

    threading.Thread(target=board_events, daemon=True).start()
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


def _snippet() -> dict:
    exe = shutil.which("agent-controller") or os.path.abspath(sys.argv[0])
    return claude.settings_snippet(
        exe, int(WAITS.permission), int(WAITS.choice), int(WAITS.text_window + WAITS.text_answer)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-controller")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="local bridge port")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_serve = sub.add_parser("serve", help="run the bridge (keeps the board's USB port)")
    p_serve.add_argument("--board", default=None, help="serial port, default: first Atech board found")
    p_serve.add_argument("--whisper-model", default="base", help="tiny, base, small, ... (small is better for Norwegian)")
    p_serve.add_argument("--language", default=None, help="speech language, e.g. no or en (default: detect)")
    p_serve.add_argument("--no-voice", action="store_true", help="turn speech to text off")
    p_serve.add_argument("-v", "--verbose", action="store_true")
    p_hook = sub.add_parser("hook", help="called by an agent's hook")
    p_hook.add_argument("agent", choices=["claude"])
    p_install = sub.add_parser("install-claude-hooks", help="add the hooks to Claude Code's settings (keeps a backup)")
    p_install.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    sub.add_parser("claude-settings", help="print the hooks block for ~/.claude/settings.json")
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")
        return serve(args.port, args.board, args.whisper_model, args.language, not args.no_voice)
    if args.cmd == "hook":
        return hook_claude(args.port)
    if args.cmd == "install-claude-hooks":
        backup = claude.install_hooks(Path(args.settings), _snippet())
        print(f"Hooks installed in {args.settings}.\nBackup: {backup if backup.exists() else '(no file before)'}\n"
              "Start a new Claude Code session to load them.")
        return 0
    print(json.dumps(_snippet(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
