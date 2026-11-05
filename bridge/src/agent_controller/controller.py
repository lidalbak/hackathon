"""The bridge's core: sessions, pending requests, and what the board shows.

Three kinds of request wait for the human:

- permission  approve / deny a tool call (left / right button)
- choice      pick an option for each of Claude's questions (turn the knob, left picks,
              hold the knob to say a free-text answer instead)
- text        answer a plain-text question by voice (hold the knob, left sends)

Thread-safe. Hooks call ``request_*`` from the HTTP server's threads; the board's
events call ``on_*`` from another thread.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass, field
from itertools import count
from typing import Callable, Optional, Protocol

from agent_controller.events import (
    STATE_COLORS,
    Agent,
    AgentEvent,
    Session,
    SessionState,
    rank_queue,
    screen_payload,
    to_ascii,
)

log = logging.getLogger(__name__)

LIGHT_SLOTS = 8  # light grid slots 0-7; slot 8 belongs to the Claude app over Bluetooth
CHIRP = "chirp:d=16,o=6,b=180:c,e,g"
APPROVED_TUNE = "ok:d=16,o=6,b=200:g"
DENIED_TUNE = "no:d=8,o=4,b=200:c"
KNOB_PRESS_DELAY = 0.6  # a press that becomes push-to-talk (held 0.4 s) is not "back to top"

Key = tuple[Agent, str]
Transcriber = Callable[[bytes], str]  # 16 kHz mono int16 PCM -> text


class BoardLike(Protocol):
    def call(self, key: str, value: object = "", *, timeout: float = 2.0) -> None: ...


@dataclass
class _Pending:
    id: int
    key: Key
    kind: str  # "permission" | "choice" | "text"
    done: threading.Event = field(default_factory=threading.Event)
    decision: Optional[str] = None  # permission: "allow" / "deny"
    questions: list[dict] = field(default_factory=list)  # choice
    q_index: int = 0
    option: int = 0
    answers: dict[str, str] = field(default_factory=dict)
    prompt: str = ""  # text: the question Claude asked
    heard: Optional[str] = None  # transcript waiting for "send" or "redo"
    text: Optional[str] = None  # text: the final answer
    engaged: threading.Event = field(default_factory=threading.Event)  # text: the user started answering

    @property
    def open(self) -> bool:
        return not self.done.is_set()


class Controller:
    def __init__(self, board: Optional[BoardLike], transcribe: Optional[Transcriber] = None) -> None:
        self._board = board
        self._transcribe = transcribe
        self._lock = threading.RLock()
        self._board_lock = threading.Lock()
        self._sessions: dict[Key, Session] = {}
        self._pending: dict[int, _Pending] = {}
        self._ids = count(1)
        self._slots: dict[Key, int] = {}
        self._lit: dict[int, dict[str, int]] = {}
        self._shown: Optional[dict] = None
        self._shown_cleared = False
        self._selected: Optional[Key] = None  # picked with the knob; None = top of queue
        self._knob_last = 0
        self._press_token = 0
        self._recording: Optional[_Pending] = None
        self._audio = bytearray()
        self._busy: Optional[str] = None  # e.g. "Transcribing..." shown instead of the summary

    # ================= requests from hooks =================

    def handle_event(self, event: AgentEvent, *, ended: bool = False) -> None:
        """A status event from an agent. ``ended`` removes the session."""
        with self._lock:
            key = (event.agent, event.session_id)
            if ended:
                self._sessions.pop(key, None)
                if self._selected == key:
                    self._selected = None
                slot = self._slots.pop(key, None)
                if slot is not None:
                    self._set_light(slot, STATE_COLORS[SessionState.IDLE])
            else:
                if self._open_for(key) and event.state != SessionState.NEEDS_INPUT:
                    return  # a request still waits for the human; keep showing it
                self._session(event).apply(event)
            self._refresh()

    def request_permission(self, event: AgentEvent, timeout: float) -> Optional[str]:
        """Approve / deny. Returns "allow", "deny", or None (nobody pressed: the app asks)."""
        pending = self._open(event, "permission")
        pending.done.wait(timeout)
        outcome = {"allow": "Approved: ", "deny": "Denied: "}.get(pending.decision or "", "Answer in the app: ")
        self._close(pending, SessionState.WORKING if pending.decision == "allow" else SessionState.NEEDS_INPUT if pending.decision is None else SessionState.IDLE, outcome)
        return pending.decision

    def request_choice(self, event: AgentEvent, questions: list[dict], timeout: float) -> Optional[dict[str, str]]:
        """Claude's multiple-choice questions. Returns {question: answer} or None (answer in the app)."""
        pending = self._open(event, "choice", questions=[q for q in questions if q.get("options")])
        if not pending.questions:
            self._close(pending, SessionState.NEEDS_INPUT, "Answer in the app: ")
            return None
        pending.done.wait(timeout)
        answered = pending.answers if len(pending.answers) == len(pending.questions) else None
        self._close(pending, SessionState.WORKING if answered else SessionState.NEEDS_INPUT, "Answered: " if answered else "Answer in the app: ")
        return answered

    def request_text(self, event: AgentEvent, prompt: str, window: float, max_wait: float) -> Optional[str]:
        """A plain-text question. Waits ``window`` s for the user to start talking, then up to
        ``max_wait`` s for the answer. Returns the text, or None (the turn ends as usual)."""
        pending = self._open(event, "text", prompt=prompt)
        if pending.engaged.wait(window):
            pending.done.wait(max_wait)
        if not pending.text:
            pending.done.set()
        self._close(pending, SessionState.WORKING if pending.text else SessionState.DONE, "Answered: " if pending.text else "")
        return pending.text

    # ================= board input =================

    def on_button(self, button: str) -> Optional[int]:
        """approve / deny pressed: act on the request on screen. Returns its id, or None."""
        with self._lock:
            pending = self._shown_pending()
            if pending is None or self._recording is not None or self._busy:
                return None
            yes = button == "approve"
            if pending.kind == "permission":
                pending.decision = "allow" if yes else "deny"
                pending.done.set()
            elif pending.heard is not None:  # a transcript waits: send it, or throw it away
                if yes:
                    self._accept_answer(pending, pending.heard)
                pending.heard = None
            elif pending.kind == "choice" and yes:
                options = pending.questions[pending.q_index]["options"]
                self._accept_answer(pending, str(options[pending.option].get("label", "")))
            else:  # right button on a question: answer it in the app instead
                pending.done.set()
            if pending.done.is_set():
                self._selected = None  # back to the top of the queue for the next one
            self._play(APPROVED_TUNE if yes else DENIED_TUNE)
            self._refresh()
            return pending.id

    def on_knob(self, position: int) -> None:
        """The knob turned: next option of a question on screen, else next session."""
        with self._lock:
            step = (position > self._knob_last) - (position < self._knob_last)
            self._knob_last = position
            if not step:
                return
            pending = self._shown_pending()
            if pending is not None and pending.kind == "choice" and pending.heard is None:
                options = pending.questions[pending.q_index]["options"]
                pending.option = (pending.option + step) % len(options)
                self._refresh()
                return
            queue = self._queue()
            if not queue:
                return
            nxt = queue[(self._shown_index(queue) + step) % len(queue)]
            self._selected = (nxt.agent, nxt.session_id)
            self._refresh()

    def on_knob_press(self) -> None:
        """Knob pushed: back to what needs you most, unless it turns into push-to-talk."""
        with self._lock:
            self._press_token += 1
            token = self._press_token
        timer = threading.Timer(KNOB_PRESS_DELAY, self._back_to_top, args=(token,))
        timer.daemon = True
        timer.start()

    def on_ptt(self, talking: bool) -> None:
        """Knob held (push-to-talk) or released."""
        with self._lock:
            self._press_token += 1  # this press was push-to-talk, not "back to top"
            if talking:
                pending = self._shown_pending()
                if pending is None or pending.kind == "permission":
                    self._recording = None
                    return
                self._recording, self._audio = pending, bytearray()
                pending.engaged.set()
                pending.heard = None
                return
            pending, audio = self._recording, bytes(self._audio)
            self._recording, self._audio = None, bytearray()
            if pending is None or not pending.open:
                return
            if self._transcribe is None:
                self._busy = None
                pending.heard = None
                self._show_note(pending, "Voice is not set up on the bridge")
                return
            self._busy = "Transcribing..."
            self._refresh()
        threading.Thread(target=self._finish_transcript, args=(pending, audio), daemon=True).start()

    def on_pcm(self, chunk: str) -> None:
        """Audio from the board's microphone while the knob is held (base64 PCM)."""
        with self._lock:
            if self._recording is not None:
                try:
                    self._audio += base64.b64decode(chunk)
                except ValueError:
                    pass

    # ================= lifecycle =================

    def start(self) -> None:
        """Draw the current state (the idle screen when nothing is running)."""
        with self._lock:
            self._refresh()

    def shutdown(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                pending.engaged.set()
                pending.done.set()  # no answer: agents fall back to their own prompts
            self._call("clear_session")
            for slot in list(self._lit):
                self._set_light(slot, STATE_COLORS[SessionState.IDLE])

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [screen_payload(s) | {"waiting": self._open_for((s.agent, s.session_id)) is not None} for s in self._queue()]

    # ================= internals =================

    def _session(self, event: AgentEvent) -> Session:
        key = (event.agent, event.session_id)
        session = self._sessions.get(key)
        if session is None:
            session = self._sessions[key] = Session(event.agent, event.session_id, since=event.at)
        return session

    def _open(self, event: AgentEvent, kind: str, **fields) -> _Pending:
        with self._lock:
            key = (event.agent, event.session_id)
            pending = _Pending(next(self._ids), key, kind, **fields)
            self._pending[pending.id] = pending
            self._session(event).apply(event)
            picked = self._sessions.get(self._selected) if self._selected else None
            if picked is None or picked.state != SessionState.NEEDS_INPUT:
                self._selected = None  # jump to the top, where a new request lands
            self._refresh()
            self._play(CHIRP)
            return pending

    def _close(self, pending: _Pending, state: SessionState, prefix: str) -> None:
        with self._lock:
            self._pending.pop(pending.id, None)
            if self._recording is pending:
                self._recording = None
            session = self._sessions.get(pending.key)
            if session is not None and self._open_for(pending.key) is None:
                summary = (prefix + session.summary) if prefix else session.summary
                session.apply(AgentEvent(pending.key[0], pending.key[1], state, summary=summary))
            self._refresh()

    def _accept_answer(self, pending: _Pending, answer: str) -> None:
        if pending.kind == "text":
            pending.text = answer
            pending.done.set()
            return
        question = pending.questions[pending.q_index]
        pending.answers[str(question.get("question", ""))] = answer
        pending.q_index += 1
        pending.option = 0
        if pending.q_index >= len(pending.questions):
            pending.done.set()

    def _finish_transcript(self, pending: _Pending, audio: bytes) -> None:
        try:
            text = (self._transcribe(audio) if len(audio) > 3200 else "").strip()  # under 0.1 s: nothing said
        except Exception as exc:
            log.warning("transcription failed: %s", exc)
            text = ""
        with self._lock:
            self._busy = None
            if pending.open:
                pending.heard = text or None
                if not text:
                    self._show_note(pending, "Heard nothing. Hold the knob and try again")
                    return
            self._refresh()

    def _show_note(self, pending: _Pending, note: str) -> None:
        self._busy = None
        self._refresh(note=note)

    def _back_to_top(self, token: int) -> None:
        with self._lock:
            if token != self._press_token:
                return  # became push-to-talk
            self._selected = None
            self._refresh()

    def _queue(self) -> list[Session]:
        return rank_queue(list(self._sessions.values()))

    def _open_for(self, key: Key) -> Optional[_Pending]:
        waiting = [p for p in self._pending.values() if p.key == key and p.open]
        return min(waiting, key=lambda p: p.id) if waiting else None

    def _shown_index(self, queue: list[Session]) -> int:
        if self._selected is not None:
            for i, s in enumerate(queue):
                if (s.agent, s.session_id) == self._selected:
                    return i
            self._selected = None  # it left the queue
        return 0

    def _shown_pending(self) -> Optional[_Pending]:
        queue = self._queue()
        if not queue:
            return None
        shown = queue[self._shown_index(queue)]
        return self._open_for((shown.agent, shown.session_id))

    def _payload(self, session: Session, index: int, total: int, note: Optional[str]) -> dict:
        payload = screen_payload(session, index + 1, total)
        payload["focus"] = 1 if self._selected is not None else 0
        pending = self._open_for((session.agent, session.session_id))
        hint = ""
        if pending is not None and pending.kind != "permission":
            if self._busy:
                payload["summary"], hint = self._busy, "Please wait"
            elif pending.heard is not None:
                payload["summary"], hint = to_ascii(f'Heard: "{pending.heard}"')[:160], "L send   R redo"
            elif pending.kind == "choice":
                question = pending.questions[pending.q_index]
                options = question["options"]
                option = options[pending.option]
                label = str(option.get("label", ""))
                desc = str(option.get("description", ""))
                payload["project"] = to_ascii(str(question.get("header") or question.get("question", "")))[:40]
                payload["summary"] = to_ascii(f"> {label}" + (f": {desc}" if desc else ""))[:160]
                payload["pos"], payload["total"] = pending.option + 1, len(options)
                hint = "L pick R app Hold: talk"
            else:
                payload["summary"] = to_ascii(pending.prompt)[:160]
                hint = "Hold knob to answer"
        if note:
            payload["summary"], hint = to_ascii(note)[:160], "Hold knob to talk"
        if hint:
            payload["hint"] = hint
        return payload

    def _refresh(self, note: Optional[str] = None) -> None:
        queue = self._queue()
        if queue:
            index = self._shown_index(queue)
            payload = self._payload(queue[index], index, len(queue), note)
            if payload != self._shown and self._call("show_session", payload):
                self._shown, self._shown_cleared = payload, False
        elif not self._shown_cleared and self._call("clear_session"):
            self._shown, self._shown_cleared = None, True
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


def wait_until(predicate: Callable[[], bool], timeout: float, step: float = 0.01) -> bool:
    """Test helper: poll ``predicate`` until true or ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
