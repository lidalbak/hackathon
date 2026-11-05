import asyncio
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from starlette.testclient import TestClient

from airbnb_chat import server
from airbnb_chat.server import app

NEW = {'my_role': 'host', 'guest': {'name': 'Noah'}, 'host': {'name': 'Alex'},
       'listing': {'title': 'Studio', 'location': 'Oslo, Norway'},
       'reservation': {'status': 'inquiry', 'check_in': 3, 'check_out': 5, 'guests': 2},
       'messages': [{'sender': 'guest', 'text': 'Later', 'minutes_ago': 1},
                    {'sender': 'guest', 'text': 'Earlier', 'minutes_ago': 10}]}


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {'CHAT_DB': os.path.join(self.tmp.name, 'chat.db')}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        for key in ('CHAT_API_TOKEN', 'CHAT_WEBHOOK_URL', 'CHAT_CORS_ORIGINS'):
            os.environ.pop(key, None)
        self.client = TestClient(app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def test_pages_and_seed(self):
        c = self.client
        self.assertEqual(c.get('/guest/messages/1').status_code, 200)
        self.assertEqual(c.get('/hosting/messages').status_code, 200)
        convos = c.get('/api/conversations').json()['conversations']
        self.assertEqual(len(convos), 6)
        self.assertEqual({x['guest']['name'] for x in convos}, {'Emma'})
        hosted = c.get('/api/conversations?role=host').json()['conversations']  # stay_agent's demo stay
        self.assertEqual([(x['id'], x['listing']['title'], x['reservation']['check_in']) for x in hosted],
                         [(1, 'Cozy loft near Bryggen', str(date.today()))])
        self.assertFalse(any(x['unread_by']['guest'] or x['unread_by']['host'] for x in convos))
        self.assertEqual(c.get('/', follow_redirects=False).headers['location'], '/guest/messages')

    def test_write_and_read_messages(self):
        c = self.client
        created = c.post('/api/conversations', json=NEW)
        self.assertEqual(created.status_code, 201)
        cid = created.json()['id']
        self.assertEqual(created.json()['reservation']['check_in'], str(date.today() + timedelta(days=3)))
        msgs = c.get(f'/api/conversations/{cid}/messages').json()['messages']
        self.assertEqual([m['text'] for m in msgs], ['Earlier', 'Later'])
        self.assertEqual(c.get('/api/conversations').json()['conversations'][0]['id'], cid)
        self.assertEqual(created.json()['unread'], 2)
        self.assertEqual(created.json()['unread_by'], {'guest': 0, 'host': 2})

        reply = c.post(f'/api/conversations/{cid}/messages', json={'sender': 'host', 'text': ' Hi Noah! '})
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.json()['text'], 'Hi Noah!')
        after = c.get(f'/api/conversations/{cid}/messages?after={msgs[-1]["id"]}').json()['messages']
        self.assertEqual([m['id'] for m in after], [reply.json()['id']])
        convo = c.get(f'/api/conversations/{cid}').json()
        self.assertEqual(convo['unread'], 0)  # replying marks the thread read
        self.assertEqual(convo['unread_by']['guest'], 1)  # each side has its own count
        self.assertEqual(len(convo['messages']), 3)

        read = c.post(f'/api/conversations/{cid}/read', json={'by': 'guest'}).json()
        self.assertEqual(read['read']['guest'], reply.json()['id'])

        patched = c.patch(f'/api/conversations/{cid}', json={'reservation': {'status': 'confirmed', 'code': 'HMABC'}})
        self.assertEqual(patched.json()['reservation']['status'], 'confirmed')
        self.assertEqual(patched.json()['reservation']['guests'], 2)

        self.assertEqual(c.delete(f'/api/messages/{reply.json()["id"]}').status_code, 204)
        self.assertEqual(c.delete(f'/api/conversations/{cid}').status_code, 204)
        self.assertEqual(c.get(f'/api/conversations/{cid}').status_code, 404)

    def test_validation(self):
        c = self.client
        bad = [({'sender': 'bob', 'text': 'x'}, 'sender'), ({'sender': 'host', 'text': '  '}, 'text'),
               ({'sender': 'host', 'text': 'x' * 5001}, 'text'), ({'sender': 'host', 'text': 'x', 'created_at': 'nope'}, 'created_at')]
        for body, field in bad:
            r = c.post('/api/conversations/1/messages', json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(field, r.json()['error'])
        self.assertEqual(c.post('/api/conversations/1/messages', content=b'not json').status_code, 400)
        self.assertEqual(c.post('/api/conversations/999/messages', json={'sender': 'host', 'text': 'x'}).status_code, 404)
        self.assertEqual(c.patch('/api/conversations/1', json={'reservation': {'status': 'maybe'}}).status_code, 400)
        self.assertEqual(c.patch('/api/conversations/1', json={'listing': {'price': 1}}).status_code, 400)
        self.assertEqual(c.post('/api/conversations', json={'guest': {'name': 'X'}}).status_code, 400)
        self.assertEqual(c.post('/api/conversations/1/typing', json={'sender': 'system'}).status_code, 400)

    def test_typing_visible_to_polling_clients(self):
        c = self.client
        c.post('/api/conversations/1/typing', json={'sender': 'host'})
        self.assertEqual(c.get('/api/conversations/1').json()['typing'], 'host')
        self.assertEqual(c.get('/api/conversations').json()['conversations'][0]['typing'], 'host')
        c.post('/api/conversations/1/messages', json={'sender': 'host', 'text': 'Done typing'})
        self.assertIsNone(c.get('/api/conversations/1').json()['typing'])
        self.assertEqual(c.get('/api/conversations/1/messages?after=x').status_code, 400)

    def test_cross_origin_writes_blocked_unless_allowed(self):
        c, body = self.client, {'sender': 'host', 'text': 'x'}
        self.assertEqual(c.post('/api/conversations/1/messages', json=body, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(c.post('/api/conversations/1/messages', json=body, headers={'Origin': 'http://testserver'}).status_code, 201)
        with mock.patch.dict(os.environ, {'CHAT_CORS_ORIGINS': 'https://evil.example'}):
            self.assertEqual(c.post('/api/conversations/1/messages', json=body, headers={'Origin': 'https://evil.example'}).status_code, 201)

    def test_token(self):
        c = self.client
        with mock.patch.dict(os.environ, {'CHAT_API_TOKEN': 's3cret'}):
            self.assertEqual(c.get('/api/conversations').status_code, 401)
            self.assertEqual(c.get('/guest/messages').status_code, 401)
            self.assertEqual(c.get('/api/conversations', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
            self.assertEqual(c.get('/api/conversations', headers={'Authorization': 'Bearer s3cret'}).status_code, 200)
            login = c.get('/guest/messages?token=s3cret', follow_redirects=False)
            self.assertEqual(login.status_code, 303)
            self.assertEqual(login.headers['location'], 'http://testserver/guest/messages')
            self.assertEqual(c.get('/api/conversations').status_code, 200)  # cookie from the login link
            self.assertEqual(c.get('/guest/messages').status_code, 200)

    def test_reset(self):
        c = self.client
        c.delete('/api/conversations/1')
        self.assertEqual(c.post('/api/reset').json()['conversations'], 6)


class HubTests(unittest.TestCase):
    def test_publish_and_webhook(self):
        async def run():
            hub, queue = server.Hub(), asyncio.Queue(1)
            hub.queues.add(queue)
            sent = []

            async def fake_post(url, event):
                sent.append((url, event['type']))
            with mock.patch.object(server, 'post_webhook', fake_post), \
                    mock.patch.dict(os.environ, {'CHAT_WEBHOOK_URL': 'http://hook'}):
                hub.notify({'type': 'message.created'})
                hub.notify({'type': 'typing'})  # queue full: dropped, not raised; no webhook for typing
                await asyncio.gather(*hub.tasks)
            self.assertEqual((await queue.get())['type'], 'message.created')
            self.assertEqual(sent, [('http://hook', 'message.created')])
        asyncio.run(run())
