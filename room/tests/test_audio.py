import math
import random
import unittest
from array import array

from app.audio import EnergyVAD, Resampler, level_db, samples, to_bytes
from app.config import SAMPLE_RATE, VADSettings


def tone(seconds, amplitude, rate=SAMPLE_RATE, seed=1):
    """Speech-like signal: a 180 Hz voice with syllable-rate amplitude modulation plus noise."""
    rng = random.Random(seed)
    n = int(seconds * rate)
    return array('h', (int(amplitude * (0.6 + 0.4 * math.sin(2 * math.pi * 4 * i / rate))
                           * math.sin(2 * math.pi * 180 * i / rate) + rng.gauss(0, 40)) for i in range(n)))


def noise(seconds, sigma=30, rate=SAMPLE_RATE, seed=2):
    rng = random.Random(seed)
    return array('h', (int(rng.gauss(0, sigma)) for _ in range(int(seconds * rate))))


def run_vad(signal, settings=None):
    vad = EnergyVAD(settings or VADSettings())
    events = []
    pcm = to_bytes(signal)
    for i in range(0, len(pcm), 4800):  # 100 ms chunks, as the pipeline receives them
        events += vad.feed(pcm[i:i + 4800])
    return events + vad.flush()


class VADTests(unittest.TestCase):
    def test_two_utterances_give_two_segments_with_preroll(self):
        signal = noise(1.0) + tone(1.2, 6000) + noise(1.2) + tone(0.6, 6000, seed=3) + noise(1.0)
        events = run_vad(signal)
        starts = [e[1] for e in events if e[0] == 'start']
        ends = [e for e in events if e[0] == 'end']
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(ends), 2)
        self.assertAlmostEqual(starts[0], 1.0 - 0.3 + 0.08, delta=0.12)  # onset minus pre-roll
        self.assertGreater(ends[0][2], 1.0)  # voiced seconds of the first utterance
        audio = sum(len(e[1]) for e in events if e[0] == 'audio') / 2 / SAMPLE_RATE
        self.assertLess(audio, 3.6)  # only speech, pre-roll and hangover are forwarded, not the silence

    def test_silence_and_steady_room_noise_are_not_speech(self):
        quiet = noise(3.0, sigma=20)
        loud_hum = array('h', (int(900 * math.sin(2 * math.pi * 50 * i / SAMPLE_RATE)) for i in range(SAMPLE_RATE * 4)))
        self.assertFalse([e for e in run_vad(quiet) if e[0] == 'start'])
        # A constant hum above the absolute floor is learned as the noise floor after a moment.
        starts = [e for e in run_vad(noise(1.0) + loud_hum) if e[0] == 'start']
        self.assertLessEqual(len(starts), 1)

    def test_long_monologue_is_split(self):
        events = run_vad(noise(0.5) + tone(9.0, 6000) + noise(1.0), VADSettings(max_segment_s=4.0))
        self.assertGreaterEqual(len([e for e in events if e[0] == 'end']), 3)

    def test_flush_closes_open_segment(self):
        vad = EnergyVAD()
        vad.feed(to_bytes(noise(0.5) + tone(0.8, 6000)))
        self.assertTrue(vad.speaking)
        self.assertEqual(vad.flush()[0][0], 'end')


class ResamplerTests(unittest.TestCase):
    def test_rates_and_continuity(self):
        for src in (16000, 44100, 48000):
            r = Resampler(src, SAMPLE_RATE)
            sine = array('h', (int(8000 * math.sin(2 * math.pi * 440 * i / src)) for i in range(src)))
            pcm = to_bytes(sine)
            out = b''.join(r.process(pcm[i:i + 1000]) for i in range(0, len(pcm), 1000))
            self.assertAlmostEqual(len(out) / 2, SAMPLE_RATE, delta=3)
            self.assertAlmostEqual(level_db(samples(out)), level_db(sine), delta=0.5)

    def test_passthrough(self):
        pcm = to_bytes(tone(0.1, 3000))
        self.assertEqual(Resampler(SAMPLE_RATE).process(pcm), pcm)


if __name__ == '__main__':
    unittest.main()
