"""Data the harness passes around.

Pydantic models, so input from the device and the booking source is validated on the
way in and everything serialises straight to JSON for the replica and the reports.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Reading(BaseModel):
    """One POST from the device: the last 5 s of measurements."""

    device_id: str = "stay-sensor-1"
    at: datetime  # set by the server on arrival; the device has no clock
    temp: Optional[float] = None  # degrees C; the device sends -99 when the sensor is missing
    hum: Optional[float] = None  # % RH; the device sends -1 when the sensor is missing
    db: float  # 5 s energy average
    db_max: float  # loudest 50 ms in those 5 s
    moving: bool = False
    tampered: bool = False
    angle: float = 0  # degrees away from where the unit was placed
    near: bool = False  # something within 10 cm: the device shows DO NOT TOUCH

    @field_validator("temp")
    @classmethod
    def _missing_temp(cls, v: Optional[float]) -> Optional[float]:
        return None if v is None or v <= -90 else v

    @field_validator("hum")
    @classmethod
    def _missing_hum(cls, v: Optional[float]) -> Optional[float]:
        return None if v is None or v < 0 else v


class Booking(BaseModel):
    id: str  # the replica's conversation id: guest messages go to that thread
    guest_name: str
    guests: int = 1
    check_in: datetime
    check_out: datetime
    language: str = "en"  # language for messages to the guest
    listing: str = ""
    code: str = ""  # reservation code


class IncidentKind(str, Enum):
    NOISE = "noise"
    TEMP_HIGH = "temp_high"
    TEMP_LOW = "temp_low"
    HUMIDITY = "humidity"
    TAMPER = "tamper"
    OFFLINE = "offline"


class Incident(BaseModel):
    kind: IncidentKind
    started: datetime
    ended: Optional[datetime] = None
    detail: str  # the facts, worded by code: "average 78 dB for 10 min after 22:00 (limit 65 dB)"
    peak: Optional[float] = None  # loudest dB, highest/lowest temp, highest humidity
    quiet_hours: bool = False
    messages_sent: int = 0
    last_message_at: Optional[datetime] = None
    escalated: bool = False


Recipient = Literal["guest", "owner", "cleaner"]
Author = Literal["claude", "template"]


class SentMessage(BaseModel):
    at: datetime
    to: Recipient
    kind: Optional[IncidentKind] = None
    text: str
    written_by: Author


# ---- What Claude writes (structured output) -----------------------------------------


class GuestText(BaseModel):
    text: str = Field(description="The message to the guest, ready to send. 2-4 short sentences.")


class GuestReply(BaseModel):
    text: str = Field(description="The reply to the guest, ready to send.")
    needs_owner: bool = Field(description="True if the real host must follow up (can't answer, a decision, an emergency).")
    owner_note: str = Field(description="One line for the host about what the guest needs; empty if needs_owner is false.")


class OwnerAlert(BaseModel):
    subject: str = Field(description="One line, under 70 characters.")
    text: str = Field(description="What happened, what has been done, what the owner may want to do.")


class StayNarrative(BaseModel):
    headline: str = Field(description="One line verdict on the stay, e.g. 'Quiet stay, no issues'.")
    summary: str = Field(description="3-5 sentences for the owner, using only the facts given.")
    issues: list[str] = Field(description="One line per issue worth the owner's attention; empty if none.")
    recommendation: str = Field(description="One practical suggestion for the owner, or 'Nothing to do'.")


class ReviewDraft(BaseModel):
    rating: int = Field(ge=1, le=5, description="Suggested overall star rating for the guest.")
    public_review: str = Field(description="Review of the guest for Airbnb, 2-4 sentences, fair and specific.")
    private_note: str = Field(description="Private feedback to the guest; empty string if nothing to add.")


# ---- Reports and device replies -----------------------------------------------------


class StayStats(BaseModel):
    """Computed by code from every reading of the stay, so the numbers are never made up."""

    readings: int = 0
    avg_db: Optional[float] = None
    max_db: Optional[float] = None
    quiet_hours_max_db: Optional[float] = None
    min_temp: Optional[float] = None
    max_temp: Optional[float] = None
    max_humidity: Optional[float] = None
    incidents: dict[str, int] = Field(default_factory=dict)
    guest_messages: int = 0
    owner_alerts: int = 0
    tamper_events: int = 0


class StayReport(BaseModel):
    booking: Booking
    stats: StayStats
    narrative: StayNarrative
    review: ReviewDraft
    incidents: list[Incident]
    messages: list[SentMessage]
    written_by: Author
    created: datetime


class DeviceReply(BaseModel):
    """What the device shows: the answer to every POST /api/readings."""

    level: Literal["ok", "watch", "alert"] = "ok"
    text: str = "No guests right now"
    guest: str = ""
    guests: int = 0
