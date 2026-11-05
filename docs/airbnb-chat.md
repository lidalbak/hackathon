# Airbnb-style chat (demo)

A lookalike of the Airbnb web inbox with a JSON API that reads and writes it. Code in [`airbnb_chat/`](../airbnb_chat).
Use it to demo an agent that talks to guests or hosts: the agent writes through the API, and the open page
updates live, as if it were Airbnb.

Not affiliated with Airbnb, and it does not connect to Airbnb. Colors, radii and the font stack come from airbnb.com's
public CSS. The proprietary Cereal font is not included, and the logo is an approximation.

## Run

Needs Python 3.10+.

```bash
pip install -r airbnb_chat/requirements.txt
python -m airbnb_chat                     # http://127.0.0.1:8766  (--host, --port to change)
```

| Page | What it shows |
| --- | --- |
| `/guest/messages[/{id}]` | Traveler inbox |
| `/hosting/messages[/{id}]` | Host inbox (the default): host top bar, Accept/Decline on booking requests |

The demo starts with six conversations from [`airbnb_chat/seed.json`](../airbnb_chat/seed.json). You are "Alex", the
host in all of them: guests asking for the wifi password and parking, and your follow-ups about noise and checkout.
`/` opens the host inbox. Seed dates are days from today, and message times are minutes ago. Data is stored in
`airbnb_chat/data/chat.db` (git-ignored). `POST /api/reset` reloads the seed.

| Variable | Effect |
| --- | --- |
| `CHAT_API_TOKEN` | Every page and API call needs it. API: `Authorization: Bearer <token>`. Browser: open any page with `?token=<token>` once (sets a cookie) |
| `CHAT_WEBHOOK_URL` | Every `message.created` event is POSTed there as JSON |
| `CHAT_CORS_ORIGINS` | Comma-separated browser origins allowed to call the API, e.g. `http://localhost:3000` |
| `CHAT_DB` | SQLite path |

### Public URL

```bash
export CHAT_API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')
python -m airbnb_chat
cloudflared tunnel --url http://127.0.0.1:8766        # prints https://<random>.trycloudflare.com
```

Share `https://<random>.trycloudflare.com/hosting/messages?token=$CHAT_API_TOKEN`. Always set a token first: without
one, anyone with the URL can read and write. Quick tunnels hold back server-sent events, so the page falls back to
polling every 2 s on its own. Bots should use `http://127.0.0.1:8766` on the same machine, the webhook, or polling.

## Data model

Every conversation belongs to the account owner. `my_role` is the owner's side in it, `guest` or `host`. A message's
`sender` is `guest`, `host` or `system`. A system message shows as a centered event, such as "Reservation confirmed".

```json
{
  "id": 1,
  "my_role": "host",
  "guest": {"name": "Emma", "avatar": "https://…"},
  "host": {"name": "Alex", "superhost": true},
  "listing": {"title": "Bright studio in Grünerløkka", "location": "Oslo, Norway",
              "type": "Entire rental unit", "photo": "https://…"},
  "reservation": {"status": "confirmed", "check_in": "2026-11-05", "check_out": "2026-11-08",
                  "guests": 2, "code": "HM8VK2QX7D", "total": "kr 4,860"},
  "read": {"guest": 3, "host": 3},
  "unread": 0,
  "typing": null,
  "last_message": {"id": 3, "conversation_id": 1, "sender": "host", "text": "Welcome, Emma! The network is …",
                   "created_at": "2026-11-05T17:49:57+00:00", "source": "seed"},
  "created_at": "2026-11-05T17:04:57+00:00",
  "updated_at": "2026-11-05T17:04:57+00:00"
}
```

- `reservation.status`: `inquiry`, `pending` (request to book), `confirmed`, `canceled` or `completed`.
- Dates are `YYYY-MM-DD`. On input, a whole number means days from today.
- `read.guest` and `read.host` are the id of the last message each side has read. Sending a message marks everything
  before it as read for the sender. The page shows "Read" under the owner's last message once the other side's read id
  reaches it.
- `unread` counts messages from the other side that the owner has not read.
- `typing` is the side currently typing (lasts 8 s), or `null`.
- `source` on a message is `ui` (typed in the page), `api` or `seed`.
- `avatar` and `photo` are image URLs. When an avatar is missing, the page shows the first letter of the name.

## API

All bodies are JSON. Errors are `{"error": "…"}` with 400 (validation), 401 (token), 403 (cross-origin write) or 404.

