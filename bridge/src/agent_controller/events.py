"""Shared session model.

Every agent integration turns its own hook payloads into an ``AgentEvent``.
Everything after that (ranking, the board) only sees this model, so adding an
agent never touches the rest of the bridge.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from time import time


class Agent(str, Enum):
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"


class SessionState(str, Enum):
    """States in priority order: the first ones need the human."""

    NEEDS_INPUT = "needs_input"
    ERROR = "error"
    DONE = "done"
    WORKING = "working"
    IDLE = "idle"


# Light-grid colour per state, as the board's status_pixel action takes it.
# The grid caps brightness at 20 %, so full values are fine.
STATE_COLORS: dict[SessionState, dict[str, int]] = {
    SessionState.NEEDS_INPUT: {"r": 255, "g": 140, "b": 0},  # amber
    SessionState.ERROR: {"r": 255, "g": 0, "b": 0},  # red
    SessionState.DONE: {"r": 0, "g": 255, "b": 0},  # green
    SessionState.WORKING: {"r": 0, "g": 80, "b": 255},  # blue
    SessionState.IDLE: {"r": 0, "g": 0, "b": 0},  # off
}

_PRIORITY = {state: rank for rank, state in enumerate(SessionState)}


@dataclass(frozen=True)
class AgentEvent:
    """One normalised event from any agent's hook."""

    agent: Agent
    session_id: str
    state: SessionState
    summary: str = ""  # what the agent wants, in one line, for the screen
    project: str = ""  # working directory or repo name, to tell sessions apart
    at: float = field(default_factory=time)


@dataclass
class Session:
    agent: Agent
    session_id: str
    state: SessionState = SessionState.IDLE
    summary: str = ""
    project: str = ""
    since: float = field(default_factory=time)  # when the current state began

    def apply(self, event: AgentEvent) -> None:
        if event.state != self.state:
            self.since = event.at
        self.state = event.state
        self.summary = event.summary or self.summary
        self.project = event.project or self.project


def rank_queue(sessions: list[Session]) -> list[Session]:
    """What needs the human first; within a state, whoever has waited longest."""
    return sorted(sessions, key=lambda s: (_PRIORITY[s.state], s.since))


AGENT_NAMES = {Agent.CLAUDE_CODE: "Claude Code", Agent.CODEX: "Codex"}

# The screen font is plain ASCII, and the board's JSON reader does not decode
# \u escapes, so every string sent to the screen is folded to ASCII first.
_FOLD = str.maketrans({"æ": "ae", "Æ": "Ae", "ø": "o", "Ø": "O", "å": "a", "Å": "A"})
SUMMARY_MAX = 160  # about four lines on the 160x80 screen


def to_ascii(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.translate(_FOLD))
    return folded.encode("ascii", "ignore").decode("ascii")


def screen_payload(session: Session, pos: int = 0, total: int = 0) -> dict[str, object]:
    """Value for the board's show_session action: which agent, where, and its state."""
    summary = to_ascii(" ".join(session.summary.split()))
    if len(summary) > SUMMARY_MAX:
        summary = summary[: SUMMARY_MAX - 2] + ".."
    return {
        "agent": AGENT_NAMES[session.agent],
        "project": to_ascii(session.project),
        "state": session.state.value,
        "summary": summary,
        "pos": pos,
        "total": total,
    }
