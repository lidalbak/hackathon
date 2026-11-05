"""Turns readings into incidents. No LLM here: what counts as a problem is the host's rule.

A condition becomes an incident when most readings over its duration breach the limit
(80 %, so one quiet moment in a party doesn't reset the clock), and the incident ends
once nothing has breached for ``clear_minutes``. Tamper starts and ends at once.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Callable, Literal, Optional

from stay_agent.config import Limits
from stay_agent.models import Incident, IncidentKind, Reading

BREACH_SHARE = 0.8


@dataclass
class Change:
    what: Literal["started", "ended"]
    incident: Incident


def in_quiet_hours(t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # wraps past midnight


@dataclass
class _Rule:
    kind: IncidentKind
    minutes: float
    breach: Callable[[Reading], bool]
    value: Callable[[Reading], Optional[float]]  # what "peak" tracks
    peak_is_max: bool
    describe: Callable[[list[Reading], bool], str]


class Detector:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.history: deque[Reading] = deque()
        self.active: dict[IncidentKind, Incident] = {}
        self._last_breach: dict[IncidentKind, datetime] = {}
        self._ended: dict[IncidentKind, datetime] = {}
        lim = limits
        self.rules = [
            _Rule(
                IncidentKind.NOISE,
                lim.noise_minutes,
                lambda r: r.db > self.noise_limit(r.at),
                lambda r: r.db_max,
                True,
                self._describe_noise,
            ),
            _Rule(
                IncidentKind.TEMP_HIGH,
                lim.temp_minutes,
                lambda r: r.temp is not None and r.temp > lim.temp_high,
                lambda r: r.temp,
                True,
                lambda rs, _: f"temperature {_avg(rs, 'temp'):.1f} C for {lim.temp_minutes:g} min (limit {lim.temp_high:g} C)",
            ),
            _Rule(
                IncidentKind.TEMP_LOW,
                lim.temp_minutes,
                lambda r: r.temp is not None and r.temp < lim.temp_low,
                lambda r: r.temp,
                False,
                lambda rs, _: f"temperature {_avg(rs, 'temp'):.1f} C for {lim.temp_minutes:g} min (limit {lim.temp_low:g} C)",
            ),
            _Rule(
                IncidentKind.HUMIDITY,
                lim.humidity_minutes,
                lambda r: r.hum is not None and r.hum > lim.humidity,
                lambda r: r.hum,
                True,
                lambda rs, _: f"humidity {_avg(rs, 'hum'):.0f} % for {lim.humidity_minutes:g} min (limit {lim.humidity:g} %)",
            ),
        ]
        # Keep a margin beyond the longest rule: otherwise the oldest reading kept is always
        # a little newer than the window start, and no rule has ever "watched long enough".
        self._keep = timedelta(minutes=max(r.minutes for r in self.rules) + 2)

    def quiet(self, at: datetime) -> bool:
        return in_quiet_hours(at.time(), self.limits.quiet_start, self.limits.quiet_end)

    def noise_limit(self, at: datetime) -> float:
        return self.limits.quiet_noise_db if self.quiet(at) else self.limits.noise_db

    def _describe_noise(self, rs: list[Reading], quiet: bool) -> str:
        limit = self.limits.quiet_noise_db if quiet else self.limits.noise_db
        when = " during quiet hours" if quiet else ""
        return (
            f"average {_avg(rs, 'db'):.0f} dB, peaks to {max(r.db_max for r in rs):.0f} dB, "
            f"for {self.limits.noise_minutes:g} min{when} (limit {limit:g} dB)"
        )

    def reset(self) -> None:
        self.history.clear()
        self.active.clear()
        self._last_breach.clear()
        self._ended.clear()

    def breaching(self, kind: IncidentKind) -> bool:
        """Is the condition still going on (breached within the last ``clear_minutes``)?"""
        last = self._last_breach.get(kind)
        if last is None or not self.history:
            return False
        return self.history[-1].at - last < timedelta(minutes=self.limits.clear_minutes)

    def feed(self, r: Reading) -> list[Change]:
        self.history.append(r)
        while self.history and r.at - self.history[0].at > self._keep:
            self.history.popleft()

        changes: list[Change] = []
        for rule in self.rules:
            changes += self._apply(rule, r)
        changes += self._tamper(r)
        return changes

    def _apply(self, rule: _Rule, r: Reading) -> list[Change]:
        if rule.breach(r):
            self._last_breach[rule.kind] = r.at
        inc = self.active.get(rule.kind)

        if inc is not None:
            v = rule.value(r)
            if v is not None and rule.breach(r):
                inc.peak = v if inc.peak is None else (max if rule.peak_is_max else min)(inc.peak, v)
            if not self.breaching(rule.kind):
                inc.ended = r.at
                self._ended[rule.kind] = r.at
                del self.active[rule.kind]
                return [Change("ended", inc)]
            return []

        since = r.at - timedelta(minutes=rule.minutes)
        if self.history[0].at > since:
            return []  # not watched for long enough yet
        ended = self._ended.get(rule.kind)
        if ended is not None and ended > since:
            return []  # a new incident needs a full duration of readings after the last one ended
        window = [x for x in self.history if x.at >= since]
        if sum(rule.breach(x) for x in window) < BREACH_SHARE * len(window):
            return []
        values = [v for v in (rule.value(x) for x in window if rule.breach(x)) if v is not None]
        quiet = rule.kind == IncidentKind.NOISE and self.quiet(r.at)
        inc = Incident(
            kind=rule.kind,
            started=next(x.at for x in window if rule.breach(x)),
            detail=rule.describe(window, quiet),
            peak=(max(values) if rule.peak_is_max else min(values)) if values else None,
            quiet_hours=quiet,
        )
        self.active[rule.kind] = inc
        return [Change("started", inc)]

    def _tamper(self, r: Reading) -> list[Change]:
        inc = self.active.get(IncidentKind.TAMPER)
        if r.tampered and inc is None:
            inc = Incident(
                kind=IncidentKind.TAMPER,
                started=r.at,
                detail=f"the sensor was moved: turned {r.angle:.0f} degrees from where the host placed it",
                peak=r.angle,
            )
            self.active[IncidentKind.TAMPER] = inc
            return [Change("started", inc)]
        if inc is not None:
            if r.tampered:
                inc.peak = max(inc.peak or 0, r.angle)
                return []
            inc.ended = r.at
            del self.active[IncidentKind.TAMPER]
            return [Change("ended", inc)]
        return []


def _avg(rs: list[Reading], field: str) -> float:
    vals = [getattr(r, field) for r in rs if getattr(r, field) is not None]
    return sum(vals) / len(vals) if vals else float("nan")
