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
