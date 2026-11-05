"""Where bookings come from and where messages go.

Each has a console version (prints, for the demo and tests) and an HTTP version: the
Airbnb copy in ``airbnb_chat/`` for bookings and guest messages (API in
docs/airbnb-chat.md), webhooks for the owner and the cleaner.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import date, datetime
from datetime import time as clock
from pathlib import Path
from typing import Optional, Protocol
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from stay_agent.config import Settings
from stay_agent.models import Booking, OwnerAlert

log = logging.getLogger(__name__)

_bookings = TypeAdapter(list[Booking])


def localize(b: Booking, tz: ZoneInfo) -> Booking:
    """Bookings may come without a timezone; they are in the apartment's."""
    fix = lambda dt: dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)  # noqa: E731
    return b.model_copy(update={"check_in": fix(b.check_in), "check_out": fix(b.check_out)})


def current_booking(bookings: list[Booking], now: datetime) -> Optional[Booking]:
    return next((b for b in bookings if b.check_in <= now < b.check_out), None)


def _http(method: str, url: str, body: Optional[dict] = None, token: str = "", timeout: float = 10) -> object:
    data = json.dumps(body, default=str).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


# ---- Bookings ---------------------------------------------------------------------


class Bookings(Protocol):
    def current(self, now: datetime) -> Optional[Booking]: ...


class StaticBookings:
    def __init__(self, bookings: list[Booking], tz: ZoneInfo):
        self.bookings = [localize(b, tz) for b in bookings]

    def current(self, now: datetime) -> Optional[Booking]:
        return current_booking(self.bookings, now)


class FileBookings:
    """A JSON list of bookings, re-read on every call so it can be edited while running."""

    def __init__(self, path: str, tz: ZoneInfo):
        self.path, self.tz = Path(path), tz

    def current(self, now: datetime) -> Optional[Booking]:
        bookings = [localize(b, self.tz) for b in _bookings.validate_json(self.path.read_text())]
        return current_booking(bookings, now)


def booking_from_conversation(c: dict, tz: ZoneInfo, check_in: clock, check_out: clock) -> Optional[Booking]:
    """A confirmed stay at one of the owner's listings, from an airbnb_chat conversation."""
    r = c.get("reservation") or {}
    if c.get("my_role") != "host" or r.get("status") != "confirmed" or not r.get("check_in") or not r.get("check_out"):
        return None
    return Booking(
        id=str(c["id"]),
        guest_name=c["guest"]["name"],
        guests=r.get("guests") or 1,
        check_in=datetime.combine(date.fromisoformat(r["check_in"]), check_in, tz),
        check_out=datetime.combine(date.fromisoformat(r["check_out"]), check_out, tz),
        listing=(c.get("listing") or {}).get("title", ""),
        code=r.get("code") or "",
    )


class ReplicaBookings:
    """Confirmed stays from the Airbnb copy: GET {replica}/api/conversations?role=host"""

    def __init__(self, s: Settings):
        self.s = s
        self.base_url = s.replica_url.rstrip("/")
        self._last: list[Booking] = []

    def current(self, now: datetime) -> Optional[Booking]:
        s = self.s
        try:
            data = _http("GET", f"{self.base_url}/api/conversations?role=host", token=s.replica_token)
            found = [booking_from_conversation(c, s.tz, s.check_in_time, s.check_out_time) for c in data["conversations"]]
            self._last = [b for b in found if b and s.listing_title.lower() in b.listing.lower()]
        except Exception:  # replica down: keep using the last list we had
            log.warning("could not fetch bookings from the replica; using the last list", exc_info=True)
        return current_booking(self._last, now)


def create_replica_stay(s: Settings, guest_name: str, guests: int, nights: int = 1) -> int:
    """For the demo: a confirmed stay from today, as if the guest just checked in. Returns its id."""
    conv = _http(
        "POST",
        f"{s.replica_url.rstrip('/')}/api/conversations",
        {
            "my_role": "host",
            "guest": {"name": guest_name},
            "host": {"name": s.owner_name},
            "listing": {"title": s.listing_title or s.property_name},
            "reservation": {"status": "confirmed", "check_in": 0, "check_out": nights, "guests": guests},
            "messages": [
                {"sender": "system", "text": "Reservation confirmed", "minutes_ago": 180},
                {"sender": "guest", "text": "Hi! We just checked in, the place looks great 😊", "minutes_ago": 5},
            ],
        },
        token=s.replica_token,
    )
    return int(conv["id"])


