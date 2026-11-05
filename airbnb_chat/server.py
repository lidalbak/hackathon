"""Airbnb-style inbox: a static chat UI plus a JSON API that reads and writes it.

Environment:
  CHAT_DB           SQLite file (default airbnb_chat/data/chat.db)
  CHAT_API_TOKEN    if set, every page and API call needs it (Bearer header, or open /?token=... once)
  CHAT_WEBHOOK_URL  if set, every new message is POSTed there as JSON
  CHAT_CORS_ORIGINS comma-separated browser origins allowed to call the API (e.g. http://localhost:3000)
"""
import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import db

log = logging.getLogger('airbnb_chat')
STATIC = Path(__file__).parent / 'static'
LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1'}
COOKIE = 'chat_token'
TYPING_SECONDS = 8


def cors_origins():
    return [o.strip() for o in os.environ.get('CHAT_CORS_ORIGINS', '').split(',') if o.strip()]


class Hub:
    """Fan-out of live events to every open /api/events stream."""

    def __init__(self):
        self.queues = set()
        self.tasks = set()
        self.typing = {}  # conversation id -> (sender, expires); lets polling clients see typing too

    def typing_of(self, cid):
        sender, until = self.typing.get(cid, (None, 0))
        return sender if until > time.monotonic() else None

    def publish(self, event):
        for queue in list(self.queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a stalled client misses events; the UI refetches on reconnect

    def notify(self, event):
        self.publish(event)
        url = os.environ.get('CHAT_WEBHOOK_URL')
        if url and event['type'] == 'message.created':
            task = asyncio.create_task(post_webhook(url, event))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)


hub = Hub()


async def post_webhook(url, event):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            (await client.post(url, json=event)).raise_for_status()
    except Exception as exc:
        log.warning('Webhook to %s failed: %s', url, exc)


# ---- access control ---------------------------------------------------------

def authorized(request):
    token = os.environ.get('CHAT_API_TOKEN')
    if not token:
        return True
    header = request.headers.get('authorization', '')
    given = header[7:] if header.lower().startswith('bearer ') else request.cookies.get(COOKIE, '')
    return secrets.compare_digest(given.encode(), token.encode())


def same_origin(request):
    """Stop other websites from writing through a visitor's browser. Clients without Origin are allowed."""
    origin = request.headers.get('origin')
    if not origin or origin in cors_origins():
        return True
    o, h = urlparse(origin), urlparse('//' + request.headers.get('host', ''))
    if o.netloc == h.netloc:
        return True
    return o.hostname in LOCAL_HOSTS and h.hostname in LOCAL_HOSTS and o.port == h.port


