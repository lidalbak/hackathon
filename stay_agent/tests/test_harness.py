from datetime import datetime

from pydantic_ai.models.test import TestModel
from pydantic_ai.profiles import ModelProfile

from stay_agent import Inline, Settings, StayHarness, Writer
from stay_agent.channels import StaticBookings
from stay_agent.simulate import demo_booking, readings


class Recorder:
    def __init__(self):
        self.guest, self.owner, self.cleaning = [], [], []

    def send(self, *args):  # guest: (booking, text); owner: (alert,)
        (self.guest if len(args) == 2 else self.owner).append(args[-1])

    def order(self, booking, property_name):
        self.cleaning.append(booking.id)


def run(scenario: str, writer_model=None, tmp_path=None):
    settings = Settings(use_claude=writer_model is not None, reports_dir=str(tmp_path))
    booking = demo_booking(settings.tz, datetime(2026, 11, 5).date())
    rec = Recorder()
    harness = StayHarness(
        settings, Writer(settings, model=writer_model), StaticBookings([booking], settings.tz), rec, rec, rec, Inline()
    )
    replies = [harness.ingest(r) for r in readings(scenario, booking)]
    harness.finish(booking.check_out)
    return harness, rec, replies, booking


def test_quiet_stay_sends_nothing_and_reports_five_stars(tmp_path):
    harness, rec, replies, booking = run("quiet", tmp_path=tmp_path)
    assert rec.guest == [] and rec.owner == []
    assert {r.level for r in replies} == {"ok"}
    report = harness.reports[booking.id]
    assert report.review.rating == 5 and report.stats.readings == 1200
    assert rec.cleaning == [booking.id]
    assert (tmp_path / f"{booking.id}.json").exists()


def test_party_escalates_to_owner_after_three_messages_then_stops_messaging(tmp_path):
    harness, rec, replies, booking = run("party", tmp_path=tmp_path)
    assert len(rec.guest) == 3
    assert len(rec.owner) == 1
    assert "alert" in {r.level for r in replies}
    report = harness.reports[booking.id]
    assert report.stats.guest_messages == 3 and report.stats.owner_alerts == 1
    assert report.incidents[0].escalated


def test_tamper_messages_guest_and_alerts_owner_at_once(tmp_path):
    _, rec, replies, _ = run("tamper", tmp_path=tmp_path)
    assert len(rec.guest) == 1 and len(rec.owner) == 1
    assert "alert" in {r.level for r in replies}


def test_model_output_is_used_and_bad_output_falls_back_to_templates(tmp_path):
    # No API call: the profile lets TestModel take NativeOutput like Claude does. Its fixed
    # answer fits the guest message schema, but not the owner alert or the report.
    model = TestModel(
        custom_output_text='{"text": "Hi Anna, from Claude"}',
        profile=ModelProfile(supports_json_schema_output=True),
    )
    harness, rec, _, booking = run("party", writer_model=model, tmp_path=tmp_path)
    assert rec.guest == ["Hi Anna, from Claude"] * 3
    assert len(rec.owner) == 1  # template, since the model's answer didn't fit
    report = harness.reports[booking.id]
    assert report.written_by == "template"
    assert [m.written_by for m in report.messages if m.to == "guest"] == ["claude"] * 3


def test_demo_second_loud_spell_is_the_final_warning_and_alerts_the_owner(tmp_path):
    # demo policy: 90 dB for 3 s, escalate on the second warning, even after a quiet spell
    from datetime import timedelta

    from stay_agent import Limits, Policy, Reading

    settings = Settings(
        use_claude=False, owner_name="Simen", reports_dir=str(tmp_path),
        limits=Limits(noise_db=90, quiet_noise_db=90, noise_minutes=0.05, clear_minutes=0.5),
        policy=Policy(reminder_minutes=0.5, escalate_after=2, escalate_window_minutes=10),
    )
    booking = demo_booking(settings.tz, datetime(2026, 11, 5).date())
    rec = Recorder()
    harness = StayHarness(settings, Writer(settings), StaticBookings([booking], settings.tz), rec, rec, rec, Inline())
    t = booking.check_in + timedelta(hours=3)
    replies = []
    for i, db in enumerate([60] * 4 + [95] * 2 + [60] * 12 + [95] * 2):  # loud, a minute quiet, loud again
        replies.append(harness.ingest(Reading(at=t + timedelta(seconds=5 * i), db=db, db_max=db + 5, temp=21, hum=40)))
    assert len(rec.guest) == 2 and len(rec.owner) == 1
    assert "second time" in rec.guest[1] and "Simen" in rec.guest[1]
    assert replies[-1].level == "alert"
