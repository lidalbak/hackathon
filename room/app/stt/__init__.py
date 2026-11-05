"""Transcription providers. Pick one with ROOM_STT_PROVIDER (default: openai when a key is set).

Add a provider by registering a factory: PROVIDERS['name'] = lambda settings: MyTranscriber(...).
"""
from ..config import stt_settings
from .base import Transcriber, TranscriberError, VadOnlyTranscriber
from .openai_live import OpenAILiveTranscriber
from .scripted import ScriptedTranscriber

PROVIDERS = {
    'openai': OpenAILiveTranscriber,
    'vad-only': lambda settings: VadOnlyTranscriber(),
}


def make_transcriber(kind='mic', settings=None, **options):
    if kind == 'simulated':
        return ScriptedTranscriber(**options)
    settings = settings or stt_settings()
    factory = PROVIDERS.get(settings.provider)
    if not factory:
        raise TranscriberError(f'Ukjent transkripsjonsleverandør: {settings.provider}')
    return factory(settings)


def describe_provider(settings=None):
    """What the UI shows before recording starts: where audio goes and whether text comes back."""
    settings = settings or stt_settings()
    if settings.provider == 'openai':
        if not settings.api_key:
            return {'provider': 'openai', 'ready': False, 'transcribes': True, 'model': settings.model,
                    'label': 'OpenAI · mangler nøkkel', 'destination': 'OpenAI (sky)',
                    'note': 'Sett OPENAI_API_KEY på serveren og start den på nytt.'}
        return {'provider': 'openai', 'ready': True, 'transcribes': True, 'model': settings.model,
                'label': f'OpenAI · {settings.model}', 'destination': OpenAILiveTranscriber.destination,
                'note': 'Bare tale (pluss 0,3 s før) strømmes. Nøkkelen blir på serveren.'}
    if settings.provider == 'vad-only':
        return {'provider': 'vad-only', 'ready': True, 'transcribes': False, 'model': None,
                'label': VadOnlyTranscriber.label, 'destination': VadOnlyTranscriber.destination,
                'note': 'Tester mikrofon og taledeteksjon. Sett OPENAI_API_KEY for å få tekst.'}
    return {'provider': settings.provider, 'ready': settings.provider in PROVIDERS, 'transcribes': True,
            'model': settings.model, 'label': settings.provider, 'destination': 'Egendefinert', 'note': ''}


__all__ = ['Transcriber', 'TranscriberError', 'make_transcriber', 'describe_provider', 'PROVIDERS',
           'ScriptedTranscriber', 'OpenAILiveTranscriber', 'VadOnlyTranscriber']
