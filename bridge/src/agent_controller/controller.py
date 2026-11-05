"""The bridge's core: sessions, pending permission requests, and what the board shows.

Thread-safe. Hooks call ``handle_event`` and ``request_permission`` from the HTTP
server's threads; the board's button events call ``on_button`` from another.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from itertools import count
from typing import Optional, Protocol

from agent_controller.events import (
    STATE_COLORS,
    Agent,
    AgentEvent,
    Session,
    SessionState,
    rank_queue,
    screen_payload,
)

log = logging.getLogger(__name__)

LIGHT_SLOTS = 8  # light grid slots 0-7; slot 8 belongs to the Claude app over Bluetooth
CHIRP = "chirp:d=16,o=6,b=180:c,e,g"
APPROVED_TUNE = "ok:d=16,o=6,b=200:g"
DENIED_TUNE = "no:d=8,o=4,b=200:c"


class BoardLike(Protocol):
    def call(self, key: str, value: object = "", *, timeout: float = 2.0) -> None: ...


@dataclass
class _Pending:
    id: int
    key: tuple[Agent, str]
    done: threading.Event = field(default_factory=threading.Event)
    decision: Optional[str] = None  # "allow" | "deny"


class Controller:
    def __init__(self, board: Optional[BoardLike]) -> None:
        self._board = board
        self._lock = threading.RLock()
        self._board_lock = threading.Lock()
        self._sessions: dict[tuple[Agent, str], Session] = {}
        self._pending: dict[int, _Pending] = {}
        self._ids = count(1)
        self._slots: dict[tuple[Agent, str], int] = {}
        self._lit: dict[int, dict[str, int]] = {}
        self._shown: Optional[dict] = None
        self._shown_cleared = False

    # ---------- inputs ----------

    def handle_event(self, event: AgentEvent, *, ended: bool = False) -> None:
        """A status event from an agent. ``ended`` removes the session."""
        with self._lock:
            key = (event.agent, event.session_id)
            if ended:
                self._sessions.pop(key, None)
                slot = self._slots.pop(key, None)
                if slot is not None:
                    self._set_light(slot, STATE_COLORS[SessionState.IDLE])
            else:
                if self._has_pending(key) and event.state != SessionState.NEEDS_INPUT:
                    # A pending request still waits for the button; keep showing it.
                    return
                session = self._sessions.get(key)
                if session is None:
                    session = Session(event.agent, event.session_id, since=event.at)
                    self._sessions[key] = session
                session.apply(event)
            self._refresh()

    def request_permission(self, event: AgentEvent, timeout: float) -> Optional[str]:
        """Show a permission request and wait for a button press.

        Returns "allow", "deny", or None when nobody pressed within ``timeout``
        (the agent then falls back to its own prompt).
        """
        with self._lock:
            key = (event.agent, event.session_id)
            pending = _Pending(next(self._ids), key)
            self._pending[pending.id] = pending
            session = self._sessions.get(key)
            if session is None:
                session = Session(event.agent, event.session_id, since=event.at)
                self._sessions[key] = session
            session.apply(event)
            self._refresh()
            self._play(CHIRP)

        pending.done.wait(timeout)

        with self._lock:
            self._pending.pop(pending.id, None)
            session = self._sessions.get(key)
            if session is not None and not self._has_pending(key):
                if pending.decision == "allow":
                    session.apply(AgentEvent(event.agent, event.session_id, SessionState.WORKING, summary="Approved: " + session.summary))
                elif pending.decision == "deny":
                    session.apply(AgentEvent(event.agent, event.session_id, SessionState.IDLE, summary="Denied: " + session.summary))
                else:
                    session.apply(AgentEvent(event.agent, event.session_id, SessionState.NEEDS_INPUT, summary="Answer in the app: " + session.summary))
            self._refresh()
        return pending.decision

    def on_button(self, button: str) -> Optional[int]:
        """approve / deny pressed: answer the request on screen. Returns its id, or None."""
        with self._lock:
            pending = self._top_pending()
            if pending is None or pending.decision is not None:
                return None
            pending.decision = "allow" if button == "approve" else "deny"
            pending.done.set()
            self._play(APPROVED_TUNE if pending.decision == "allow" else DENIED_TUNE)
            return pending.id

    def start(self) -> None:
        """Draw the current state (the idle screen when nothing is running)."""
        with self._lock:
            self._refresh()

    def shutdown(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                pending.done.set()  # decision stays None: agents fall back to their own prompt
            self._call("clear_session")
            for slot in list(self._lit):
                self._set_light(slot, STATE_COLORS[SessionState.IDLE])

    # ---------- state ----------

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [screen_payload(s) | {"pending": self._has_pending((s.agent, s.session_id))} for s in self._queue()]

    def _queue(self) -> list[Session]:
        return rank_queue(list(self._sessions.values()))

    def _has_pending(self, key: tuple[Agent, str]) -> bool:
        return any(p.key == key and p.decision is None for p in self._pending.values())

    def _top_pending(self) -> Optional[_Pending]:
        queue = self._queue()
        if not queue:
            return None
        top = (queue[0].agent, queue[0].session_id)
        waiting = [p for p in self._pending.values() if p.key == top and p.decision is None]
        return min(waiting, key=lambda p: p.id) if waiting else None

    # ---------- board ----------

    def _refresh(self) -> None:
        queue = self._queue()
        # Screen: the top of the queue.
        if queue:
            payload = screen_payload(queue[0], 1, len(queue))
            if payload != self._shown:
                if self._call("show_session", payload):
                    self._shown, self._shown_cleared = payload, False
        elif not self._shown_cleared:
            if self._call("clear_session"):
                self._shown, self._shown_cleared = None, True
        # Lights: one slot per session, while slots last.
        for session in queue:
            key = (session.agent, session.session_id)
            slot = self._slots.get(key)
            if slot is None:
                free = [i for i in range(LIGHT_SLOTS) if i not in self._slots.values()]
                if not free:
                    continue
                slot = self._slots[key] = free[0]
            self._set_light(slot, STATE_COLORS[session.state])

    def _set_light(self, slot: int, color: dict[str, int]) -> None:
        if self._lit.get(slot) == color:
            return
        if self._call("status_pixel", {"index": slot, **color}):
            self._lit[slot] = color

    def _play(self, tune: str) -> None:
        self._call("speaker_play_rtttl", tune)

    def _call(self, key: str, value: object = "") -> bool:
        if self._board is None:
            return True
        with self._board_lock:
            try:
                self._board.call(key, value)
                return True
            except Exception as exc:  # the board must never take the bridge down
                log.warning("board %s failed: %s", key, exc)
                return False
