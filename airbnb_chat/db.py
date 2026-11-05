"""SQLite store for the Airbnb-style inbox.

A conversation is between a guest and a host. `my_role` is the account owner's side in it
(the one that ?role= filters by); the pages show every conversation from their own side,
/guest/messages as the guest and /hosting/messages as the host. Messages are sent by
'guest', 'host' or 'system'.
"""
import json
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / 'data' / 'chat.db'
SEED = Path(__file__).parent / 'seed.json'

ROLES = ('guest', 'host')
SENDERS = ROLES + ('system',)
STATUSES = ('inquiry', 'pending', 'confirmed', 'canceled', 'completed')
MAX_TEXT = 5000
# Allowed fields per section: value type ('str', 'int', 'bool', 'date', or a tuple of choices).
SCHEMA = {
    'guest': {'name': 'str', 'avatar': 'str'},
    'host': {'name': 'str', 'avatar': 'str', 'superhost': 'bool'},
    'listing': {'title': 'str', 'location': 'str', 'type': 'str', 'photo': 'str'},
    'reservation': {'status': STATUSES, 'check_in': 'date', 'check_out': 'date', 'guests': 'int',
                    'code': 'str', 'total': 'str'},
}


class NotFound(LookupError):
    pass


_lock = threading.RLock()
_conn = None


def init(path=None):
    global _conn
    path = Path(path or DEFAULT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _conn:
            _conn.close()
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript('''PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS conversations(
          id INTEGER PRIMARY KEY, my_role TEXT NOT NULL, data TEXT NOT NULL,
          guest_read_id INTEGER NOT NULL DEFAULT 0, host_read_id INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(
          id INTEGER PRIMARY KEY,
          conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          sender TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, source TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, id);''')
        if not _conn.execute('SELECT count(*) FROM conversations').fetchone()[0]:
            seed()


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def other(role):
    return 'host' if role == 'guest' else 'guest'


# ---- validation -------------------------------------------------------------

def _value(kind, value, field):
    if value is None:
        return None
    if isinstance(kind, tuple):
        if value not in kind:
            raise ValueError(f'{field} must be one of {", ".join(kind)}')
        return value
    if kind == 'str':
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError(f'{field} must be a string of at most 500 characters')
        return value.strip()
    if kind == 'bool':
        if not isinstance(value, bool):
            raise ValueError(f'{field} must be true or false')
        return value
    if kind == 'int':
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 100:
            raise ValueError(f'{field} must be a whole number between 1 and 99')
        return value
    # date: 'YYYY-MM-DD', or a whole number of days from today (handy for seeds and demos)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(date.today() + timedelta(days=value))
    try:
        return str(date.fromisoformat(value))
    except (TypeError, ValueError):
        raise ValueError(f'{field} must be YYYY-MM-DD or a number of days from today') from None


def _merge(data, patch):
    """Validate a nested patch like {'host': {'name': 'Ingrid'}} and merge it into data."""
    if not isinstance(patch, dict):
        raise ValueError('Expected a JSON object')
    for section, fields in patch.items():
        if section not in SCHEMA:
            continue
        if not isinstance(fields, dict):
            raise ValueError(f'{section} must be an object')
        for key, value in fields.items():
            if key not in SCHEMA[section]:
                raise ValueError(f'Unknown field {section}.{key}')
            data.setdefault(section, {})[key] = _value(SCHEMA[section][key], value, f'{section}.{key}')
    return data


def _timestamp(message):
    if message.get('created_at'):
        try:
            stamp = datetime.fromisoformat(message['created_at'].replace('Z', '+00:00'))
        except (AttributeError, ValueError):
            raise ValueError('created_at must be an ISO 8601 timestamp') from None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat(timespec='seconds')
    if message.get('minutes_ago') is not None:
        minutes = message['minutes_ago']
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes < 0:
            raise ValueError('minutes_ago must be a positive number')
        return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec='seconds')
    return now()


# ---- serialization ----------------------------------------------------------

def _message(row):
    return {'id': row['id'], 'conversation_id': row['conversation_id'], 'sender': row['sender'],
            'text': row['text'], 'created_at': row['created_at'], 'source': row['source']}


def _conversation(row):
    data = json.loads(row['data'])
    me = row['my_role']
    last = _conn.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1',
                         (row['id'],)).fetchone()
    unread_by = {role: _conn.execute('SELECT count(*) FROM messages WHERE conversation_id=? AND sender=? AND id>?',
                                     (row['id'], other(role), row[f'{role}_read_id'])).fetchone()[0]
                 for role in ROLES}
    return {'id': row['id'], 'my_role': me, **{k: data.get(k, {}) for k in SCHEMA},
            'read': {'guest': row['guest_read_id'], 'host': row['host_read_id']},
            'unread': unread_by[me], 'unread_by': unread_by, 'last_message': _message(last) if last else None,
            'created_at': row['created_at'], 'updated_at': row['updated_at']}


