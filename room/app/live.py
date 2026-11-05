"""Live layer: one recording session at a time, and a broadcast hub for every open screen.

Pipeline per session:
  audio source (browser mic over /ws/audio, WAV feeder, later Atech mic)
    -> Resampler -> EnergyVAD -> Transcriber (append/commit)        partial text -> live captions
    -> final segments -> TurnAssembler -> Engine.handle_turn         only complete turns become tasks
A scripted transcriber skips the audio part and drives the same events itself.
"""
import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from .audio import EnergyVAD, Resampler, WavRecorder
from .config import SAMPLE_RATE, retention, vad_settings
from .conversation import TurnAssembler
from .store import ROOT
from .stt import TranscriberError, make_transcriber

log = logging.getLogger('room.live')
SEND_CHUNK = SAMPLE_RATE * 2 // 10  # 100 ms of PCM16 per provider message


class Busy(Exception):
    pass


class Hub:
    """Fan-out of small JSON messages to /ws/live clients. Slow clients lose old messages."""

    def __init__(self):
        self.clients = set()

    def subscribe(self):
        queue = asyncio.Queue(maxsize=256)
        self.clients.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.clients.discard(queue)

    def publish(self, message):
        for queue in list(self.clients):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(message)


class RecordingSession:
    def __init__(self, recorder, kind, source, transcriber, sample_rate=SAMPLE_RATE, turn_scale=1.0):
        self.recorder = recorder
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.source = kind, source
        self.transcriber = transcriber
        self.resampler = Resampler(sample_rate)
        self.vad = EnergyVAD(vad_settings()) if transcriber.needs_audio else None
        self.assembler = TurnAssembler(self._turn_closed, scale=turn_scale)
        self.state = 'starting'
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started = time.monotonic()
        self.audio_seconds = 0.0
        self.speech_seconds = 0.0
        self.segments = 0
        self.speaking = False
        self.partial = ''
        self.out = bytearray()
        self.notice = None
        self.wav = None
        if retention().store_audio and transcriber.needs_audio:
            self.wav = WavRecorder(ROOT / 'data' / 'recordings' / f'{self.started_at[:19].replace(":", "")}-{self.id}.wav')
        self.stop_listeners = []
        self.turn_jobs = set()
        self.ticker = None
        self.stop_reason = None
        self._last_level = 0.0
        self._stopping = None

    # ------------------------------------------------------------ lifecycle

    async def start(self):
        await self.transcriber.start(self)
        self.state = 'recording'
        self.ticker = asyncio.create_task(self._tick())
        self.publish_live()

    async def _tick(self):
        while self.state == 'recording':
            await asyncio.sleep(0.1)
            self.assembler.tick()

    def stop_soon(self, reason, error=False):
        if self.state == 'recording':
            asyncio.get_running_loop().create_task(self.stop(reason, error))

    async def stop(self, reason='Stoppet', error=False):
        if self._stopping:
            return await self._stopping
        self._stopping = asyncio.get_running_loop().create_future()
        try:
            self.state = 'stopping'
            self.stop_reason = reason
            self.recorder.changed()
            if self.vad:
                for event in self.vad.flush():
                    await self._vad_event(event)
            await self._send_audio(force=True)
            try:
                await self.transcriber.finish(timeout=4.0)
            except Exception:
                log.exception('Transcriber finish failed')
            self.assembler.flush()
            if self.turn_jobs:
                await asyncio.wait(self.turn_jobs, timeout=15)
            if self.ticker:
                self.ticker.cancel()
            if self.wav:
                self.wav.close()
        finally:
            self.state = 'stopped'
            self.partial = ''
            self.speaking = False
            for listener in self.stop_listeners:
                listener(reason)
            self.recorder.ended(self, reason, error)
            self._stopping.set_result(None)

    # ---------------------------------------------------------------- audio

    async def feed(self, pcm):
        if self.state != 'recording' or not self.vad:
            return
        pcm = self.resampler.process(pcm)
        if self.wav:
            self.wav.write(pcm)
        self.audio_seconds += len(pcm) / 2 / SAMPLE_RATE
        for event in self.vad.feed(pcm):
            await self._vad_event(event)
        now = time.monotonic()
        if now - self._last_level >= 0.1:
            self._last_level = now
            self.recorder.hub.publish({'type': 'level', 'db': round(self.vad.last_db, 1), 'speaking': self.speaking,
                                       'floor': round(self.vad.floor, 1)})

    async def _vad_event(self, event):
        kind = event[0]
        if kind == 'start':
            self.speaking = True
            self.assembler.speech(True)
            self.publish_live()
        elif kind == 'audio':
            self.out += event[1]
            await self._send_audio()
        elif kind == 'end':
            await self._send_audio(force=True)
            self.speaking = False
            self.segments += 1
            self.speech_seconds += event[2]
            self.assembler.speech(False)
            await self.transcriber.commit()
            self.publish_live()

    async def _send_audio(self, force=False):
        while len(self.out) >= SEND_CHUNK or (force and self.out):
            chunk = bytes(self.out[:SEND_CHUNK])
            del self.out[:SEND_CHUNK]
            await self.transcriber.append(chunk)

    # --------------------------------------------------- transcriber sink

    def on_partial(self, key, text):
        self.partial = text
        self.publish_live()

    def on_final(self, key, text):
        self.partial = ''
        self.assembler.final(text)
        self.publish_live()

    def on_failed(self, key, reason):
        self.assembler.failed()
        if reason == 'not_transcribed':
            self.notice = f'Tale oppdaget ({self.segments}. segment), ikke transkribert: ingen leverandør valgt.'
        self.partial = ''
        self.publish_live()

    def on_error(self, message, fatal):
        log.warning('Transcriber: %s', message)
        self.notice = message
        self.publish_live()
        if fatal:
            self.stop_soon(message, error=True)

    def on_speech(self, active):
        self.speaking = active
        self.assembler.speech(active)
        if not active:
            self.segments += 1
        self.publish_live()

    def on_finished(self):
        self.stop_soon('Demomøtet er ferdig.')

    # ---------------------------------------------------------------- turns

    def _turn_closed(self, turn):
        turn['source'] = 'simulated' if self.kind == 'simulated' else self.source
        job = asyncio.get_running_loop().create_task(self.recorder.engine.handle_turn(turn))
        self.turn_jobs.add(job)
        job.add_done_callback(self.turn_jobs.discard)
        self.publish_live()

    def live(self):
        return {'session_id': self.id, 'segments': self.assembler.open_text, 'partial': self.partial,
                'speaking': self.speaking, 'notice': self.notice}

    def publish_live(self):
        self.recorder.hub.publish({'type': 'live', **self.live()})

    def describe(self):
        return {'active': self.state in ('starting', 'recording', 'stopping'), 'state': self.state, 'id': self.id,
                'kind': self.kind, 'source': self.source, 'started_at': self.started_at,
                'elapsed_s': round(time.monotonic() - self.started, 1), 'audio_seconds': round(self.audio_seconds, 1),
                'speech_seconds': round(self.speech_seconds, 1), 'segments': self.segments,
                'provider': self.transcriber.describe(), 'store_audio': bool(self.wav)}


