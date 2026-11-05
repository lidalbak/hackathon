"""Simulated meeting: replays a scripted Norwegian conversation through the real pipeline.

No microphone and no provider are used. The script emits the same events a live provider
does (speech on/off, growing partial text, a final per segment), so turn assembly,
interpretation, corrections, negations and deduplication run exactly as in a real meeting.
"""
import asyncio

from .base import Transcriber

# Each turn is a list of (segment text, pause after the segment in seconds).
# A short pause keeps segments in one turn; a longer one starts a new turn.
DEMO_MEETING = [
    [('Ok, skal vi starte? Vi går gjennom lanseringen først.', 2.2)],
    [('Det hadde vært fint å se hvor mange nye kunder', 0.5),
     ('som faktisk bruker produkt X, siste 30 dager.', 2.4)],
    [('Ja, hvor mange nye kunder bruker produkt X den siste måneden?', 2.2)],
    [('Og hva med produkt Y?', 2.0)],
    [('Nei vent, jeg mente produkt Z, ikke Y.', 2.6)],
    [('Vi burde også se på bruken siste kvartal.', 2.4)],
    [('Produkt Y.', 2.4)],
    [('Vi trenger ikke å sjekke produkt X siste 7 dager, det er for kort tid.', 2.4)],
    [('Bra, da tar vi resten av agendaen.', 1.0)],
]


class ScriptedTranscriber(Transcriber):
    name = 'simulated'
    label = 'Simulert møte · ingen mikrofon'
    destination = 'Ingen · ferdig skrevet manus'
    needs_audio = False

    def __init__(self, script=None, speed=1.0, words_per_second=3.2):
        self.script = script or DEMO_MEETING
        self.speed = speed
        self.word_dt = 1 / words_per_second
        self.task = None

    async def start(self, sink):
        self.sink = sink
        self.task = asyncio.create_task(self._run())

    async def _sleep(self, seconds):
        await asyncio.sleep(seconds * self.speed)

    async def _run(self):
        n = 0
        try:
            await self._sleep(0.6)
            for turn in self.script:
                for text, pause in turn:
                    n += 1
                    key = f'sim-{n}'
                    words = text.split()
                    self.sink.on_speech(True)
                    for i in range(len(words)):
                        await self._sleep(self.word_dt)
                        self.sink.on_partial(key, ' '.join(words[:i + 1]))
                    await self._sleep(0.35)
                    self.sink.on_speech(False)
                    await self._sleep(0.15)
                    self.sink.on_final(key, text)
                    await self._sleep(pause)
            self.sink.on_finished()
        except asyncio.CancelledError:
            pass

    @property
    def done(self):
        return self.task is not None and self.task.done()

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
