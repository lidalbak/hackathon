"""Answers guests who write in the Airbnb copy, as the host.

Polls the host inbox for the watched listing. When a guest's message is the newest in a
conversation, Claude writes the reply from the chat, the booking, the house rules, the
apartment facts and what the sensor reads now. Anything only the real host can handle
also goes to the owner. History from before the agent started is not answered, and only
guest messages are: the agent's own replies are the host's, so it never answers itself.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from stay_agent.channels import OwnerChannel, booking_from_conversation, replica_conversations, replica_messages
from stay_agent.harness import StayHarness
from stay_agent.models import OwnerAlert, SentMessage

log = logging.getLogger(__name__)

POLL_SECONDS = 2


class InboxWatcher:
    def __init__(
        self,
        harness: StayHarness,
        owner: OwnerChannel,
        send: Callable[[int, str], None],
        list_conversations: Optional[Callable[[], list[dict]]] = None,
        get_messages: Optional[Callable[[int], list[dict]]] = None,
    ):
        s = harness.settings
        self.harness, self.owner, self.send = harness, owner, send
        self.list_conversations = list_conversations or (lambda: replica_conversations(s))
        self.get_messages = get_messages or (lambda cid: replica_messages(s, cid))
        self.seen: Optional[dict[int, int]] = None  # conversation id -> last message id handled

    def start(self) -> None:
        threading.Thread(target=self._run, name="stay-agent-inbox", daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                self.poll()
            except Exception:  # the copy is down or restarting: try again
                log.warning("could not read the Airbnb inbox", exc_info=True)
            time.sleep(POLL_SECONDS)

    def poll(self) -> None:
        conversations = self.list_conversations()
        if self.seen is None:  # first look: what is there already was before our time
            self.seen = {c["id"]: (c.get("last_message") or {}).get("id", 0) for c in conversations}
            return
        for c in conversations:
            last = c.get("last_message") or {}
            if last.get("sender") == "guest" and last.get("id", 0) > self.seen.get(c["id"], 0):
                self.seen[c["id"]] = last["id"]
                self.harness.executor.submit(lambda c=c: self._answer(c))

    def _answer(self, c: dict) -> None:
        s = self.harness.settings
        guest = c["guest"]["name"]
        messages = self.get_messages(c["id"])
        transcript = [f"{m['sender']}: {m['text']}" for m in messages]
        asked = next((m["text"] for m in reversed(messages) if m["sender"] == "guest"), "")
        r = c.get("reservation") or {}
        stay = {k: r.get(k) for k in ("status", "check_in", "check_out", "guests")}
        booking = booking_from_conversation(c, s.tz, s.check_in_time, s.check_out_time)
        sensor = self.harness.sensor_now(str(c["id"])) if booking else None

        reply, author = self.harness.writer.guest_reply(guest, stay, transcript, sensor)
        self.send(c["id"], reply.text)
        now = datetime.now(s.tz)
        self.harness.note_sent(str(c["id"]), SentMessage(at=now, to="guest", text=reply.text, written_by=author))
        log.info("answered %s in conversation %s", guest, c["id"])

        if reply.needs_owner:
            alert = OwnerAlert(
                subject=f"{guest} needs you: {reply.owner_note}"[:70],
                text=f"{guest} wrote: \"{asked}\"\nI answered: \"{reply.text}\"\n{reply.owner_note}",
            )
            self.owner.send(alert)
            self.harness.note_sent(str(c["id"]), SentMessage(at=now, to="owner", text=f"{alert.subject}\n{alert.text}", written_by=author))
