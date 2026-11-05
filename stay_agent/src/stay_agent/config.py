"""The host's rules and where things are sent. Load from JSON with ``Settings.load``."""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel


class Limits(BaseModel):
    """When a reading becomes an incident. Durations are in minutes."""

    noise_db: float = 75  # average above this...
    quiet_noise_db: float = 65  # ...or this during quiet hours...
    noise_minutes: float = 10  # ...for this long
    quiet_start: time = time(22, 0)
    quiet_end: time = time(8, 0)
    temp_high: float = 28
    temp_low: float = 12
    temp_minutes: float = 15
    humidity: float = 70
    humidity_minutes: float = 30
    clear_minutes: float = 5  # no breach for this long ends the incident
    offline_seconds: float = 60  # no reading for this long during a stay


class Policy(BaseModel):
    reminder_minutes: float = 15  # gap before reminding the guest about the same ongoing issue
    escalate_after: int = 3  # this many guest messages...
    escalate_window_minutes: float = 60  # ...within this window contacts the owner
    escalate_tamper: bool = True  # a moved device alerts the owner straight away


class Settings(BaseModel):
    property_name: str = "the apartment"
    owner_name: str = "your host"
    house_rules: str = "Quiet hours 22:00-08:00. No parties or events. No more guests than booked."
    timezone: str = "Europe/Oslo"

    model: str = "claude-opus-5-5"
    use_claude: bool = True  # False: templates only, no API calls

    device_token: str = ""  # must match the token stored on the device; empty accepts any device

    replica_url: str = ""  # Airbnb copy (airbnb_chat), e.g. http://127.0.0.1:8766; empty prints guest messages
    replica_token: str = ""  # its CHAT_API_TOKEN, if set
    listing_title: str = ""  # only stays at listings whose title contains this; empty: every hosted listing
    check_in_time: time = time(15, 0)  # the replica's reservations have dates only
    check_out_time: time = time(11, 0)
    typing_seconds: float = 2  # show "typing..." in the replica before each message
    bookings_file: str = ""  # JSON list of bookings, used when there is no replica
    owner_webhook: str = ""  # POST owner alerts here (Slack-style {"text": ...}); empty prints them
    cleaning_webhook: str = ""  # POST cleaning orders here; empty prints them
    reports_dir: str = "reports"

    limits: Limits = Limits()
    policy: Policy = Policy()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def load(cls, path: Optional[str | Path]) -> "Settings":
        if not path:
            return cls()
        return cls.model_validate_json(Path(path).read_text())