def api(handler):
    @wraps(handler)
    async def wrapped(request):
        if not authorized(request):
            return JSONResponse({'error': 'Missing or invalid token. Send Authorization: Bearer <token>.'}, 401)
        if request.method != 'GET' and not same_origin(request):
            return JSONResponse({'error': 'Cross-origin writes are blocked. Add the origin to CHAT_CORS_ORIGINS.'}, 403)
        try:
            result = await handler(request)
        except db.NotFound as exc:
            return JSONResponse({'error': str(exc)}, 404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({'error': str(exc)}, 400)
        if isinstance(result, Response):
            return result
        body, status = result if isinstance(result, tuple) else (result, 200)
        return JSONResponse(body, status)
    return wrapped


async def body_of(request):
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError('Body must be JSON') from None
    if not isinstance(body, dict):
        raise ValueError('Body must be a JSON object')
    return body


def cid_of(request):
    return request.path_params['cid']


# ---- pages ------------------------------------------------------------------

async def page(request):
    token = os.environ.get('CHAT_API_TOKEN')
    given = request.query_params.get('token')
    if token and given:
        if not secrets.compare_digest(given.encode(), token.encode()):
            return PlainTextResponse('Invalid token.', 401)
        response = RedirectResponse(request.url.remove_query_params('token'), 303)
        response.set_cookie(COOKIE, token, httponly=True, samesite='strict', max_age=60 * 60 * 24 * 30,
                            secure=request.url.scheme == 'https')
        return response
    if not authorized(request):
        return PlainTextResponse('This demo is private. Open the link with ?token=<token>.', 401)
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store'})


async def root(request):
    query = f'?{request.url.query}' if request.url.query else ''
    return RedirectResponse(f'/guest/messages{query}', 302)


# ---- API --------------------------------------------------------------------

ENDPOINTS = {
    'GET /api/conversations?role=guest|host': 'List conversations, newest first, with last_message and unread',
    'POST /api/conversations': 'Create a conversation (my_role, guest, host, listing, reservation, messages)',
    'GET /api/conversations/{id}': 'One conversation including all messages',
    'PATCH /api/conversations/{id}': 'Update guest/host/listing/reservation fields',
    'DELETE /api/conversations/{id}': 'Delete a conversation',
    'GET /api/conversations/{id}/messages?after=<message id>': 'Messages, optionally only newer than an id',
    'POST /api/conversations/{id}/messages': 'Send {sender: guest|host|system, text, created_at?|minutes_ago?}',
    'DELETE /api/messages/{id}': 'Delete a message',
    'POST /api/conversations/{id}/typing': 'Show/hide the typing indicator {sender, typing: true|false}',
    'POST /api/conversations/{id}/read': 'Mark read {by: guest|host}',
    'GET /api/events': 'Server-sent events: message.created, message.deleted, conversation.*, typing, reset',
    'POST /api/reset': 'Restore the demo scenario from seed.json',
}


@api
async def index(request):
    return {'endpoints': ENDPOINTS, 'webhook': bool(os.environ.get('CHAT_WEBHOOK_URL'))}


@api
async def conversations(request):
    if request.method == 'POST':
        created = db.create_conversation({**await body_of(request), 'source': 'api'})
        hub.notify({'type': 'conversation.created', 'conversation': created})
        return created, 201
    role = request.query_params.get('role')
    if role not in (None, *db.ROLES):
        raise ValueError('role must be guest or host')
    return {'conversations': [{**c, 'typing': hub.typing_of(c['id'])} for c in db.list_conversations(role)]}


@api
async def conversation(request):
    cid = cid_of(request)
    if request.method == 'PATCH':
        updated = db.update_conversation(cid, await body_of(request))
        hub.notify({'type': 'conversation.updated', 'conversation': updated})
        return updated
    if request.method == 'DELETE':
        db.delete_conversation(cid)
        hub.notify({'type': 'conversation.deleted', 'conversation_id': cid})
        return Response(status_code=204)
    return {**db.get_conversation(cid), 'typing': hub.typing_of(cid), 'messages': db.list_messages(cid)}


def query_int(request, name, default):
    try:
        return int(request.query_params.get(name, default))
    except ValueError:
        raise ValueError(f'{name} must be a whole number') from None


@api
async def messages(request):
    cid = cid_of(request)
    if request.method == 'GET':
        return {'messages': db.list_messages(cid, query_int(request, 'after', 0), query_int(request, 'limit', 500))}
    body = await body_of(request)
    source = 'ui' if body.pop('source', None) == 'ui' else 'api'
    message = db.add_message(cid, body, source)
    if hub.typing_of(cid) == message['sender']:
        hub.typing.pop(cid, None)
    convo = db.get_conversation(cid)
    hub.notify({'type': 'message.created', 'conversation_id': cid, 'message': message, 'conversation': convo})
    return message, 201


@api
async def message(request):
    mid = request.path_params['mid']
    cid = db.delete_message(mid)
    hub.notify({'type': 'message.deleted', 'conversation_id': cid, 'message_id': mid})
    return Response(status_code=204)


@api
async def typing(request):
    cid, body = cid_of(request), await body_of(request)
    if body.get('sender') not in db.ROLES:
        raise ValueError('sender must be guest or host')
    db.get_conversation(cid)
    active = body.get('typing', True) is not False
    if active:
        hub.typing[cid] = (body['sender'], time.monotonic() + TYPING_SECONDS)
    elif hub.typing_of(cid) == body['sender']:
        hub.typing.pop(cid, None)
    hub.publish({'type': 'typing', 'conversation_id': cid, 'sender': body['sender'], 'typing': active})
    return {'ok': True}


@api
async def read(request):
    cid, body = cid_of(request), await body_of(request)
    updated = db.mark_read(cid, body.get('by'), body.get('upto'))
    hub.notify({'type': 'conversation.read', 'conversation': updated, 'by': body.get('by')})
    return updated


@api
async def reset(request):
    db.reset()
    hub.notify({'type': 'reset'})
    return {'ok': True, 'conversations': len(db.list_conversations())}


@api
async def events(request):
    queue = asyncio.Queue(256)
    hub.queues.add(queue)

    async def stream():
        try:
            yield 'retry: 2000\nevent: hello\ndata: {}\n\n'  # page falls back to polling if this never arrives
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 15)
                except asyncio.TimeoutError:
                    yield ': ping\n\n'
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        finally:
            hub.queues.discard(queue)

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@asynccontextmanager
async def lifespan(app):
    db.init(os.environ.get('CHAT_DB'))
    yield


app = Starlette(
    routes=[
        Route('/', root),
        Route('/guest/messages', page),
        Route('/guest/messages/{cid:int}', page),
        Route('/hosting/messages', page),
        Route('/hosting/messages/{cid:int}', page),
        Route('/api', index),
        Route('/api/conversations', conversations, methods=['GET', 'POST']),
        Route('/api/conversations/{cid:int}', conversation, methods=['GET', 'PATCH', 'DELETE']),
        Route('/api/conversations/{cid:int}/messages', messages, methods=['GET', 'POST']),
        Route('/api/conversations/{cid:int}/typing', typing, methods=['POST']),
        Route('/api/conversations/{cid:int}/read', read, methods=['POST']),
        Route('/api/messages/{mid:int}', message, methods=['DELETE']),
        Route('/api/events', events),
        Route('/api/reset', reset, methods=['POST']),
        Mount('/static', StaticFiles(directory=STATIC), name='static'),
    ],
    middleware=[Middleware(CORSMiddleware, allow_origins=cors_origins(), allow_methods=['*'],
                           allow_headers=['*'], allow_credentials=True)],
    lifespan=lifespan,
)
