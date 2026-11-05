from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stay_agent import Detector, IncidentKind, Limits, Reading

TZ = ZoneInfo("Europe/Oslo")


def feed(detector: Detector, start: datetime, minutes: int, **values) -> list:
    changes = []
    for m in range(minutes):
        r = Reading(at=start + timedelta(minutes=m), db=values.get("db", 40), db_max=values.get("db", 40) + 8,
                    temp=values.get("temp", 21), hum=values.get("hum", 45), tampered=values.get("tampered", False))
        changes += detector.feed(r)
    return changes


def test_loud_daytime_below_day_limit_is_fine():
    d = Detector(Limits())
    assert feed(d, datetime(2026, 11, 5, 15, 0, tzinfo=TZ), 30, db=70) == []


def test_same_noise_in_quiet_hours_is_an_incident():
    d = Detector(Limits())
    changes = feed(d, datetime(2026, 11, 5, 22, 30, tzinfo=TZ), 15, db=70)
    assert [c.what for c in changes] == ["started"]
    inc = changes[0].incident
    assert inc.kind == IncidentKind.NOISE and inc.quiet_hours
    assert inc.started == datetime(2026, 11, 5, 22, 30, tzinfo=TZ)


def test_noise_needs_the_full_duration():
    d = Detector(Limits(noise_minutes=10))
    assert feed(d, datetime(2026, 11, 5, 23, 0, tzinfo=TZ), 10, db=80) == []  # 0..9 min: not yet 10 min watched
    assert [c.what for c in feed(d, datetime(2026, 11, 5, 23, 10, tzinfo=TZ), 1, db=80)] == ["started"]


def test_incident_ends_after_clear_minutes_and_does_not_restart_at_once():
    d = Detector(Limits(clear_minutes=5))
    start = datetime(2026, 11, 5, 23, 0, tzinfo=TZ)
    feed(d, start, 15, db=80)
    changes = feed(d, start + timedelta(minutes=15), 10, db=35)
    assert [c.what for c in changes] == ["ended"]
    # loud again right after: the old loud readings must not count towards a new incident
    assert feed(d, start + timedelta(minutes=25), 5, db=80) == []


def test_tamper_starts_and_ends_at_once():
    d = Detector(Limits())
    start = datetime(2026, 11, 5, 12, 0, tzinfo=TZ)
    assert [c.what for c in feed(d, start, 2, tampered=True)] == ["started"]
    assert [c.what for c in feed(d, start + timedelta(minutes=2), 1)] == ["ended"]


def test_device_missing_sensor_values_become_none():
    r = Reading.model_validate({"at": datetime.now(TZ), "temp": -99, "hum": -1, "db": 40, "db_max": 50})
    assert r.temp is None and r.hum is None


def test_confirmed_hosted_stays_become_bookings():
    from datetime import time

    from stay_agent.channels import booking_from_conversation

    conv = {
        "id": 7, "my_role": "host", "guest": {"name": "Lukas"},
        "listing": {"title": "Bright studio in Grünerløkka"},
        "reservation": {"status": "confirmed", "check_in": "2026-11-07", "check_out": "2026-11-10", "guests": 1, "code": "HM3"},
    }
    b = booking_from_conversation(conv, TZ, time(15), time(11))
    assert b.id == "7" and b.guest_name == "Lukas" and b.code == "HM3"
    assert b.check_in == datetime(2026, 11, 7, 15, 0, tzinfo=TZ)
    assert b.check_out == datetime(2026, 11, 10, 11, 0, tzinfo=TZ)
    for other in ({"my_role": "guest"}, {"reservation": {**conv["reservation"], "status": "pending"}}):
        assert booking_from_conversation({**conv, **other}, TZ, time(15), time(11)) is None


def test_device_rate_readings_with_short_demo_limits():
    # 5 s readings, 30 s rules: what the demo config does with the real device
    d = Detector(Limits(noise_db=72, quiet_noise_db=72, noise_minutes=0.5, clear_minutes=0.5,
                        temp_minutes=0.5, humidity_minutes=0.5))
    start = datetime(2026, 11, 5, 18, 0, 0, 300000, tzinfo=TZ)
    changes = []
    for i in range(10):
        r = Reading(at=start + timedelta(seconds=5 * i, milliseconds=37 * i), db=80, db_max=90, temp=21, hum=40)
        changes += d.feed(r)
    assert [c.what for c in changes] == ["started"]
