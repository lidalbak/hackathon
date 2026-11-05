# Airbnb-style chat (demo)

A lookalike of the Airbnb web inbox with a JSON API that reads and writes it. Not affiliated with Airbnb.
Full documentation (data model, every endpoint and event, bot recipe): [docs/airbnb-chat.md](../docs/airbnb-chat.md).

```bash
pip install -r airbnb_chat/requirements.txt
python -m airbnb_chat              # http://127.0.0.1:8766/guest/messages and /hosting/messages
```

```bash
# reply as the host in conversation 1; the open page updates live
curl -X POST http://127.0.0.1:8766/api/conversations/1/messages \
  -H 'Content-Type: application/json' -d '{"sender": "host", "text": "Hi Emma!"}'
```

| File | What it is |
| --- | --- |
| `server.py` | Starlette app: pages, API, event stream, webhook, token |
| `db.py` | SQLite store and validation |
| `seed.json` | Demo scenario (you are "Alex"), reloaded by `POST /api/reset` |
| `static/` | The inbox page: plain HTML, CSS and JavaScript |
| `tests/` | `python -m unittest discover -s airbnb_chat/tests -t .` |
