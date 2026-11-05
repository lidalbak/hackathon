"""The harness: readings in, decisions out.

``ingest`` runs for every reading (the device's POST, or the simulator). It is quick: it
moves the stay along (check-in, check-out), runs the rules, decides who to message, and
answers with what the device should show. Anything slow (Claude, HTTP) goes to the
executor: a background worker in ``serve`` so the device gets its answer at once,
inline in ``simulate`` and the tests.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Protocol

from stay_agent.channels import Bookings, CleaningChannel, GuestChannel, OwnerChannel
from stay_agent.config import Settings
from stay_agent.llm import Writer
from stay_agent.models import (
    Booking,
    DeviceReply,
    Incident,
    IncidentKind,
    Reading,
    SentMessage,
    StayReport,
    StayStats,
)
from stay_agent.rules import Change, Detector

log = logging.getLogger(__name__)

BOOKING_REFRESH = timedelta(seconds=30)
LABELS = {
    IncidentKind.NOISE: "noise",
    IncidentKind.TEMP_HIGH: "heat",
    IncidentKind.TEMP_LOW: "cold",
    IncidentKind.HUMIDITY: "humidity",
    IncidentKind.TAMPER: "the moved sensor",
    IncidentKind.OFFLINE: "the sensor going offline",
}


class Executor(Protocol):
    def submit(self, fn: Callable[[], None]) -> None: ...


class Inline:
    def submit(self, fn: Callable[[], None]) -> None:
        fn()


class Worker:
    """One background thread, so messages go out in the order they were decided."""

    def __init__(self) -> None:
        self._q: queue.Queue[Callable[[], None]] = queue.Queue()
        threading.Thread(target=self._run, name="stay-agent-worker", daemon=True).start()

    def submit(self, fn: Callable[[], None]) -> None:
        self._q.put(fn)

    def _run(self) -> None:
        while True:
            fn = self._q.get()
            try:
                fn()
            except Exception:
                log.exception("job failed")


@dataclass
class StayLog:
    """Everything that happened during one stay (or between stays, with no booking)."""

    booking: Optional[Booking]
    incidents: list[Incident] = field(default_factory=list)
    messages: list[SentMessage] = field(default_factory=list)
    guest_message_times: list[datetime] = field(default_factory=list)
    owner_alert_times: list[datetime] = field(default_factory=list)
    readings: int = 0
    _db_sum: float = 0
    max_db: Optional[float] = None
    quiet_max_db: Optional[float] = None
    min_temp: Optional[float] = None
    max_temp: Optional[float] = None
    max_hum: Optional[float] = None

    def add(self, r: Reading, quiet: bool) -> None:
        self.readings += 1
        self._db_sum += r.db
        self.max_db = _max(self.max_db, r.db_max)
        if quiet:
            self.quiet_max_db = _max(self.quiet_max_db, r.db_max)
        if r.temp is not None:
            self.min_temp = r.temp if self.min_temp is None else min(self.min_temp, r.temp)
            self.max_temp = _max(self.max_temp, r.temp)
        if r.hum is not None:
            self.max_hum = _max(self.max_hum, r.hum)

    def stats(self) -> StayStats:
        kinds: dict[str, int] = {}
        for i in self.incidents:
            kinds[i.kind.value] = kinds.get(i.kind.value, 0) + 1
        r1 = lambda v: None if v is None else round(v, 1)  # noqa: E731
        return StayStats(
            readings=self.readings,
            avg_db=r1(self._db_sum / self.readings) if self.readings else None,
            max_db=r1(self.max_db),
            quiet_hours_max_db=r1(self.quiet_max_db),
            min_temp=r1(self.min_temp),
            max_temp=r1(self.max_temp),
            max_humidity=r1(self.max_hum),
            incidents=kinds,
            guest_messages=len(self.guest_message_times),
            owner_alerts=len(self.owner_alert_times),
            tamper_events=kinds.get(IncidentKind.TAMPER.value, 0),
        )


class StayHarness:
    def __init__(
        self,
        settings: Settings,
        writer: Writer,
        bookings: Bookings,
        guest: GuestChannel,
        owner: OwnerChannel,
        cleaning: CleaningChannel,
        executor: Optional[Executor] = None,
    ):
        self.settings = settings
        self.writer = writer
        self.bookings, self.guest, self.owner, self.cleaning = bookings, guest, owner, cleaning
        self.executor = executor or Inline()
        self.lock = threading.RLock()
        self.detector = Detector(settings.limits)
        self.log = StayLog(None)
        self.reply = DeviceReply()
        self.last_reading: Optional[Reading] = None
        self.offline: Optional[Incident] = None
        self.reports: dict[str, StayReport] = {}
        self._action = ""  # what the device says while an incident is open
        self._booking: Optional[Booking] = None
        self._booking_checked: Optional[datetime] = None

    # ---- Entry points ---------------------------------------------------------------

    def ingest(self, r: Reading) -> DeviceReply:
        with self.lock:
            self._advance(r.at)
            if self.offline is not None:
                self.offline.ended = r.at
                self.offline = None
            self.last_reading = r
            self.log.add(r, self.detector.quiet(r.at))
            for change in self.detector.feed(r):
                self._on_change(change, r.at)
            self._remind(r.at)
            self._update_reply()
            return self.reply.model_copy()

    def tick(self, now: datetime) -> None:
        """Runs every few seconds in ``serve``: check-out and offline need no reading."""
        with self.lock:
            self._advance(now)
            silent = self.last_reading is None or now - self.last_reading.at > timedelta(
                seconds=self.settings.limits.offline_seconds
            )
            if self.log.booking and silent and self.offline is None and self.last_reading is not None:
                self.offline = Incident(
                    kind=IncidentKind.OFFLINE,
                    started=self.last_reading.at,
                    detail=f"no reading since {self.last_reading.at:%H:%M}: unplugged, switched off or out of Wi-Fi",
                )
                self.log.incidents.append(self.offline)
                self._alert_owner("the sensor went offline during a stay", now)
            self._update_reply()

    def finish(self, now: datetime) -> None:
        """Check out the current stay now (end of a simulation)."""
        with self.lock:
            if self.log.booking:
                self._check_out(self.log.booking, now)
                self._booking, self._booking_checked = None, now

    def state(self) -> dict:
        """For GET /api/state: what the replica's dashboard shows."""
        with self.lock:
            return {
                "booking": self.log.booking.model_dump(mode="json") if self.log.booking else None,
                "device": self.reply.model_dump(),
                "reading": self.last_reading.model_dump(mode="json") if self.last_reading else None,
                "active": [i.model_dump(mode="json") for i in self.detector.active.values()],
                "incidents": [i.model_dump(mode="json") for i in self.log.incidents],
                "messages": [m.model_dump(mode="json") for m in self.log.messages[-50:]],
                "stats": self.log.stats().model_dump(),
                "reports": sorted(self.reports),
            }

    # ---- Stays ------------------------------------------------------------------------

    def _current_booking(self, now: datetime) -> Optional[Booking]:
        stale = self._booking_checked is None or now - self._booking_checked >= BOOKING_REFRESH
        if self._booking is not None and now >= self._booking.check_out:
            stale = True
        if stale:
            self._booking = self.bookings.current(now)
            self._booking_checked = now
        return self._booking

    def _advance(self, now: datetime) -> None:
        booking = self._current_booking(now)
        current = self.log.booking
        if current and (booking is None or booking.id != current.id):
            self._check_out(current, now)
        if booking and (self.log.booking is None or booking.id != self.log.booking.id):
            self._check_in(booking)

    def _check_in(self, b: Booking) -> None:
        log.info("check-in: %s (%s guests), booking %s", b.guest_name, b.guests, b.id)
        self.log = StayLog(b)
        self.detector.reset()
        self.offline = None
        self._action = ""

    def _check_out(self, b: Booking, now: datetime) -> None:
        log.info("check-out: %s, booking %s", b.guest_name, b.id)
        for inc in self.detector.active.values():
            inc.ended = now
        stay = self.log
        self.log = StayLog(None)
        self.detector.reset()
        self.offline = None
        self._action = ""
        stats = stay.stats()
        incidents = list(stay.incidents)

        def job() -> None:
            self.cleaning.order(b, self.settings.property_name)
            with self.lock:
                messages = list(stay.messages)
            narrative, review, author = self.writer.stay_report(b, stats, incidents, messages)
            report = StayReport(
                booking=b,
                stats=stats,
                narrative=narrative,
                review=review,
                incidents=incidents,
                messages=messages,
                written_by=author,
                created=now,
            )
            with self.lock:
                self.reports[b.id] = report
            out = Path(self.settings.reports_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{b.id}.json").write_text(report.model_dump_json(indent=2))
            _print_report(report)

        self.executor.submit(job)

    # ---- Incidents --------------------------------------------------------------------

    def _on_change(self, change: Change, now: datetime) -> None:
        inc = change.incident
        if change.what == "ended":
            log.info("%s ended", inc.kind.value)
            return
        log.info("%s started: %s", inc.kind.value, inc.detail)
        self.log.incidents.append(inc)
        b = self.log.booking
        if b is None:  # nobody should be there: tell the owner, not a guest
            self._alert_owner(f"{LABELS[inc.kind]} with no guest booked", now, escalate=[inc])
            return
        self._message_guest(inc, now)
        if inc.kind == IncidentKind.TAMPER and self.settings.policy.escalate_tamper:
            self._alert_owner("the sensor was moved during a stay", now, escalate=[inc])

    def _remind(self, now: datetime) -> None:
        """Noise that goes on after a message gets a reminder every ``reminder_minutes``,
        until the owner has been contacted: from then on it's the owner's call."""
        inc = self.detector.active.get(IncidentKind.NOISE)
        if inc is None or self.log.booking is None or inc.last_message_at is None or inc.escalated:
            return
        gap = timedelta(minutes=self.settings.policy.reminder_minutes)
        if self.detector.breaching(IncidentKind.NOISE) and now - inc.last_message_at >= gap:
            self._message_guest(inc, now)

    def _message_guest(self, inc: Incident, now: datetime) -> None:
        b = self.log.booking
        assert b is not None
        stay = self.log
        snapshot = inc.model_copy()  # messages_sent as it was: the writer numbers this one +1
        inc.messages_sent += 1
        inc.last_message_at = now
        stay.guest_message_times.append(now)
        self._action = f"Sent {b.guest_name} a note about {LABELS[inc.kind]}"

        def job() -> None:
            with self.lock:
                history = list(stay.messages)
            text, author = self.writer.guest_message(b, snapshot, history)
            self.guest.send(b, text)
            with self.lock:
                stay.messages.append(SentMessage(at=now, to="guest", kind=inc.kind, text=text, written_by=author))

        self.executor.submit(job)
        self._maybe_escalate(now)

    def _maybe_escalate(self, now: datetime) -> None:
        p = self.settings.policy
        since = now - timedelta(minutes=p.escalate_window_minutes)
        recent = [t for t in self.log.guest_message_times if t >= since]
        already = any(t >= since for t in self.log.owner_alert_times)
        if len(recent) >= p.escalate_after and not already:
            self._alert_owner(
                f"{len(recent)} messages to the guest in {p.escalate_window_minutes:g} min and it continues",
                now,
                escalate=list(self.detector.active.values()),
            )

    def _alert_owner(self, reason: str, now: datetime, escalate: Optional[list[Incident]] = None) -> None:
        stay = self.log
        for inc in escalate or []:
            inc.escalated = True
        stay.owner_alert_times.append(now)
        owner = self.settings.owner_name
        self._action = f"{owner[:1].upper()}{owner[1:]} has been contacted"
        b = stay.booking
        incidents = [i.model_copy() for i in (escalate or stay.incidents[-3:])]

        def job() -> None:
            with self.lock:
                messages = list(stay.messages)
            alert, author = self.writer.owner_alert(reason, b, incidents, messages)
            self.owner.send(alert)
            with self.lock:
                stay.messages.append(
                    SentMessage(at=now, to="owner", text=f"{alert.subject}\n{alert.text}", written_by=author)
                )

        self.executor.submit(job)

    # ---- Device -------------------------------------------------------------------------

    def _update_reply(self) -> None:
        b = self.log.booking
        active = list(self.detector.active.values()) + ([self.offline] if self.offline else [])
        if any(i.escalated or i.kind == IncidentKind.TAMPER for i in active):
            level = "alert"
        elif active:
            level = "watch"
        else:
            level = "ok"
        if active and self._action:
            text = self._action
        elif b is not None:
            text = f"Welcome {b.guest_name}! All good."
        else:
            text = "No guests right now"
        self.reply = DeviceReply(level=level, text=text, guest=b.guest_name if b else "", guests=b.guests if b else 0)


def _max(a: Optional[float], b: float) -> float:
    return b if a is None else max(a, b)


def _print_report(r: StayReport) -> None:
    s = r.stats
    print(
        f"\n📋 Stay report: {r.booking.guest_name}, booking {r.booking.id} ({r.written_by})\n"
        f"   {r.narrative.headline}\n   {r.narrative.summary}\n"
        + "".join(f"   - {i}\n" for i in r.narrative.issues)
        + f"   Recommendation: {r.narrative.recommendation}\n"
        f"   Numbers: {s.readings} readings, avg {s.avg_db} dB, max {s.max_db} dB, "
        f"{s.guest_messages} guest message(s), {s.owner_alerts} owner alert(s)\n"
        f"\n⭐ Review draft ({r.review.rating}/5), waiting for the owner's approval:\n"
        f"   {r.review.public_review}\n"
        + (f"   Private note: {r.review.private_note}\n" if r.review.private_note else ""),
        flush=True,
    )