| Method & path | Body / query | Returns |
| --- | --- | --- |
| `GET /api` | | Endpoint list |
| `GET /api/conversations` | `?role=guest\|host` | `{"conversations": [...]}` newest first |
| `POST /api/conversations` | `my_role`, `guest`, `host` (both need `name`), `listing`, `reservation`, `messages` | 201, the conversation |
| `GET /api/conversations/{id}` | | Conversation plus `messages` |
| `PATCH /api/conversations/{id}` | Any subset, merged: `{"reservation": {"status": "confirmed"}}` | The conversation |
| `DELETE /api/conversations/{id}` | | 204 |
| `GET /api/conversations/{id}/messages` | `?after=<message id>&limit=500` | `{"messages": [...]}` oldest first |
| `POST /api/conversations/{id}/messages` | `{"sender": "host", "text": "…"}`, optional `created_at` (ISO 8601) or `minutes_ago` | 201, the message |
| `DELETE /api/messages/{id}` | | 204 |
| `POST /api/conversations/{id}/typing` | `{"sender": "host", "typing": true}` | `{"ok": true}` |
| `POST /api/conversations/{id}/read` | `{"by": "guest"}`, optional `upto` message id | The conversation |
| `GET /api/events` | | Server-sent events, see below |
| `POST /api/reset` | | Reloads `seed.json` |

Text is 1–5000 characters and is trimmed. Unknown fields are rejected, so typos fail loudly.

### Events

`GET /api/events` is a `text/event-stream`. The first event is `hello`. After that, each event's `data` is JSON with a `type`:

| Type | Payload |
| --- | --- |
| `message.created` | `conversation_id`, `message`, `conversation` (after the message) |
| `message.deleted` | `conversation_id`, `message_id` |
| `conversation.created` / `.updated` / `.read` | `conversation` (`.read` also has `by`) |
| `conversation.deleted` | `conversation_id` |
| `typing` | `conversation_id`, `sender`, `typing` |
| `reset` | nothing else |

The webhook (`CHAT_WEBHOOK_URL`) receives the same `message.created` payload as a POST.

## Recipes

```bash
B=http://127.0.0.1:8766/api      # add -H "Authorization: Bearer $CHAT_API_TOKEN" if a token is set

# A new guest writes in. It shows up live in /hosting/messages.
curl -X POST $B/conversations -H 'Content-Type: application/json' -d '{
  "my_role": "host",
  "guest": {"name": "Noah"}, "host": {"name": "Alex"},
  "listing": {"title": "Bright studio in Grünerløkka", "location": "Oslo, Norway"},
  "reservation": {"status": "inquiry", "check_in": 30, "check_out": 33, "guests": 2},
  "messages": [{"sender": "guest", "text": "Is the studio free over New Year?"}]
}'

# Show "typing…", then reply as the host
curl -X POST $B/conversations/7/typing   -H 'Content-Type: application/json' -d '{"sender": "host"}'
curl -X POST $B/conversations/7/messages -H 'Content-Type: application/json' -d '{"sender": "host", "text": "Yes, it is!"}'

# Turn a request into a confirmed booking
curl -X PATCH $B/conversations/7 -H 'Content-Type: application/json' -d '{"reservation": {"status": "confirmed"}}'
curl -X POST  $B/conversations/7/messages -H 'Content-Type: application/json' -d '{"sender": "system", "text": "Reservation confirmed"}'

# Poll for anything new after message 20
curl "$B/conversations/7/messages?after=20"
```

An auto-reply bot for the host side:

```python
import json, httpx

B = 'http://127.0.0.1:8766/api'
with httpx.stream('GET', f'{B}/events', timeout=None) as events:
    for line in events.iter_lines():
        if not line.startswith('data: '):
            continue
        event = json.loads(line[6:])
        if event['type'] == 'message.created' and event['message']['sender'] == 'guest' \
                and event['conversation']['my_role'] == 'host':
            cid = event['conversation_id']
            httpx.post(f'{B}/conversations/{cid}/typing', json={'sender': 'host'})
            reply = 'Thanks! I will get back to you shortly.'   # call your model here
            httpx.post(f'{B}/conversations/{cid}/messages', json={'sender': 'host', 'text': reply})
```

Reply only to the other side's messages, never to your own, or the bot will answer itself in a loop.

## Tests

```bash
python -m unittest discover -s airbnb_chat/tests -t .
```