class Recorder:
    def __init__(self, engine, hub):
        self.engine, self.hub = engine, hub
        self.session = None
        self.last_stop = None

    async def start(self, kind='mic', source='browser-mic', sample_rate=SAMPLE_RATE, transcriber=None, **options):
        if self.session and self.session.state in ('starting', 'recording', 'stopping'):
            raise Busy('Et opptak pågår allerede. Stopp det først.')
        if not 8000 <= int(sample_rate) <= 96000:
            raise TranscriberError('Ugyldig samplingsrate.')
        transcriber = transcriber or make_transcriber(kind, **options)
        turn_scale = getattr(transcriber, 'speed', 1.0) if kind == 'simulated' else 1.0
        session = RecordingSession(self, kind, source, transcriber, int(sample_rate), turn_scale=turn_scale)
        self.session = session
        try:
            await session.start()
        except Exception:
            self.session = None
            session.state = 'stopped'
            await transcriber.close()
            raise
        self.changed()
        return session

    async def stop(self, reason='Stoppet'):
        if self.session:
            await self.session.stop(reason)

    def ended(self, session, reason, error):
        if self.session is session:
            self.last_stop = {'reason': reason, 'error': error, 'at': datetime.now(timezone.utc).isoformat(),
                              'kind': session.kind}
        self.hub.publish({'type': 'live', 'session_id': session.id, 'segments': [], 'partial': '', 'speaking': False, 'notice': None})
        self.changed()

    def changed(self):
        self.hub.publish({'type': 'state'})

    def state(self):
        current = self.session.describe() if self.session else {'active': False, 'state': 'idle'}
        return {**current, 'last_stop': self.last_stop}

    def live(self):
        if self.session and self.session.state in ('recording', 'stopping'):
            return self.session.live()
        return {'segments': [], 'partial': '', 'speaking': False, 'notice': None}
