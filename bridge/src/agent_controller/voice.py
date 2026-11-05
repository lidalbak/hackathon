"""Speech to text with local Whisper (faster-whisper). Optional: pip install faster-whisper.

The model downloads once (base: about 150 MB) and runs on the CPU. Audio comes from the
board as 16 kHz mono 16-bit PCM.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)


class Whisper:
    def __init__(self, model: str = "base", language: Optional[str] = None) -> None:
        self.model_name = model
        self.language = language or None
        self._model = None
        self._ready = threading.Event()
        self._error: Optional[str] = None

    def load_in_background(self) -> None:
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            from faster_whisper import WhisperModel

            log.info("loading Whisper %s (first time downloads it)...", self.model_name)
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            log.info("Whisper %s ready", self.model_name)
        except Exception as exc:  # missing package or no network: voice stays off
            self._error = str(exc)
            log.warning("voice is off: %s", exc)
        finally:
            self._ready.set()

    def __call__(self, pcm: bytes) -> str:
        self._ready.wait(120)
        if self._model is None:
            raise RuntimeError(self._error or "Whisper is not loaded")
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(audio, language=self.language, beam_size=1, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()