def _row(cid):
    row = _conn.execute('SELECT * FROM conversations WHERE id=?', (cid,)).fetchone()
    if not row:
        raise NotFound(f'Conversation {cid} not found')
    return row


# ---- queries ----------------------------------------------------------------

def list_conversations(role=None):
    with _lock:
        rows = _conn.execute('''SELECT c.* FROM conversations c ORDER BY coalesce(
          (SELECT max(created_at) FROM messages m WHERE m.conversation_id=c.id), c.created_at) DESC, c.id DESC''').fetchall()
        return [_conversation(r) for r in rows if role in (None, r['my_role'])]


def get_conversation(cid):
    with _lock:
        return _conversation(_row(cid))


def create_conversation(body):
    if not isinstance(body, dict):
        raise ValueError('Expected a JSON object')
    role = body.get('my_role', 'host')
    if role not in ROLES:
        raise ValueError('my_role must be guest or host')
    data = _merge({}, body)
    for part in ROLES:
        if not data.get(part, {}).get('name'):
            raise ValueError(f'{part}.name is required')
    data.setdefault('reservation', {}).setdefault('status', 'inquiry')
    messages = body.get('messages') or []
    if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
        raise ValueError('messages must be a list of objects')
    with _lock, _conn:
        stamp = now()
        cid = _conn.execute('INSERT INTO conversations(my_role,data,created_at,updated_at) VALUES (?,?,?,?)',
                            (role, json.dumps(data), stamp, stamp)).lastrowid
        for message in sorted(messages, key=_timestamp):
            _insert_message(cid, message, body.get('source', 'api'))
        return _conversation(_row(cid))


def update_conversation(cid, body):
    with _lock, _conn:
        row = _row(cid)
        data = _merge(json.loads(row['data']), body)
        role = body.get('my_role', row['my_role'])
        if role not in ROLES:
            raise ValueError('my_role must be guest or host')
        _conn.execute('UPDATE conversations SET data=?, my_role=?, updated_at=? WHERE id=?',
                      (json.dumps(data), role, now(), cid))
        return _conversation(_row(cid))


def delete_conversation(cid):
    with _lock, _conn:
        _row(cid)
        _conn.execute('DELETE FROM conversations WHERE id=?', (cid,))


def list_messages(cid, after=0, limit=500):
    with _lock:
        _row(cid)
        rows = _conn.execute('SELECT * FROM messages WHERE conversation_id=? AND id>? ORDER BY id LIMIT ?',
                             (cid, after, max(1, min(limit, 1000)))).fetchall()
        return [_message(r) for r in rows]


def _insert_message(cid, message, source):
    if not isinstance(message, dict):
        raise ValueError('Each message must be an object')
    sender, text = message.get('sender'), message.get('text')
    if sender not in SENDERS:
        raise ValueError('sender must be guest, host or system')
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError(f'text must be 1–{MAX_TEXT} characters')
    stamp = _timestamp(message)
    mid = _conn.execute('INSERT INTO messages(conversation_id,sender,text,created_at,source) VALUES (?,?,?,?,?)',
                        (cid, sender, text.strip(), stamp, source)).lastrowid
    if sender in ROLES:  # whoever writes has read everything before it
        _conn.execute(f'UPDATE conversations SET {sender}_read_id=? WHERE id=?', (mid, cid))
    _conn.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now(), cid))
    return mid


def add_message(cid, message, source='api'):
    with _lock, _conn:
        _row(cid)
        mid = _insert_message(cid, message, source)
        return _message(_conn.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone())


def delete_message(mid):
    with _lock, _conn:
        row = _conn.execute('SELECT conversation_id FROM messages WHERE id=?', (mid,)).fetchone()
        if not row:
            raise NotFound(f'Message {mid} not found')
        _conn.execute('DELETE FROM messages WHERE id=?', (mid,))
        return row['conversation_id']


def mark_read(cid, role, upto=None):
    if role not in ROLES:
        raise ValueError('by must be guest or host')
    with _lock, _conn:
        _row(cid)
        last = _conn.execute('SELECT max(id) FROM messages WHERE conversation_id=?', (cid,)).fetchone()[0] or 0
        upto = last if upto is None else min(int(upto), last)
        _conn.execute(f'UPDATE conversations SET {role}_read_id=max({role}_read_id, ?) WHERE id=?', (upto, cid))
        return _conversation(_row(cid))


def seed(path=SEED):
    """Load the demo scenario. Dates and times in the seed are relative to now."""
    scenario = json.loads(Path(path).read_text())
    for conversation in scenario['conversations']:
        created = create_conversation({**conversation, 'source': 'seed'})
        mark_read(created['id'], other(created['my_role']))
        if not conversation.get('unread'):
            mark_read(created['id'], created['my_role'])


def reset(path=SEED):
    with _lock, _conn:
        _conn.execute('DELETE FROM messages')
        _conn.execute('DELETE FROM conversations')
    seed(path)