# ---- Messages -------------------------------------------------------------------


class GuestChannel(Protocol):
    def send(self, booking: Booking, text: str) -> None: ...


class OwnerChannel(Protocol):
    def send(self, alert: OwnerAlert) -> None: ...


class CleaningChannel(Protocol):
    def order(self, booking: Booking, property_name: str) -> None: ...


class ConsoleGuest:
    def send(self, booking: Booking, text: str) -> None:
        print(f"\n💬 To guest {booking.guest_name} (booking {booking.id}):\n   {text}\n", flush=True)


class ReplicaGuest:
    """Posts as the host in the stay's conversation, after a moment of "typing..."."""

    def __init__(self, s: Settings):
        self.base_url, self.token, self.typing = s.replica_url.rstrip("/"), s.replica_token, s.typing_seconds

    def send(self, booking: Booking, text: str) -> None:
        self.send_to(booking.id, text)

    def send_to(self, conversation_id: str | int, text: str) -> None:
        conv = f"{self.base_url}/api/conversations/{conversation_id}"
        if self.typing:
            _http("POST", f"{conv}/typing", {"sender": "host", "typing": True}, token=self.token)
            time.sleep(self.typing)
        _http("POST", f"{conv}/messages", {"sender": "host", "text": text}, token=self.token)


def replica_conversations(s: Settings) -> list[dict]:
    """The owner's host conversations at the watched listing, newest first."""
    data = _http("GET", f"{s.replica_url.rstrip('/')}/api/conversations?role=host", token=s.replica_token)
    title = s.listing_title.lower()
    return [c for c in data["conversations"] if title in (c.get("listing") or {}).get("title", "").lower()]


def replica_messages(s: Settings, conversation_id: int) -> list[dict]:
    data = _http("GET", f"{s.replica_url.rstrip('/')}/api/conversations/{conversation_id}/messages", token=s.replica_token)
    return data["messages"]


class ConsoleOwner:
    def send(self, alert: OwnerAlert) -> None:
        print(f"\n🚨 To owner: {alert.subject}\n   {alert.text}\n", flush=True)


class WebhookOwner:
    """Slack-style incoming webhook: {"text": ...}, plus the fields on their own."""

    def __init__(self, url: str):
        self.url = url

    def send(self, alert: OwnerAlert) -> None:
        _http("POST", self.url, {"text": f"*{alert.subject}*\n{alert.text}", "subject": alert.subject, "body": alert.text})


class ConsoleCleaning:
    def order(self, booking: Booking, property_name: str) -> None:
        print(
            f"\n🧹 Cleaning ordered for {property_name} after {booking.guest_name} "
            f"checks out {booking.check_out:%a %d %b %H:%M}\n",
            flush=True,
        )


class WebhookCleaning:
    def __init__(self, url: str):
        self.url = url

    def order(self, booking: Booking, property_name: str) -> None:
        _http(
            "POST",
            self.url,
            {
                "text": f"Cleaning needed at {property_name} after {booking.check_out:%a %d %b %H:%M}",
                "property": property_name,
                "booking_id": booking.id,
                "after": booking.check_out.isoformat(),
            },
        )


def channels_from_settings(s: Settings) -> tuple[Bookings, GuestChannel, OwnerChannel, CleaningChannel]:
    if s.replica_url:
        bookings: Bookings = ReplicaBookings(s)
        guest: GuestChannel = ReplicaGuest(s)
    else:
        bookings = FileBookings(s.bookings_file, s.tz) if s.bookings_file else StaticBookings([], s.tz)
        guest = ConsoleGuest()
    owner: OwnerChannel = WebhookOwner(s.owner_webhook) if s.owner_webhook else ConsoleOwner()
    cleaning: CleaningChannel = WebhookCleaning(s.cleaning_webhook) if s.cleaning_webhook else ConsoleCleaning()
    return bookings, guest, owner, cleaning
