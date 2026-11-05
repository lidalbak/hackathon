"""End to end without hardware: Claude Code hook -> bridge -> fake board -> button -> hook output."""

import io
import json
import threading
import time

import pytest

from agent_controller import claude
from agent_controller.cli import hook_claude
from agent_controller.controller import Controller
from agent_controller.events import Agent, AgentEvent, SessionState
from agent_controller.server import make_server


class FakeBoard:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def call(self, key, value="", *, timeout=2.0):
        with self.lock:
            self.calls.append((key, value))

    def last(self, key):
        with self.lock:
            return next((v for k, v in reversed(self.calls) if k == key), None)


def permission_payload(command="mkdir buddy-test", session="s1"):
    return {
        "session_id": session,
        "cwd": "/Users/benjamin/testfolder_hackathon",
        "permission_mode": "default",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "Create buddy-test directory"},
    }


@pytest.fixture
def bridge():
    board = FakeBoard()
    controller = Controller(board)
    server = make_server(controller, port=0, wait_seconds=2)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield board, controller, port
    server.shutdown()


def run_hook(payload, port, wait=2):
    out = io.StringIO()
    hook_claude(port, wait, stdin=io.StringIO(json.dumps(payload)), stdout=out)
    return out.getvalue().strip()


def press_when_waiting(controller, button, board):
    def press():
        for _ in range(200):
            if board.last("show_session") and board.last("show_session")["state"] == "needs_input":
                controller.on_button(button)
                return
            time.sleep(0.01)
    threading.Thread(target=press, daemon=True).start()


def test_approve_button_allows_the_request(bridge):
    board, controller, port = bridge
    press_when_waiting(controller, "approve", board)
    out = run_hook(permission_payload(), port)
    assert json.loads(out) == {
        "hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}
    }
    shown = [v for k, v in board.calls if k == "show_session"]
    assert shown[0] == {
        "agent": "Claude Code",
        "project": "testfolder_hackathon",
        "state": "needs_input",
        "summary": "Run: mkdir buddy-test",
        "pos": 1,
        "total": 1,
        "focus": 0,
    }
    assert ("speaker_play_rtttl", "chirp:d=16,o=6,b=180:c,e,g") in board.calls


def test_deny_button_denies_with_a_message(bridge):
    board, controller, port = bridge
    press_when_waiting(controller, "deny", board)
    out = json.loads(run_hook(permission_payload(), port))
    decision = out["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny" and "Agent Controller" in decision["message"]


def test_no_press_prints_nothing_so_the_app_asks(bridge):
    board, controller, port = bridge
    assert run_hook(permission_payload(), port) == ""
    assert board.last("show_session")["summary"].startswith("Answer in the app")


def test_works_twice_in_a_row(bridge):
    board, controller, port = bridge
    for button, behavior in [("deny", "deny"), ("approve", "allow")]:
        press_when_waiting(controller, button, board)
        out = json.loads(run_hook(permission_payload(), port))
        assert out["hookSpecificOutput"]["decision"]["behavior"] == behavior
        board.calls.clear()


def test_hook_without_bridge_stays_silent():
    assert run_hook(permission_payload(), port=1, wait=0) == ""  # nothing listens on port 1


def test_status_events_reach_the_screen(bridge):
    board, controller, port = bridge
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/x/demo", "prompt": "Make a README"}, port)
    assert board.last("show_session")["state"] == "working"
    run_hook({"hook_event_name": "Stop", "session_id": "s1", "cwd": "/x/demo"}, port)
    assert board.last("show_session")["state"] == "done"
    assert board.last("status_pixel") == {"index": 0, "r": 0, "g": 255, "b": 0}
    run_hook({"hook_event_name": "SessionEnd", "session_id": "s1", "cwd": "/x/demo"}, port)
    assert board.calls[-1][0] in ("clear_session", "status_pixel")
    assert ("clear_session", "") in board.calls


def test_button_answers_the_request_on_screen_first():
    board = FakeBoard()
    controller = Controller(board)
    results = {}

    def ask(session, command):
        results[session] = controller.request_permission(
            AgentEvent(Agent.CLAUDE_CODE, session, SessionState.NEEDS_INPUT, summary=command), timeout=2
        )

    first = threading.Thread(target=ask, args=("old", "Run: a"))
    first.start()
    time.sleep(0.05)
    second = threading.Thread(target=ask, args=("new", "Run: b"))
    second.start()
    time.sleep(0.05)
    assert board.last("show_session")["summary"] == "Run: a"  # oldest waiting is on screen
    controller.on_button("approve")
    first.join()
    assert results["old"] == "allow" and "new" not in results
    controller.on_button("deny")
    second.join()
    assert results["new"] == "deny"


def test_settings_snippet_points_at_the_hook():
    hooks = claude.settings_snippet("/venv/bin/agent-controller", 60)["hooks"]
    entry = hooks["PermissionRequest"][0]
    assert entry["matcher"] == "*"
    assert entry["hooks"][0] == {"type": "command", "command": "/venv/bin/agent-controller hook claude", "timeout": 75}
    assert set(hooks) == {"PermissionRequest", "PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SessionStart", "SessionEnd"}


def test_knob_moves_through_waiting_requests_and_buttons_answer_the_shown_one():
    board = FakeBoard()
    controller = Controller(board)
    results = {}

    def ask(session, command):
        results[session] = controller.request_permission(
            AgentEvent(Agent.CLAUDE_CODE, session, SessionState.NEEDS_INPUT, summary=command, project=session), timeout=3
        )

    threads = []
    for session, command in [("a", "Run: one"), ("b", "Run: two"), ("c", "Run: three")]:
        t = threading.Thread(target=ask, args=(session, command))
        t.start()
        threads.append(t)
        time.sleep(0.05)

    shown = board.last("show_session")
    assert (shown["summary"], shown["pos"], shown["total"], shown["focus"]) == ("Run: one", 1, 3, 0)

    controller.on_knob(1)  # one click clockwise
    shown = board.last("show_session")
    assert (shown["summary"], shown["pos"], shown["focus"]) == ("Run: two", 2, 1)

    controller.on_knob(0)  # one click back
    assert board.last("show_session")["summary"] == "Run: one"
    controller.on_knob(-1)  # back past the top wraps to the end
    assert board.last("show_session")["summary"] == "Run: three"

    controller.on_button("deny")  # answers the one on screen
    threads[2].join()
    assert results == {"c": "deny"}
    shown = board.last("show_session")
    assert (shown["summary"], shown["focus"]) == ("Run: one", 0)  # back to the top

    controller.on_knob(0)  # two clicks clockwise
    controller.on_knob(1)
    controller.on_knob_press()  # push: back to the top
    assert board.last("show_session")["summary"] == "Run: one"

    controller.on_button("approve")
    threads[0].join()
    controller.on_button("approve")
    threads[1].join()
    assert results == {"c": "deny", "a": "allow", "b": "allow"}
