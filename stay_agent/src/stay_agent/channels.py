"""Where bookings come from and where messages go.

Each has a console version (prints, for the demo and tests) and an HTTP version: the
Airbnb replica for bookings and guest messages, webhooks for the owner and the cleaner.
The replica endpoints the harness expects are listed in README.md.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime
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


def _http(method: str, url: str, body: Optional[dict] = None, timeout: float = 10) -> object:
    data = json.dumps(body, default=str).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
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


class ReplicaBookings:
    """GET {replica}/api/bookings -> [Booking, ...]"""

    def __init__(self, base_url: str, tz: ZoneInfo):
        self.base_url, self.tz = base_url.rstrip("/"), tz
        self._last: list[Booking] = []

    def current(self, now: datetime) -> Optional[Booking]:
        try:
            self._last = [localize(b, self.tz) for b in _bookings.validate_python(_http("GET", f"{self.base_url}/api/bookings"))]
        except Exception:  # replica down: keep using the last list we had
            log.warning("could not fetch bookings from the replica; using the last list", exc_info=True)
        return current_booking(self._last, now)


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
    """POST {replica}/api/bookings/{id}/messages {"sender": "host", "text": ...}"""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def send(self, booking: Booking, text: str) -> None:
        _http("POST", f"{self.base_url}/api/bookings/{booking.id}/messages", {"sender": "host", "text": text})


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
        bookings: Bookings = ReplicaBookings(s.replica_url, s.tz)
        guest: GuestChannel = ReplicaGuest(s.replica_url)
    else:
        bookings = FileBookings(s.bookings_file, s.tz) if s.bookings_file else StaticBookings([], s.tz)
        guest = ConsoleGuest()
    owner: OwnerChannel = WebhookOwner(s.owner_webhook) if s.owner_webhook else ConsoleOwner()
    cleaning: CleaningChannel = WebhookCleaning(s.cleaning_webhook) if s.cleaning_webhook else ConsoleCleaning()
    return bookings, guest, owner, cleaning
