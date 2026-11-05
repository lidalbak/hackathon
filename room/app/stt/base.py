"""Transcriber interface. A transcriber turns committed speech segments into text.

The recording session owns the VAD and decides when a segment ends; the transcriber only
streams audio, reports partial text while someone speaks, and a final text per commit.
Callbacks on the sink are synchronous and must not block.
"""


class TranscriberError(Exception):
    """Startup or connection failure that the user must see."""


class Sink:
    def on_partial(self, key, text): ...
    def on_final(self, key, text): ...
    def on_failed(self, key, reason): ...
    def on_error(self, message, fatal): ...
    def on_speech(self, active): ...   # only for transcribers that do not take audio
    def on_finished(self): ...         # the source ended on its own (scripted demo)


class Transcriber:
    name = 'base'
    label = 'Ingen transkripsjon'
    model = None
    destination = 'Lokalt'
    needs_audio = True      # False: the transcriber produces its own speech events (scripted demo)
    transcribes = True      # False: segments are detected but never turned into text

    async def start(self, sink):
        self.sink = sink

    async def append(self, pcm):
        pass

    async def commit(self):
        pass

    async def finish(self, timeout=4.0):
        """Wait for outstanding commits (bounded), then release resources."""
        await self.close()

    async def close(self):
        pass

    def describe(self):
        return {'provider': self.name, 'label': self.label, 'model': self.model,
                'destination': self.destination, 'transcribes': self.transcribes}


class VadOnlyTranscriber(Transcriber):
    """Microphone test without a provider: the VAD runs, nothing is transcribed or sent anywhere."""
    name = 'vad-only'
    label = 'Mikrofontest · ingen transkripsjon'
    destination = 'Ingen · lyd forlater ikke maskinen'
    transcribes = False

    async def commit(self):
        self.sink.on_failed(None, 'not_transcribed')
