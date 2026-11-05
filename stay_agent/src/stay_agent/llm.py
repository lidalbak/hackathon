"""Everything Claude writes, through Pydantic AI.

Code decides *whether* to message and to whom (rules.py, harness.py); Claude only
decides *how* to say it. Each method falls back to a template when Claude can't be
reached or declines, so a guest always gets a message and the owner always gets a report.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider

from stay_agent.config import Settings
from stay_agent.models import (
    Author,
    Booking,
    GuestReply,
    GuestText,
    Incident,
    IncidentKind,
    OwnerAlert,
    ReviewDraft,
    SentMessage,
    StayNarrative,
    StayStats,
)

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

GUEST_INSTRUCTIONS = """\
You are the host of a short-term rental, writing to your current guest in the booking
platform's chat about something the apartment's sensor noticed (sound level,
temperature, humidity, or the sensor being moved). Write as the host yourself, in the
first person, like a friendly person texting, not a company.

- First message about an issue: warm, polite, assume good faith.
- Reminders: firmer but still respectful. From the third message on, say that you will
  have to step in (call or come by) if it continues.
- Use only the facts given. Never invent numbers, and never claim more than the facts
  show (a loud room is not proof of a party). Don't explain how the sensor works.
- Write in the language given. No subject line, no placeholders. 2-4 short sentences.
- Sign with the host's first name."""

REPLY_INSTRUCTIONS = """\
You are the host of a short-term rental, answering your guest in the booking platform's
chat. Write as the host yourself, in the first person, briefly (1-4 sentences), warm and
practical, in the language the guest writes in. No signature needed in a chat reply.

- Use only the facts given about the apartment, the booking, the house rules and what
  the apartment's sensor reads now. Never invent facts such as codes, passwords,
  addresses, prices or times that are not given.
- If you can't answer from the facts, or the guest wants something only the real host
  can decide or do (refunds, damage, extra guests, early check-in, anything broken),
  say you'll get back to them shortly, and set needs_owner with a one-line note.
- If the guest reports an emergency (fire, smoke, water leak, break-in, injury), tell
  them to call 112 right away, and set needs_owner.
- If the guest is only saying thanks or hello, answer in kind; needs_owner stays false."""

OWNER_INSTRUCTIONS = """\
You alert the owner of a short-term rental about a problem in their apartment. Be brief
and factual: what happened, what the assistant has already done (messages to the guest),
and what the owner may want to do now (call the guest, check in person, nothing yet).
Use only the facts given."""

REPORT_INSTRUCTIONS = """\
You write the owner's report after a guest's stay in their short-term rental, from the
computed statistics, incident log and message log given. Use only those numbers and
facts; do not estimate anything that isn't given. A stay with no incidents is a good
stay: say so plainly."""

REVIEW_INSTRUCTIONS = """\
You draft the host's review of a guest for Airbnb, for the host to approve before it is
posted. Be fair and specific. A single issue that stopped after one friendly message
should not sink the rating; repeated issues, escalations to the owner and a moved sensor
should be mentioned factually and without drama. Write the public review in English."""


def claude_model(name: str) -> Model:
    # AsyncAnthropic() resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
    return AnthropicModel(name, provider=AnthropicProvider(anthropic_client=AsyncAnthropic()))


