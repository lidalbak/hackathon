"""PCM16 helpers: level metering, resampling, voice activity detection and opt-in WAV storage.

Every audio source (browser microphone, WAV file, a future Atech microphone) delivers
mono PCM16 little-endian. The pipeline resamples to config.SAMPLE_RATE, runs the VAD and
forwards only speech (plus a short pre-roll) to the transcription provider.
"""
import math
import sys
import wave
from array import array
from collections import deque
from pathlib import Path

from .config import SAMPLE_RATE, VADSettings


def samples(pcm):
    data = array('h')
    data.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if sys.byteorder == 'big':
        data.byteswap()
    return data


def to_bytes(data):
    if sys.byteorder == 'big':
        data = array('h', data)
        data.byteswap()
    return data.tobytes()


def level_db(data):
    if not data:
        return -100.0
    rms = math.sqrt(sum(s * s for s in data) / len(data)) / 32768
    return 20 * math.log10(rms + 1e-9)


class Resampler:
    """Streaming linear-interpolation resampler for mono PCM16. Keeps state between chunks."""

    def __init__(self, src_rate, dst_rate=SAMPLE_RATE):
        self.src, self.dst = src_rate, dst_rate
        self.step = src_rate / dst_rate
        self.t = 0.0
        self.last = 0

    def process(self, pcm):
        if self.src == self.dst:
            return pcm
        x = samples(pcm)
        n = len(x)
        if not n:
            return b''
        out = array('h')
        t, prev = self.t, self.last
        while t < n:
            i = int(t)
            a = prev if i == 0 else x[i - 1]
            b = x[i] if i < n else x[-1]
            out.append(int(a + (b - a) * (t - i)))
            t += self.step
        self.t, self.last = t - n, x[-1]
        return to_bytes(out)


class EnergyVAD:
    """Energy VAD with an adaptive noise floor (15th percentile of the last ~3 s).

    feed() returns events in order:
      ('start', t)          speech segment opens (t = audio seconds, pre-roll included)
      ('audio', bytes)      audio belonging to the open segment
      ('end', t, voiced_s)  segment closes after trailing silence or the max length
    """

    FRAME_MS = 20

    def __init__(self, settings=None, rate=SAMPLE_RATE):
        self.s = settings or VADSettings()
        self.rate = rate
        self.frame_bytes = rate * self.FRAME_MS // 1000 * 2
        self.buf = bytearray()
        self.preroll = deque(maxlen=max(1, self.s.preroll_ms // self.FRAME_MS))
        self.history = deque(maxlen=150)
        self.floor = -60.0
        self.speaking = False
        self.voiced_run = self.silence_run = 0
        self.segment_frames = self.voiced_frames = 0
        self.frames = 0
        self.last_db = -100.0

    @property
    def t(self):
        return self.frames * self.FRAME_MS / 1000

    def _update_floor(self):
        if len(self.history) >= 25 and self.frames % 5 == 0:
            ordered = sorted(self.history)
            self.floor = min(-20.0, ordered[int(len(ordered) * 0.15)])

    def feed(self, pcm):
        self.buf += pcm
        events = []
        while len(self.buf) >= self.frame_bytes:
            frame = bytes(self.buf[:self.frame_bytes])
            del self.buf[:self.frame_bytes]
            self._frame(frame, events)
        return events

    def _frame(self, frame, events):
        db = level_db(samples(frame))
        self.last_db = db
        self.frames += 1
        self.history.append(db)
        self._update_floor()
        voiced = db > max(self.floor + self.s.threshold_db, self.s.min_level_db)
        if not self.speaking:
            self.preroll.append(frame)
            self.voiced_run = self.voiced_run + 1 if voiced else 0
            if self.voiced_run * self.FRAME_MS >= self.s.start_ms:
                self.speaking = True
                self.silence_run = 0
                self.segment_frames = len(self.preroll)
                self.voiced_frames = self.voiced_run
                events.append(('start', self.t - len(self.preroll) * self.FRAME_MS / 1000))
                events.append(('audio', b''.join(self.preroll)))
                self.preroll.clear()
            return
        events.append(('audio', frame))
        self.segment_frames += 1
        if voiced:
            self.voiced_frames += 1
            self.silence_run = 0
        else:
            self.silence_run += 1
        length_s = self.segment_frames * self.FRAME_MS / 1000
        if self.silence_run * self.FRAME_MS >= self.s.silence_ms:
            self._end(events)
        elif length_s >= self.s.max_segment_s or (length_s >= self.s.max_segment_s * 0.8 and self.silence_run >= 5):
            # Long monologue: split at a pause if possible, otherwise hard split, and keep listening.
            self._end(events)
            self.speaking = True
            self.segment_frames = self.voiced_frames = 0
            events.append(('start', self.t))

    def _end(self, events):
        events.append(('end', self.t, self.voiced_frames * self.FRAME_MS / 1000))
        self.speaking = False
        self.voiced_run = self.silence_run = 0

    def flush(self):
        events = []
        if self.speaking:
            self._end(events)
        self.buf.clear()
        self.preroll.clear()
        return events


class WavRecorder:
    """Writes the session's audio to disk. Only used when ROOM_STORE_AUDIO=1."""

    def __init__(self, path: Path, rate=SAMPLE_RATE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.file = wave.open(str(path), 'wb')
        self.file.setnchannels(1)
        self.file.setsampwidth(2)
        self.file.setframerate(rate)

    def write(self, pcm):
        self.file.writeframes(pcm)

    def close(self):
        self.file.close()
