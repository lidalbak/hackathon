"""End to end without hardware: Claude Code hook -> bridge -> fake board -> knob/buttons/voice -> hook output."""

import base64
import io
import json
import threading
import time

import pytest

from agent_controller import claude
from agent_controller.cli import hook_claude
from agent_controller.controller import Controller, wait_until
from agent_controller.events import Agent, AgentEvent, SessionState
from agent_controller.server import Waits, make_server

WAITS = Waits(permission=2, choice=3, text_window=1, text_answer=3)


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


def fake_transcriber(pcm: bytes) -> str:
    return "Use Postgres, not SQLite" if pcm else ""


def speak(controller, seconds=0.5):
    """Hold the knob, send audio, release."""
    controller.on_ptt(True)
    samples = b"\x01\x00" * int(16000 * seconds)
    for i in range(0, len(samples), 512):
        controller.on_pcm(base64.b64encode(samples[i:i + 512]).decode())
    controller.on_ptt(False)


@pytest.fixture
def bridge():
    board = FakeBoard()
    controller = Controller(board, fake_transcriber)
    server = make_server(controller, port=0, waits=WAITS)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield board, controller, port
    server.shutdown()


def run_hook(payload, port):
    out = io.StringIO()
    hook_claude(port, WAITS, stdin=io.StringIO(json.dumps(payload)), stdout=out)
    return out.getvalue().strip()


def run_hook_async(payload, port):
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("out", run_hook(payload, port)), daemon=True)
    thread.start()
    return thread, result


def shown(board, key="summary"):
    payload = board.last("show_session") or {}
    return payload.get(key)


def permission_payload(command="mkdir buddy-test", session="s1"):
    return {
        "session_id": session,
        "cwd": "/Users/benjamin/testfolder_hackathon",
        "permission_mode": "default",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "Create buddy-test directory"},
    }


QUESTIONS = [
    {"question": "Which framework?", "header": "Framework", "multiSelect": False,
     "options": [{"label": "React", "description": "Component library"}, {"label": "Vue", "description": "Progressive"}]},
    {"question": "Which database?", "header": "Database", "multiSelect": False,
     "options": [{"label": "SQLite", "description": ""}, {"label": "Postgres", "description": ""}]},
]


def choice_payload(questions=QUESTIONS):
    return {"session_id": "q1", "cwd": "/x/demo", "hook_event_name": "PermissionRequest",
            "tool_name": "AskUserQuestion", "tool_input": {"questions": questions}}


# ---------------- approve / deny ----------------

def press_when_waiting(controller, button, board):
    def press():
        if wait_until(lambda: shown(board, "state") == "needs_input", 2):
            controller.on_button(button)
    threading.Thread(target=press, daemon=True).start()


def test_approve_button_allows_the_request(bridge):
    board, controller, port = bridge
    press_when_waiting(controller, "approve", board)
    out = run_hook(permission_payload(), port)
    assert json.loads(out) == {
        "hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}
    }
    first = next(v for k, v in board.calls if k == "show_session")
    assert first == {"agent": "Claude Code", "project": "testfolder_hackathon", "state": "needs_input",
                     "summary": "Run: mkdir buddy-test", "pos": 1, "total": 1, "focus": 0}
    assert ("speaker_play_rtttl", "chirp:d=16,o=6,b=180:c,e,g") in board.calls


