from datetime import date, timedelta

from stay_agent import Inline, Settings, StayHarness, Writer
from stay_agent.channels import StaticBookings
from stay_agent.replies import InboxWatcher


class Inbox:
    """A fake Airbnb copy: one conversation the test adds messages to."""

    def __init__(self):
        today = date.today()
        self.messages = [{"id": 1, "sender": "guest", "text": "Hi! We just checked in"}]
        self.conv = {
            "id": 7,
            "guest": {"name": "Anna"},
            "my_role": "host",
            "listing": {"title": "Bright studio"},
            "reservation": {"status": "confirmed", "check_in": today.isoformat(),
                            "check_out": (today + timedelta(days=1)).isoformat(), "guests": 2},
        }
        self.sent, self.owner = [], []

    def conversations(self):
        return [{**self.conv, "last_message": self.messages[-1]}]

    def write(self, sender, text):
        self.messages.append({"id": len(self.messages) + 1, "sender": sender, "text": text})

    def send(self, cid, text):
        self.write("host", text)
        self.sent.append((cid, text))


def watcher(inbox):
    settings = Settings(use_claude=False, owner_name="Simen")
    harness = StayHarness(settings, Writer(settings), StaticBookings([], settings.tz), None, None, None, Inline())

    class Owner:
        def send(self, alert):
            inbox.owner.append(alert)

    return InboxWatcher(harness, Owner(), inbox.send, inbox.conversations, lambda cid: inbox.messages)


def test_history_is_not_answered_new_guest_messages_are_once():
    inbox = Inbox()
    w = watcher(inbox)
    w.poll()  # first look: the greeting was there before the agent started
    assert inbox.sent == []
    inbox.write("guest", "Where do we put the trash?")
    w.poll()
    w.poll()  # nothing new: no second answer, and the agent's own reply is not answered
    assert len(inbox.sent) == 1 and inbox.sent[0][0] == 7
    assert "Simen" in inbox.sent[0][1]


def test_host_messages_are_not_answered():
    inbox = Inbox()
    w = watcher(inbox)
    w.poll()
    inbox.write("host", "Welcome!")
    w.poll()
    assert inbox.sent == []


def test_an_unanswerable_message_also_goes_to_the_owner():
    # with Claude off, the template reply says "I'll get back to you" and flags the owner
    inbox = Inbox()
    w = watcher(inbox)
    w.poll()
    inbox.write("guest", "Can we get a refund for tonight?")
    w.poll()
    assert len(inbox.sent) == 1 and len(inbox.owner) == 1
    assert "refund" in inbox.owner[0].text
