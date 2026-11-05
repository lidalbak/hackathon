import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from .config import SAMPLE_RATE, retention, stt_settings
from .engine import engine
from .live import Busy, Hub, Recorder
from .store import seed
from .stt import TranscriberError, describe_provider

log = logging.getLogger('room')
hub = Hub()
recorder = Recorder(engine, hub)
engine.listeners.append(lambda: hub.publish({'type': 'state'}))
LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1'}


@asynccontextmanager
async def lifespan(app):
    seed()
    yield
    await recorder.stop('Serveren stopper.')
    for job in list(engine.jobs):
        job.cancel()


def same_origin(headers):
    """Block other websites from driving Room through the visitor's browser (CSRF / WebSocket hijacking).
    Non-browser clients (audio feeder, hardware bridge, tests) send no Origin and are allowed."""
    origin = headers.get('origin')
    if not origin:
        return True
    o, h = urlparse(origin), urlparse('//' + headers.get('host', ''))
    if o.netloc == h.netloc:
        return True
    return o.hostname in LOCAL_HOSTS and h.hostname in LOCAL_HOSTS and o.port == h.port


async def home(request):
    return FileResponse(Path(__file__).parent / 'static' / 'index.html')


def full_state():
    keep = retention()
    settings = stt_settings()
    return {**engine.state(), 'recording': recorder.state(), 'live': recorder.live(),
            'stt': {**describe_provider(settings), 'languages': settings.languages},
            'retention': {'audio': 'disk' if keep.store_audio else 'off', 'transcript': keep.transcript}}


async def state(request):
    return JSONResponse(full_state())


async def action(request):
    if not same_origin(request.headers):
        return JSONResponse({'error': 'Forespørsler fra andre nettsider er blokkert.'}, 403)
    try:
        body = await request.json()
        command = body.get('command')
        if command == 'submit':
            text = body.get('text', '').strip()
            if not text or len(text) > 4000:
                return JSONResponse({'error': 'Bruk 1–4000 tegn.'}, 400)
            return JSONResponse(await engine.submit(text))
        if command == 'start_demo':
            try:
                session = await recorder.start('simulated', 'simulated')
            except Busy as exc:
                return JSONResponse({'error': str(exc)}, 409)
            return JSONResponse({'ok': True, 'session_id': session.id})
        if command == 'stop_recording':
            if not recorder.session or not recorder.state()['active']:
                return JSONResponse({'error': 'Ingen opptak pågår.'}, 409)
            asyncio.get_running_loop().create_task(recorder.stop(body.get('reason') or 'Stoppet fra skjermen.'))
            return JSONResponse({'ok': True})
        if command == 'clear_transcript':
            engine.clear_transcript()
            return JSONResponse({'ok': True})
        task = engine.tasks.get(body.get('id'))
        if not task:
            return JSONResponse({'error': 'Ukjent oppgave.'}, 404)
        if command == 'clarify':
            if task['status'] != 'clarify':
                return JSONResponse({'error': 'Oppgaven venter ikke på avklaring.'}, 409)
            product = body.get('product'); days = body.get('days')
            if product not in ['X', 'Y', 'Z'] or type(days) is not int or days not in [7, 30, 90]:
                return JSONResponse({'error': 'Velg produkt X/Y/Z og 7/30/90 dager.'}, 400)
            task.update(product=product, days=days, status='queued')
            engine.log(task, 'clarified', f'Valgt på skjermen: produkt {product}, {days} dager')
            engine.launch(task)
        elif command == 'cancel':
            engine.cancel(task, 'Avbrutt på skjermen')
        elif command == 'show':
            if task['status'] != 'ready':
                return JSONResponse({'error': 'Resultatet er ikke klart.'}, 409)
            task['shown'] = True
            task['shown_at'] = datetime.now(timezone.utc).isoformat()
        elif command == 'draft':
            if task['status'] != 'ready':
                return JSONResponse({'error': 'Resultatet er ikke klart.'}, 409)
            engine.make_draft(task)
        else:
            return JSONResponse({'error': 'Ukjent handling.'}, 400)
        engine.save()
        return JSONResponse({'ok': True})
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({'error': 'Ugyldig forespørsel.'}, 400)
    except TranscriberError as exc:
        return JSONResponse({'error': str(exc)}, 502)
    except Exception:
        log.exception('Action failed')
        return JSONResponse({'error': 'Kunne ikke behandle forespørselen. Sjekk serverloggen og API-oppsettet.'}, 502)


async def audio_ws(ws):
    """Audio ingest. Protocol:
    client -> {"type":"start","source":"browser-mic","format":"pcm16","sample_rate":24000}
    server -> {"type":"ready",...} | {"type":"error","message":...}
    client -> binary PCM16LE mono frames ... -> {"type":"stop"}
    server -> {"type":"stop","reason":...} when the session ends elsewhere, {"type":"stopped"} after a client stop.
    """
    if not same_origin(ws.headers):
        await ws.close(code=1008)
        return
    await ws.accept()
    session = None
    notifier = None
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 10)
        if hello.get('type') != 'start' or hello.get('format', 'pcm16') != 'pcm16':
            await ws.send_json({'type': 'error', 'message': 'Forventet start-melding med PCM16-lyd.'})
            return
        try:
            session = await recorder.start('mic', str(hello.get('source') or 'browser-mic')[:40],
                                           int(hello.get('sample_rate') or SAMPLE_RATE))
        except (Busy, TranscriberError, ValueError) as exc:
            await ws.send_json({'type': 'error', 'message': str(exc)})
            return
        stopped = asyncio.Event()
        session.stop_listeners.append(lambda reason: stopped.set())

        async def notify():
            await stopped.wait()
            try:
                await ws.send_json({'type': 'stop', 'reason': session.stop_reason})
            except Exception:
                pass

        notifier = asyncio.create_task(notify())
        await ws.send_json({'type': 'ready', 'session_id': session.id, 'provider': session.transcriber.describe(),
                            'sample_rate': SAMPLE_RATE, 'store_audio': bool(session.wav)})
        while True:
            message = await ws.receive()
            if message['type'] == 'websocket.disconnect':
                break
            if message.get('bytes'):
                await session.feed(message['bytes'])
            elif message.get('text'):
                data = json.loads(message['text'])
                if data.get('type') == 'stop':
                    await recorder.stop('Stoppet av brukeren.')
                    try:
                        await ws.send_json({'type': 'stopped'})
                    except Exception:
                        pass  # The client may already have closed after the 'stop' notice.
                    break
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError, RuntimeError):
        pass
    finally:
        if session and session.state in ('starting', 'recording'):
            await recorder.stop('Forbindelsen til lydkilden ble brutt.')
        if notifier:
            notifier.cancel()
        try:
            await ws.close()
        except Exception:
            pass


async def live_ws(ws):
    """Broadcast of captions, levels and state nudges to every open screen."""
    if not same_origin(ws.headers):
        await ws.close(code=1008)
        return
    await ws.accept()
    queue = hub.subscribe()

    async def pump():
        while True:
            await ws.send_json(await queue.get())

    sender = asyncio.create_task(pump())
    try:
        await ws.send_json({'type': 'live', **recorder.live()})
        while (await ws.receive())['type'] != 'websocket.disconnect':
            pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        hub.unsubscribe(queue)


app = Starlette(lifespan=lifespan, routes=[
    Route('/', home), Route('/api/state', state), Route('/api/action', action, methods=['POST']),
    WebSocketRoute('/ws/audio', audio_ws), WebSocketRoute('/ws/live', live_ws),
    Mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'))])