class Writer:
    def __init__(self, settings: Settings, model: Optional[Model] = None):
        self.settings = settings
        if model is None and settings.use_claude:
            model = claude_model(settings.model)
        self.model = model
        short = AnthropicModelSettings(max_tokens=4000, anthropic_effort="low")
        longer = AnthropicModelSettings(max_tokens=16000, anthropic_effort="medium")
        self._guest = Agent(model, output_type=NativeOutput(GuestText), instructions=GUEST_INSTRUCTIONS, model_settings=short)
        self._owner = Agent(model, output_type=NativeOutput(OwnerAlert), instructions=OWNER_INSTRUCTIONS, model_settings=short)
        self._report = Agent(model, output_type=NativeOutput(StayNarrative), instructions=REPORT_INSTRUCTIONS, model_settings=longer)
        self._review = Agent(model, output_type=NativeOutput(ReviewDraft), instructions=REVIEW_INSTRUCTIONS, model_settings=longer)
        self._reply = Agent(model, output_type=NativeOutput(GuestReply), instructions=REPLY_INSTRUCTIONS, model_settings=short)

    def _run(self, agent: Agent[None, T], prompt: str) -> Optional[T]:
        if self.model is None:
            return None
        try:
            return agent.run_sync(prompt).output
        except Exception:  # any failure (network, auth, refusal, bad output) falls back to a template
            log.exception("Claude call failed; using a template")
            return None

    # ---- To the guest ---------------------------------------------------------------

    def guest_message(self, booking: Booking, incident: Incident, history: list[SentMessage]) -> tuple[str, Author]:
        s = self.settings
        attempt = incident.messages_sent + 1
        earlier = [m.text for m in history if m.to == "guest" and m.kind == incident.kind][-2:]
        prompt = _facts(
            property=s.property_name,
            host=s.owner_name,
            house_rules=s.house_rules,
            guest=booking.guest_name,
            guests_booked=booking.guests,
            check_out=_hm(booking.check_out, with_day=True),
            language=booking.language,
            issue=incident.kind.value,
            facts=incident.detail,
            since=_hm(incident.started),
            message_number_about_this_issue=attempt,
            earlier_messages_about_it=earlier,
        )
        out = self._run(self._guest, prompt)
        if out is not None:
            return out.text, "claude"
        return _guest_template(s, booking, incident, attempt), "template"

    def guest_reply(
        self, guest_name: str, stay: dict, transcript: list[str], sensor_now: Optional[dict]
    ) -> tuple[GuestReply, Author]:
        """Answer whatever the guest just wrote. ``transcript`` is the chat, oldest first."""
        s = self.settings
        prompt = _facts(
            property=s.property_name,
            host=s.owner_name,
            house_rules=s.house_rules,
            about_the_apartment=s.property_info,
            guest=guest_name,
            stay=stay,
            sensor_now=sensor_now or "no sensor reading for this stay",
            chat_so_far=transcript[-20:],
        )
        out = self._run(self._reply, prompt)
        if out is not None:
            return out, "claude"
        return (
            GuestReply(
                text=f"Thanks for your message, {guest_name}! I'll get back to you shortly. – {s.owner_name}",
                needs_owner=True,
                owner_note="Couldn't answer automatically",
            ),
            "template",
        )

    # ---- To the owner ---------------------------------------------------------------

    def owner_alert(
        self, reason: str, booking: Optional[Booking], incidents: list[Incident], messages: list[SentMessage]
    ) -> tuple[OwnerAlert, Author]:
        s = self.settings
        prompt = _facts(
            property=s.property_name,
            owner=s.owner_name,
            reason=reason,
            guest=f"{booking.guest_name}, {booking.guests} booked, checks out {_hm(booking.check_out, True)}" if booking else "no guest booked right now",
            incidents=[_incident_line(i) for i in incidents],
            messages_sent_to_guest=[f"{_hm(m.at)}: {m.text}" for m in messages if m.to == "guest"][-5:],
        )
        out = self._run(self._owner, prompt)
        if out is not None:
            return out, "claude"
        lines = [f"{s.property_name}: {reason}."]
        lines += [f"- {_incident_line(i)}" for i in incidents]
        sent = sum(m.to == "guest" for m in messages)
        if booking:
            lines.append(f"Guest {booking.guest_name} has had {sent} message(s) from the assistant.")
        return OwnerAlert(subject=f"{s.property_name}: {reason}"[:70], text="\n".join(lines)), "template"

    # ---- After the stay -------------------------------------------------------------

    def stay_report(
        self, booking: Booking, stats: StayStats, incidents: list[Incident], messages: list[SentMessage]
    ) -> tuple[StayNarrative, ReviewDraft, Author]:
        s = self.settings
        prompt = _facts(
            property=s.property_name,
            house_rules=s.house_rules,
            guest=booking.guest_name,
            guests_booked=booking.guests,
            stay=f"{_hm(booking.check_in, True)} to {_hm(booking.check_out, True)}",
            stats=stats.model_dump(),
            incidents=[_incident_line(i) for i in incidents],
            messages=[f"{_hm(m.at, True)} to {m.to}: {m.text}" for m in messages],
        )
        narrative = self._run(self._report, prompt)
        review = self._run(self._review, prompt)
        if narrative is not None and review is not None:
            return narrative, review, "claude"
        return (
            narrative or _narrative_template(stats, incidents),
            review or _review_template(booking, stats),
            "template",
        )


