"""Stream a WAV file to Room as if it were a microphone.

  python -m app.audio_feed meeting.wav
  python -m app.audio_feed meeting.wav --source atech-mic --url ws://127.0.0.1:8765/ws/audio

This is also the reference for new audio sources (e.g. the Atech I2S microphone, 16 kHz):
open /ws/audio, send {"type":"start","source":...,"format":"pcm16","sample_rate":N}, wait for
"ready", send binary PCM16LE mono chunks in real time, then {"type":"stop"}. The server
resamples to 24 kHz, runs the VAD and owns the provider connection and its API key.
"""
import argparse
import asyncio
import json
import time
import wave
from array import array

from websockets.asyncio.client import connect


def read_mono(path):
    with wave.open(path, 'rb') as w:
        if w.getsampwidth() != 2:
            raise SystemExit('Bare 16-bit PCM WAV støttes.')
        rate, channels = w.getframerate(), w.getnchannels()
        data = array('h', w.readframes(w.getnframes()))
    if channels > 1:
        data = array('h', (sum(data[i:i + channels]) // channels for i in range(0, len(data), channels)))
    return rate, data.tobytes()


async def feed(path, url, source, chunk_ms=50, tail_s=2.5, verbose=True):
    rate, pcm = read_mono(path)
    step = rate * 2 * chunk_ms // 1000
    async with connect(url, max_size=2 ** 22) as ws:
        await ws.send(json.dumps({'type': 'start', 'source': source, 'format': 'pcm16', 'sample_rate': rate}))
        ready = json.loads(await ws.recv())
        if ready.get('type') != 'ready':
            raise SystemExit(f"Room avviste opptaket: {ready.get('message')}")
        if verbose:
            print(f"Strømmer {len(pcm) / 2 / rate:.1f} s til {ready['provider']['label']} …")
        start = time.monotonic()
        data = pcm + bytes(int(rate * tail_s) * 2)  # trailing silence lets the VAD close the last segment
        for i, offset in enumerate(range(0, len(data), step)):
            await ws.send(data[offset:offset + step])
            delay = start + (i + 1) * chunk_ms / 1000 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        await ws.send(json.dumps({'type': 'stop'}))
        async for raw in ws:
            message = json.loads(raw)
            if message.get('type') in ('stopped', 'stop'):
                break


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('wav')
    parser.add_argument('--url', default='ws://127.0.0.1:8765/ws/audio')
    parser.add_argument('--source', default='wav-file')
    args = parser.parse_args()
    asyncio.run(feed(args.wav, args.url, args.source))
    print('Ferdig. Se transkripsjonen i Room.')


if __name__ == '__main__':
    main()
