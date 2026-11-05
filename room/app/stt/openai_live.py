"""OpenAI Realtime transcription over a server-side WebSocket.

Protocol (developers.openai.com/api/docs/guides/realtime-transcription, Nov 2026):
connect to wss://api.openai.com/v1/realtime?intent=transcription with the API key, send
session.update with type "transcription", 24 kHz PCM and turn_detection null, then
input_audio_buffer.append (base64 PCM16) and input_audio_buffer.commit per speech turn.
gpt-live-transcribe streams ...transcription.delta while audio arrives and sends
...transcription.completed after each commit. It has no server VAD, so Room's own VAD
decides when to commit. The API key stays in this process and is never sent to browsers.
"""
import asyncio
import base64
import json
import logging

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI, InvalidHandshake

from ..config import SAMPLE_RATE
from .base import Transcriber, TranscriberError

log = logging.getLogger('room.stt.openai')

LEGACY_MODELS = ('gpt-4o-transcribe', 'gpt-4o-mini-transcribe', 'whisper-1')
FATAL_CODES = {'invalid_api_key', 'insufficient_quota', 'model_not_found', 'invalid_model',
               'unsupported_model', 'session_expired', 'permission_denied'}


def session_update(settings):
    transcription = {'model': settings.model}
    if settings.model.startswith(LEGACY_MODELS):
        # Older models take one language and fold vocabulary into the prompt.
        prompt = settings.prompt
        if settings.keywords:
            prompt = (prompt + ' Ord: ' + ', '.join(settings.keywords)).strip()
        if prompt:
            transcription['prompt'] = prompt
        if settings.languages:
            transcription['language'] = settings.languages[0]
    else:
        if settings.prompt:
            transcription['prompt'] = settings.prompt
        if settings.keywords:
            transcription['keywords'] = settings.keywords
        if settings.languages:
            transcription['languages'] = settings.languages
        if settings.delay and settings.model.startswith('gpt-live'):
            transcription['delay'] = settings.delay
    return {'type': 'session.update', 'session': {'type': 'transcription', 'audio': {'input': {
        'format': {'type': 'audio/pcm', 'rate': SAMPLE_RATE},
        'transcription': transcription,
        'turn_detection': None}}}}


def _clean(value):
    # Prompts and keywords must not contain <, > or line breaks.
    return ' '.join(str(value).replace('<', ' ').replace('>', ' ').split())