# ---- Helpers and templates --------------------------------------------------------


def _facts(**kv: object) -> str:
    return "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)}" for k, v in kv.items())


def _hm(dt: datetime, with_day: bool = False) -> str:
    return dt.strftime("%a %d %b %H:%M" if with_day else "%H:%M")


def _incident_line(i: Incident) -> str:
    end = f"until {_hm(i.ended)}" if i.ended else "ongoing"
    extra = f", {i.messages_sent} guest message(s)" + (", owner alerted" if i.escalated else "")
    return f"{i.kind.value} from {_hm(i.started, True)} {end}: {i.detail}{extra}"


def _guest_template(s: Settings, b: Booking, i: Incident, attempt: int) -> str:
    sign = f"– {s.owner_name}"
    if i.kind == IncidentKind.NOISE:
        if attempt >= 3:
            body = "the noise is still high. Please turn it down now; if it continues, I'll have to call or come by."
        elif attempt == 2:
            body = "the noise level is still high. Please keep it down so the neighbours can rest."
        else:
            when = " and it's quiet hours" if i.quiet_hours else ""
            body = f"the sound level in the apartment has been high for a while{when}. Could you keep it down a little? Thank you!"
    elif i.kind == IncidentKind.TEMP_HIGH:
        body = "it's getting very warm inside. Feel free to turn down the heating or open a window."
    elif i.kind == IncidentKind.TEMP_LOW:
        body = "it's getting cold inside. Please close any open windows and turn up the heating if you need to."
    elif i.kind == IncidentKind.HUMIDITY:
        body = "the humidity is high. Please open a window for a while or use the bathroom fan after showers."
    elif i.kind == IncidentKind.TAMPER:
        body = "it looks like the sensor in the living room has been moved. Could you put it back where it was? Thanks!"
    else:
        body = "we noticed something in the apartment and wanted to check that everything is fine."
    return f"Hi {b.guest_name}, {body} {sign}"


def _narrative_template(stats: StayStats, incidents: list[Incident]) -> StayNarrative:
    if not incidents:
        return StayNarrative(
            headline="Quiet stay, no issues",
            summary=f"No incidents during the stay. Average sound level {stats.avg_db or 0:.0f} dB.",
            issues=[],
            recommendation="Nothing to do",
        )
    return StayNarrative(
        headline=f"{len(incidents)} incident(s) during the stay",
        summary=f"{stats.guest_messages} message(s) to the guest, {stats.owner_alerts} alert(s) to the owner.",
        issues=[_incident_line(i) for i in incidents],
        recommendation="Read the incidents before you review the guest.",
    )


def _review_template(b: Booking, stats: StayStats) -> ReviewDraft:
    if stats.owner_alerts:
        rating, text = 3, f"{b.guest_name} needed several reminders about the house rules during the stay."
    elif stats.guest_messages:
        rating, text = 4, f"{b.guest_name} was a good guest and responded to a friendly reminder."
    else:
        rating, text = 5, f"{b.guest_name} was a great guest: quiet, respectful and easy to host. Welcome back!"
    return ReviewDraft(rating=rating, public_review=text, private_note="")
