"""Claude Code hook payloads -> AgentEvent, and decisions -> hook output.

Claude Code runs a hook command with one JSON object on stdin. Reference:
https://code.claude.com/docs/en/hooks
"""

from __future__ import annotations

import json
import os
from typing import Optional

from agent_controller.events import Agent, AgentEvent, SessionState

PERMISSION = "PermissionRequest"
ENDED = "SessionEnd"

# Tools where "allow" is not an answer: Claude Code needs the user's choice in
# updatedInput (https://code.claude.com/docs/en/hooks#allow-with-updatedinput).
# The board shows them, but the answer is given in the app.
ANSWER_IN_APP = {"AskUserQuestion"}


def describe_tool(tool: str, tool_input: dict) -> str:
    """One line for the screen: what the agent wants to do."""
    tool_input = tool_input or {}
    if tool == "Bash":
        return "Run: " + str(tool_input.get("command", "")).strip()
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        verb = "Write" if tool == "Write" else "Edit"
        return f"{verb}: {os.path.basename(str(path)) or path}"
    if tool == "AskUserQuestion":
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
    elif name == "Stop":
        state, summary = SessionState.DONE, "Finished, your turn"
    elif name == "SessionStart":
        state, summary = SessionState.IDLE, "Session started"
    elif name == ENDED:
        state, summary = SessionState.IDLE, ""
    else:
        return None
    return AgentEvent(Agent.CLAUDE_CODE, session_id, state, summary=summary, project=project)


def decision_output(decision: Optional[str]) -> str:
    """stdout for a PermissionRequest hook. Empty = no decision: the normal prompt runs."""
    if decision not in ("allow", "deny"):
        return ""
    body: dict = {"behavior": decision}
    if decision == "deny":
        body["message"] = "Denied from the Agent Controller."
    return json.dumps({"hookSpecificOutput": {"hookEventName": PERMISSION, "decision": body}})


def settings_snippet(command: str, wait_seconds: int) -> dict:
    """The "hooks" block for ~/.claude/settings.json."""
    def entry(timeout: int, matcher: Optional[str] = None) -> list[dict]:
        group: dict = {"hooks": [{"type": "command", "command": f"{command} hook claude", "timeout": timeout}]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        return [group]

    return {
        "hooks": {
            "PermissionRequest": entry(wait_seconds + 15, "*"),
            "PreToolUse": entry(5, "*"),
            "PostToolUse": entry(5, "*"),
            "UserPromptSubmit": entry(5),
            "Stop": entry(5),
            "SessionStart": entry(5),
            "SessionEnd": entry(5),
        }
    }