class OpenAILiveTranscriber(Transcriber):
    name = 'openai'
    destination = 'OpenAI (sky) · lyd lagres ikke av Room'

    def __init__(self, settings, reconnect_attempts=3):
        if not settings.api_key:
            raise TranscriberError('OPENAI_API_KEY mangler på serveren.')
        settings.prompt = _clean(settings.prompt)
        settings.keywords = [_clean(k) for k in settings.keywords if _clean(k)]
        self.s = settings
        self.model = settings.model
        self.label = f'OpenAI · {settings.model}'
        self.reconnect_attempts = reconnect_attempts
        self.ws = None
        self.reader = None
        self.closing = False
        self.pending = 0
        self.idle = asyncio.Event()
        self.idle.set()
        self.partials = {}
        self.dropped_bytes = 0

    async def start(self, sink):
        self.sink = sink
        await self._connect()

    async def _connect(self):
        try:
            self.ws = await connect(self.s.url, additional_headers={'Authorization': 'Bearer ' + self.s.api_key},
                                    open_timeout=10, ping_interval=20, ping_timeout=20, max_size=2 ** 22)
        except InvalidStatus as exc:
            status = exc.response.status_code
            hint = {401: 'OpenAI avviste API-nøkkelen (401).', 403: 'Nøkkelen har ikke tilgang til sanntidstranskripsjon (403).',
                    429: 'OpenAI-kvoten er brukt opp eller rate-begrenset (429).'}.get(status, f'OpenAI svarte {status}.')
            raise TranscriberError(hint) from exc
        except (OSError, TimeoutError, InvalidURI, InvalidHandshake) as exc:
            raise TranscriberError('Fikk ikke kontakt med OpenAI. Sjekk nettverket.') from exc
        await self.ws.send(json.dumps(session_update(self.s)))
        await self._await_session()
        self.reader = asyncio.create_task(self._read())

    async def _await_session(self):
        """Fail fast on a rejected configuration (wrong model, bad field) before audio flows."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 6
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return  # No confirmation, no error: proceed and let later errors surface.
            try:
                raw = await asyncio.wait_for(self.ws.recv(), remaining)
            except asyncio.TimeoutError:
                return
            except ConnectionClosed as exc:
                raise TranscriberError(f'OpenAI lukket forbindelsen ved oppstart ({exc.rcvd.reason if exc.rcvd else "ukjent grunn"}).') from exc
            event = json.loads(raw)
            if event.get('type') == 'error':
                error = event.get('error') or {}
                await self.ws.close()
                raise TranscriberError('OpenAI avviste oppsettet: ' + (error.get('message') or error.get('code') or 'ukjent feil'))
            if event.get('type') in ('session.updated', 'transcription_session.updated'):
                return

    async def _read(self):
        try:
            async for raw in self.ws:
                self._handle(json.loads(raw))
        except ConnectionClosed:
            pass
        except Exception:
            log.exception('OpenAI reader failed')
        if not self.closing:
            await self._recover()

    def _handle(self, event):
        kind = event.get('type', '')
        item = event.get('item_id')
        if kind == 'conversation.item.input_audio_transcription.delta':
            text = self.partials.get(item, '') + event.get('delta', '')
            self.partials[item] = text
            self.sink.on_partial(item, text)
        elif kind == 'conversation.item.input_audio_transcription.completed':
            text = event.get('transcript') or self.partials.get(item, '')
            self.partials.pop(item, None)
            self._resolve()
            self.sink.on_final(item, text.strip())
        elif kind == 'conversation.item.input_audio_transcription.failed':
            self.partials.pop(item, None)
            self._resolve()
            self.sink.on_failed(item, (event.get('error') or {}).get('message', 'transkripsjon feilet'))
        elif kind == 'error':
            error = event.get('error') or {}
            code = error.get('code') or ''
            if 'empty' in code or 'too_small' in code:
                self._resolve()  # A commit with too little audio; nothing to transcribe.
                return
            fatal = code in FATAL_CODES or error.get('type') in ('authentication_error', 'permission_error')
            self.sink.on_error('OpenAI: ' + (error.get('message') or code or 'ukjent feil'), fatal)

    def _resolve(self):
        self.pending = max(0, self.pending - 1)
        if not self.pending:
            self.idle.set()

    async def _recover(self):
        for _ in range(self.pending):
            self.sink.on_failed(None, 'connection_lost')
        self.pending = 0
        self.idle.set()
        self.partials.clear()
        self.ws = None
        for attempt in range(self.reconnect_attempts):
            await asyncio.sleep(0.5 * 2 ** attempt)
            if self.closing:
                return
            try:
                await self._connect()
                self.sink.on_error('Forbindelsen til OpenAI ble gjenopprettet. Litt tale kan mangle.', False)
                return
            except TranscriberError as exc:
                log.warning('Reconnect %s failed: %s', attempt + 1, exc)
        self.sink.on_error('Mistet forbindelsen til OpenAI. Opptaket er stoppet.', True)

    async def append(self, pcm):
        if not self.ws or self.closing:
            self.dropped_bytes += len(pcm)
            return
        try:
            await self.ws.send(json.dumps({'type': 'input_audio_buffer.append', 'audio': base64.b64encode(pcm).decode()}))
        except ConnectionClosed:
            self.dropped_bytes += len(pcm)

    async def commit(self):
        if not self.ws or self.closing:
            self.sink.on_failed(None, 'not_connected')
            return
        self.pending += 1
        self.idle.clear()
        try:
            await self.ws.send(json.dumps({'type': 'input_audio_buffer.commit'}))
        except ConnectionClosed:
            pass  # _recover resolves pending commits.

    async def finish(self, timeout=4.0):
        if self.pending:
            try:
                await asyncio.wait_for(self.idle.wait(), timeout)
            except asyncio.TimeoutError:
                for _ in range(self.pending):
                    self.sink.on_failed(None, 'timeout')
        await self.close()

    async def close(self):
        self.closing = True
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self.reader and self.reader is not asyncio.current_task():
            self.reader.cancel()
            try:
                await self.reader
            except (asyncio.CancelledError, Exception):
                pass
        self.ws = None
