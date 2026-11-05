"""Turn assembly: final speech segments -> complete conversational turns.

A VAD segment ends at every pause of ~0.65 s, which often splits a sentence
("Det hadde vært fint å se hvor mange nye kunder" ... "som bruker produkt X").
Task detection must only see whole turns, so segments are joined until the room has been
quiet long enough. How long depends on how the last segment ended: a question mark closes
fast, a dangling "som"/"og"/comma waits longer. Partial (interim) text never reaches here.
"""
import re
import time
from datetime import datetime, timezone

INCOMPLETE_TAIL = re.compile(
    r'(,|\b(og|eller|men|som|at|for|med|på|i|til|av|fra|hvor|hvis|når|om|så|der|den|det|de|en|et|ei|'
    r'mange|altså|liksom|ehm|eh|øh|hm|the|and|or|of|to|how|many))\s*$', re.I)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class TurnAssembler:
    def __init__(self, on_turn, clock=time.monotonic, scale=1.0,
                 gap=1.4, gap_closed=0.8, gap_incomplete=2.6, max_segments=8, max_seconds=45):
        self.on_turn = on_turn
        self.clock = clock
        self.gap, self.gap_closed, self.gap_incomplete = gap * scale, gap_closed * scale, gap_incomplete * scale
        self.max_segments, self.max_seconds = max_segments, max_seconds * scale
        self.segments = []
        self.speaking = False
        self.pending = 0          # segments committed but not yet transcribed
        self.quiet_since = None   # when the last speech burst ended
        self.opened = None
        self.started_at = None

    def wait(self):
        if not self.segments:
            return self.gap
        tail = self.segments[-1].rstrip()
        if INCOMPLETE_TAIL.search(tail.rstrip('.')):
            return self.gap_incomplete
        if tail.endswith(('?', '!', '.')):
            return self.gap_closed
        return self.gap

    def _now(self, now):
        return self.clock() if now is None else now

    def speech(self, active, now=None):
        now = self._now(now)
        if active:
            # Speech after a long enough silence belongs to a new turn.
            if self.segments and not self.pending and not self.speaking and now - (self.quiet_since or now) >= self.wait():
                self._close(now)
            if self.opened is None:
                self.opened, self.started_at = now, now_iso()
            self.speaking = True
        else:
            if self.speaking:
                self.pending += 1
            self.speaking = False
            self.quiet_since = now

    def final(self, text, now=None):
        now = self._now(now)
        self.pending = max(0, self.pending - 1)
        text = ' '.join((text or '').split())
        if text:
            if self.opened is None:
                self.opened, self.started_at = now, now_iso()
            self.segments.append(text)
        if self.quiet_since is None:
            self.quiet_since = now
        if len(self.segments) >= self.max_segments or (self.opened is not None and now - self.opened >= self.max_seconds and not self.speaking):
            self._close(now)

    def failed(self, now=None):
        self.pending = max(0, self.pending - 1)
        if not self.segments and not self.pending and not self.speaking:
            self.opened = None

    def tick(self, now=None):
        now = self._now(now)
        if self.segments and not self.speaking and not self.pending and now - (self.quiet_since or now) >= self.wait():
            self._close(now)

    def flush(self):
        self.pending = 0
        self.speaking = False
        if self.segments:
            self._close(self._now(None))

    @property
    def open_text(self):
        return list(self.segments)

    def _close(self, now):
        segments, started = self.segments, self.started_at
        self.segments, self.opened, self.started_at = [], None, None
        self.on_turn({'text': ' '.join(segments), 'segments': len(segments),
                      'started_at': started or now_iso(), 'ended_at': now_iso()})
