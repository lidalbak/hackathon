"""Runtime settings, read from the environment once per call so tests can override them.

Nothing here is secret except OPENAI_API_KEY, which never leaves the backend.
"""
import os
from dataclasses import dataclass, field

SAMPLE_RATE = 24000  # Canonical PCM16 mono rate; OpenAI realtime requires 24 kHz.


def _list(name, default):
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    return [part.strip() for part in raw.split(',') if part.strip()]


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


@dataclass
class STTSettings:
    provider: str            # openai | vad-only | none
    model: str
    languages: list
    delay: str
    prompt: str
    keywords: list = field(default_factory=list)
    api_key: str | None = None
    url: str = 'wss://api.openai.com/v1/realtime?intent=transcription'


@dataclass
class Retention:
    store_audio: bool        # off unless ROOM_STORE_AUDIO=1
    transcript: str          # memory | disk


@dataclass
class VADSettings:
    threshold_db: float = 9.0      # above adaptive noise floor
    min_level_db: float = -50.0    # absolute floor in dBFS
    start_ms: int = 80             # voiced time before a segment opens
    silence_ms: int = 650          # trailing silence that closes a segment
    preroll_ms: int = 300
    max_segment_s: float = 18.0


def stt_settings():
    key = os.getenv('OPENAI_API_KEY') or None
    provider = os.getenv('ROOM_STT_PROVIDER') or ('openai' if key else 'vad-only')
    return STTSettings(
        provider=provider,
        model=os.getenv('ROOM_STT_MODEL', 'gpt-live-transcribe'),
        languages=_list('ROOM_STT_LANGUAGES', ['no']),
        delay=os.getenv('ROOM_STT_DELAY', 'low'),
        prompt=os.getenv('ROOM_STT_PROMPT',
            'Et produktmøte på norsk på et møterom. Deltakerne snakker om kunder, '
            'produkt X, produkt Y og produkt Z, bruk, aktivering og tidsperioder.'),
        keywords=_list('ROOM_STT_KEYWORDS', ['produkt X', 'produkt Y', 'produkt Z', 'Room', 'GitHub-issue', 'dashboard']),
        api_key=key,
        url=os.getenv('ROOM_STT_URL', 'wss://api.openai.com/v1/realtime?intent=transcription'),
    )


def retention():
    mode = os.getenv('ROOM_TRANSCRIPT_RETENTION', 'memory')
    return Retention(store_audio=os.getenv('ROOM_STORE_AUDIO') == '1',
                     transcript='disk' if mode == 'disk' else 'memory')


def vad_settings():
    return VADSettings(
        threshold_db=_float('ROOM_VAD_THRESHOLD_DB', 9.0),
        min_level_db=_float('ROOM_VAD_MIN_DB', -50.0),
        silence_ms=int(_float('ROOM_VAD_SILENCE_MS', 650)),
    )