def test_deny_button_denies_with_a_message(bridge):
    board, controller, port = bridge
    press_when_waiting(controller, "deny", board)
    decision = json.loads(run_hook(permission_payload(), port))["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny" and "Agent Controller" in decision["message"]


def test_no_press_prints_nothing_so_the_app_asks(bridge):
    board, controller, port = bridge
    assert run_hook(permission_payload(), port) == ""
    assert shown(board).startswith("Answer in the app")


def test_works_twice_in_a_row(bridge):
    board, controller, port = bridge
    for button, behavior in [("deny", "deny"), ("approve", "allow")]:
        press_when_waiting(controller, button, board)
        out = json.loads(run_hook(permission_payload(), port))
        assert out["hookSpecificOutput"]["decision"]["behavior"] == behavior
        board.calls.clear()


def test_hook_without_bridge_stays_silent():
    out = io.StringIO()
    hook_claude(1, Waits(0, 0, 0, 0), stdin=io.StringIO(json.dumps(permission_payload())), stdout=out)
    assert out.getvalue() == ""


def test_plan_approval_is_a_normal_request(bridge):
    board, controller, port = bridge
    press_when_waiting(controller, "approve", board)
    payload = {"hook_event_name": "PermissionRequest", "session_id": "p1", "cwd": "/x/demo",
               "tool_name": "ExitPlanMode", "tool_input": {"plan": "1. do it"}}
    assert json.loads(run_hook(payload, port))["hookSpecificOutput"]["decision"]["behavior"] == "allow"


# ---------------- status and queue ----------------

def test_status_events_reach_the_screen(bridge):
    board, controller, port = bridge
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/x/demo", "prompt": "Make a README"}, port)
    assert shown(board, "state") == "working"
    run_hook({"hook_event_name": "Stop", "session_id": "s1", "cwd": "/x/demo", "last_assistant_message": "Done."}, port)
    assert shown(board, "state") == "done"
    assert board.last("status_pixel") == {"index": 0, "r": 0, "g": 255, "b": 0}
    run_hook({"hook_event_name": "SessionEnd", "session_id": "s1", "cwd": "/x/demo"}, port)
    assert ("clear_session", "") in board.calls


def test_session_start_asks_claude_to_use_multiple_choice(bridge):
    board, controller, port = bridge
    out = json.loads(run_hook({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/x/demo"}, port))
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "AskUserQuestion" in out["hookSpecificOutput"]["additionalContext"]


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

    assert (shown(board), shown(board, "pos"), shown(board, "total"), shown(board, "focus")) == ("Run: one", 1, 3, 0)
    controller.on_knob(1)
    assert (shown(board), shown(board, "pos"), shown(board, "focus")) == ("Run: two", 2, 1)
    controller.on_knob(0)
    assert shown(board) == "Run: one"
    controller.on_knob(-1)  # back past the top wraps to the end
    assert shown(board) == "Run: three"

    controller.on_button("deny")
    threads[2].join()
    assert results == {"c": "deny"}
    assert (shown(board), shown(board, "focus")) == ("Run: one", 0)

    controller.on_knob(0)
    controller.on_knob(1)
    controller.on_knob_press()  # push: back to the top (after the push-to-talk delay)
    assert wait_until(lambda: shown(board) == "Run: one", 2)

    controller.on_button("approve")
    threads[0].join()
    controller.on_button("approve")
    threads[1].join()
    assert results == {"c": "deny", "a": "allow", "b": "allow"}


def test_holding_the_knob_is_not_back_to_top():
    board = FakeBoard()
    controller = Controller(board, fake_transcriber)
    for session in ("a", "b"):
        controller.handle_event(AgentEvent(Agent.CLAUDE_CODE, session, SessionState.WORKING, summary=session))
    controller.on_knob(1)
    assert shown(board) == "b"
    controller.on_knob_press()
    controller.on_ptt(True)  # the press became push-to-talk
    controller.on_ptt(False)
    time.sleep(0.8)
    assert shown(board) == "b"  # still where the user was


# ---------------- multiple-choice questions ----------------

def test_knob_picks_options_for_each_question(bridge):
    board, controller, port = bridge
    thread, result = run_hook_async(choice_payload(), port)
    assert wait_until(lambda: shown(board, "project") == "Framework", 2)
    assert (shown(board), shown(board, "pos"), shown(board, "total"), shown(board, "hint")) == (
        "> React: Component library", 1, 2, "L pick R app Hold: talk")
    controller.on_knob(1)
    assert shown(board) == "> Vue: Progressive"
    controller.on_button("approve")  # Vue
    assert wait_until(lambda: shown(board, "project") == "Database", 2)
    controller.on_knob(2)
    controller.on_button("approve")  # Postgres
    thread.join(5)
    decision = json.loads(result["out"])["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "allow"
    assert decision["updatedInput"]["questions"] == QUESTIONS
    assert decision["updatedInput"]["answers"] == {"Which framework?": "Vue", "Which database?": "Postgres"}


def test_voice_answer_to_a_question_needs_confirmation(bridge):
    board, controller, port = bridge
    thread, result = run_hook_async(choice_payload(QUESTIONS[1:]), port)
    assert wait_until(lambda: shown(board, "project") == "Database", 2)
    speak(controller)
    assert wait_until(lambda: (shown(board) or "").startswith("Heard:"), 2)
    assert shown(board, "hint") == "L send   R redo"
    controller.on_button("deny")  # redo: throw the transcript away
    assert wait_until(lambda: shown(board) == "> SQLite", 2)
    speak(controller)
    assert wait_until(lambda: (shown(board) or "").startswith("Heard:"), 2)
    controller.on_button("approve")  # send
    thread.join(5)
    answers = json.loads(result["out"])["hookSpecificOutput"]["decision"]["updatedInput"]["answers"]
    assert answers == {"Which database?": "Use Postgres, not SQLite"}


def test_right_button_leaves_the_question_to_the_app(bridge):
    board, controller, port = bridge
    thread, result = run_hook_async(choice_payload(), port)
    assert wait_until(lambda: shown(board, "project") == "Framework", 2)
    controller.on_button("deny")
    thread.join(5)
    assert result["out"] == ""


# ---------------- plain-text questions ----------------

def stop_payload(message="I can do it two ways. Should I use Postgres or SQLite?"):
    return {"hook_event_name": "Stop", "session_id": "t1", "cwd": "/x/demo", "stop_hook_active": False,
            "last_assistant_message": message}


def test_spoken_answer_to_a_plain_text_question_continues_claude(bridge):
    board, controller, port = bridge
    thread, result = run_hook_async(stop_payload(), port)
    assert wait_until(lambda: shown(board, "hint") == "Hold knob to answer", 2)
    assert shown(board) == "Should I use Postgres or SQLite?"
    speak(controller)
    assert wait_until(lambda: (shown(board) or "").startswith("Heard:"), 2)
    controller.on_button("approve")
    thread.join(5)
    assert json.loads(result["out"]) == {
        "decision": "block",
        "reason": "The user answered by voice from the Agent Controller: Use Postgres, not SQLite",
    }


def test_plain_text_question_ends_the_turn_if_nobody_talks(bridge):
    board, controller, port = bridge
    started = time.monotonic()
    assert run_hook(stop_payload(), port) == ""
    assert time.monotonic() - started < 2.5  # only the short window
    assert shown(board, "state") == "done"


def test_finished_turn_without_a_question_does_not_wait(bridge):
    board, controller, port = bridge
    started = time.monotonic()
    assert run_hook(stop_payload("All done. The tests pass."), port) == ""
    assert time.monotonic() - started < 0.5


def test_no_waiting_when_claude_is_already_continuing(bridge):
    board, controller, port = bridge
    payload = stop_payload() | {"stop_hook_active": True}
    started = time.monotonic()
    assert run_hook(payload, port) == ""
    assert time.monotonic() - started < 0.5


# ---------------- settings ----------------

def test_install_hooks_keeps_other_settings_and_replaces_ours(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "model": "opus",
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "say done"}]},
                     {"hooks": [{"type": "command", "command": "/old/agent-controller hook claude", "timeout": 5}]}],
        },
    }))
    snippet = claude.settings_snippet("/venv/bin/agent-controller", 60, 120, 68)
    backup = claude.install_hooks(settings, snippet)
    claude.install_hooks(settings, snippet)  # twice: still one entry of ours
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    stop = data["hooks"]["Stop"]
    assert stop[0]["hooks"][0]["command"] == "say done"
    assert [g["hooks"][0]["command"] for g in stop[1:]] == ["/venv/bin/agent-controller hook claude"]
    assert stop[1]["hooks"][0]["timeout"] == 83
    assert data["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] == 135
    assert backup.exists() and "/old/agent-controller" in backup.read_text()
