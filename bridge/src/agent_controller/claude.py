"""Claude Code hook payloads -> AgentEvent, and decisions -> hook output.

Claude Code runs a hook command with one JSON object on stdin. Reference:
https://code.claude.com/docs/en/hooks
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from agent_controller.events import Agent, AgentEvent, SessionState

PERMISSION = "PermissionRequest"
STOP = "Stop"
START = "SessionStart"
ENDED = "SessionEnd"
HOOK_MARK = "agent-controller hook claude"

# Tools where "allow" alone is not an answer: Claude Code needs the user's choice in
# updatedInput (https://code.claude.com/docs/en/hooks#allow-with-updatedinput).
CHOICE_TOOL = "AskUserQuestion"

# Told to Claude at the start of every session, so its questions can be answered
# from the controller (knob + voice) instead of only by typing.
SESSION_CONTEXT = (
    "The user may answer from a hardware controller with buttons, a knob and a microphone "
    "instead of the keyboard. When you need a decision, a clarification or any other answer "
    "from the user, ask with the AskUserQuestion tool and offer 2-4 short options, rather than "
    "ending your turn with a question in plain text."
)


def describe_tool(tool: str, tool_input: dict) -> str:
    """One line for the screen: what the agent wants to do."""
    tool_input = tool_input or {}
    if tool == "Bash":
        return "Run: " + str(tool_input.get("command", "")).strip()
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        verb = "Write" if tool == "Write" else "Edit"
        return f"{verb}: {os.path.basename(str(path)) or path}"
    if tool == CHOICE_TOOL:
        questions = tool_input.get("questions") or [{}]
        return "Question: " + str(questions[0].get("question", ""))
    if tool == "ExitPlanMode":
        return "Plan ready: approve to start"
    if tool == "WebFetch":
        return "Fetch: " + str(tool_input.get("url", ""))
    if tool == "WebSearch":
        return "Search: " + str(tool_input.get("query", ""))
    if tool.startswith("mcp__"):
        return "Use " + tool.split("__", 2)[-1].replace("_", " ")
    return tool


def to_event(payload: dict) -> Optional[AgentEvent]:
    """Map a hook payload to an AgentEvent, or None for events we ignore."""
    name = payload.get("hook_event_name", "")
    session_id = str(payload.get("session_id") or "unknown")
    project = os.path.basename(str(payload.get("cwd") or "").rstrip("/"))
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}

    if name == PERMISSION:
        state, summary = SessionState.NEEDS_INPUT, describe_tool(tool, tool_input)
    elif name in ("PreToolUse", "PostToolUse"):
        state, summary = SessionState.WORKING, describe_tool(tool, tool_input)
    elif name == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "").strip().splitlines()
        state, summary = SessionState.WORKING, (prompt[0] if prompt else "Working")
    elif name == STOP:
        question = trailing_question(str(payload.get("last_assistant_message") or ""))
        state, summary = SessionState.DONE, (question or "Finished, your turn")
    elif name == START:
        state, summary = SessionState.IDLE, "Session started"
    elif name == ENDED:
        state, summary = SessionState.IDLE, ""
    else:
        return None
    return AgentEvent(Agent.CLAUDE_CODE, session_id, state, summary=summary, project=project)


def trailing_question(message: str) -> str:
    """The question Claude ended its turn with, or "" if it did not end with one."""
    tail = message.strip()[-400:]
    if "?" not in tail[-120:]:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", tail)
    asked = [s for s in sentences if s.strip().endswith("?")]
    return " ".join(asked[-1].split()) if asked else ""


def decision_output(decision: Optional[str]) -> str:
    """stdout for a PermissionRequest hook. Empty = no decision: the normal prompt runs."""
    if decision not in ("allow", "deny"):
        return ""
    body: dict = {"behavior": decision}
    if decision == "deny":
        body["message"] = "Denied from the Agent Controller."
    return json.dumps({"hookSpecificOutput": {"hookEventName": PERMISSION, "decision": body}})


def choice_output(tool_input: dict, answers: Optional[dict]) -> str:
    """stdout for AskUserQuestion: allow, with the answers in updatedInput."""
    if not answers:
        return ""
    updated = dict(tool_input or {})
    updated["answers"] = answers
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": PERMISSION, "decision": {"behavior": "allow", "updatedInput": updated}}}
    )


def text_output(text: Optional[str]) -> str:
    """stdout for a Stop hook: keep Claude going with the user's spoken answer."""
    if not text:
        return ""
    return json.dumps({"decision": "block", "reason": f"The user answered by voice from the Agent Controller: {text}"})


def session_start_output() -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": START, "additionalContext": SESSION_CONTEXT}})


def settings_snippet(command: str, permission_wait: int, choice_wait: int, text_wait: int) -> dict:
    """The "hooks" block for ~/.claude/settings.json."""

    def entry(timeout: int, matcher: Optional[str] = None) -> list[dict]:
        group: dict = {"hooks": [{"type": "command", "command": f"{command} hook claude", "timeout": timeout}]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        return [group]

    return {
        "hooks": {
            "PermissionRequest": entry(max(permission_wait, choice_wait) + 15, "*"),
            "PreToolUse": entry(5, "*"),
            "PostToolUse": entry(5, "*"),
            "UserPromptSubmit": entry(5),
            "Stop": entry(text_wait + 15),
            "SessionStart": entry(5),
            "SessionEnd": entry(5),
        }
    }


def install_hooks(settings_path: Path, snippet: dict) -> Path:
    """Put our hooks into settings.json: keep everything else, replace only our own entries.

    Writes a timestamped backup first and returns its path.
    """
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = settings_path.with_name(f"{settings_path.name}.backup-{stamp}")
    n = 1
    while backup.exists():  # never overwrite an earlier backup
        n += 1
        backup = settings_path.with_name(f"{settings_path.name}.backup-{stamp}-{n}")
    current: dict = {}
    if settings_path.exists():
        shutil.copy2(settings_path, backup)
        text = settings_path.read_text().strip()
        current = json.loads(text) if text else {}
    hooks = current.setdefault("hooks", {})
    for event, groups in snippet["hooks"].items():
        kept = [g for g in hooks.get(event, []) if HOOK_MARK not in json.dumps(g)]
        hooks[event] = kept + groups
    settings_path.write_text(json.dumps(current, indent=2) + "\n")
    return backup
