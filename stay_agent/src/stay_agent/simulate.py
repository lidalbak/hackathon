"""A whole stay in seconds, without the device: one booking, a reading per minute.

Scenarios:
  quiet   nothing happens
  party   loud from 22:40 to 00:10: messages, reminders, then the owner is contacted
  tamper  the sensor is turned on its side at 01:30 for 15 min
  cold    a window left open from 07:30: the temperature drops to 9 C
  humid   long showers from 08:00: humidity 78 % for 90 min
  all     every one of the above in the same stay
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from typing import Iterator
from zoneinfo import ZoneInfo

from stay_agent.models import Booking, Reading

SCENARIOS = ("quiet", "party", "tamper", "cold", "humid", "all")


def demo_booking(tz: ZoneInfo, day: date) -> Booking:
    return Booking(
        id="HM-DEMO-1",
        guest_name="Anna",
        guests=2,
        check_in=datetime.combine(day, time(15, 0), tz),
        check_out=datetime.combine(day + timedelta(days=1), time(11, 0), tz),
    )


def _between(t: datetime, start: time, end: time) -> bool:
    s, e, now = start.hour * 60 + start.minute, end.hour * 60 + end.minute, t.hour * 60 + t.minute
    return s <= now < e if s <= e else now >= s or now < e


def readings(scenario: str, booking: Booking, step: timedelta = timedelta(minutes=1)) -> Iterator[Reading]:
    on = lambda name: scenario in (name, "all")  # noqa: E731
    rng = random.Random(7)
    t = booking.check_in
    while t < booking.check_out:
        night = _between(t, time(0, 0), time(7, 0))
        db = (33 if night else 47) + rng.gauss(0, 3)
        temp = 21 + rng.gauss(0, 0.2)
        hum = 42 + rng.gauss(0, 1)
        tampered, angle = False, rng.random() * 2

        if on("party") and _between(t, time(22, 40), time(0, 10)):
            db = 79 + rng.gauss(0, 3)
        if on("tamper") and _between(t, time(1, 30), time(1, 45)):
            tampered, angle = True, 85
        if on("cold") and _between(t, time(7, 30), time(11, 0)):
            minutes = (t.hour * 60 + t.minute) - (7 * 60 + 30)
            temp = max(9.0, 21 - minutes * 0.2) + rng.gauss(0, 0.2)
        if on("humid") and _between(t, time(8, 0), time(9, 30)):
            hum = 78 + rng.gauss(0, 1.5)

        yield Reading(
            device_id="simulator",
            at=t,
            temp=round(temp, 2),
            hum=round(hum, 1),
            db=round(db, 1),
            db_max=round(db + 8 + rng.random() * 6, 1),
            moving=tampered,
            tampered=tampered,
            angle=angle,
        )
        t += step
