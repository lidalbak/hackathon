from contextlib import closing
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'data' / 'demo.db'

def seed():
    DB.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(DB)) as conn, conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS customers
        (id INTEGER PRIMARY KEY, name TEXT, created_at TEXT, internal INTEGER);
        CREATE TABLE IF NOT EXISTS usage_events
        (id INTEGER PRIMARY KEY, customer_id INTEGER, product TEXT, event_name TEXT, occurred_at TEXT);''')
        if conn.execute('SELECT count(*) FROM customers').fetchone()[0]:
            return
        rng = random.Random(42)
        today = date.today()
        for i in range(1, 181):
            created = today - timedelta(days=rng.randint(0, 89))
            conn.execute('INSERT INTO customers VALUES (?,?,?,?)', (i, f'Demo customer {i}', str(created), int(i % 23 == 0)))
            for product, chance in [('X', .52), ('Y', .75), ('Z', .31)]:
                if rng.random() < chance:
                    for _ in range(rng.randint(1, 5)):
                        occurred = created + timedelta(days=rng.randint(0, (today-created).days))
                        conn.execute('INSERT INTO usage_events(customer_id,product,event_name,occurred_at) VALUES (?,?,?,?)', (i, product, 'product_used', str(occurred)))

def query(sql, params=()):
    """Read-only connection plus authorizer, deadline, and output cap."""
    import time
    deadline = time.monotonic() + 2
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchmany(501)][:500]
    finally:
        conn.close()

SCHEMA = '''customers(id, name, created_at YYYY-MM-DD, internal 0/1)
usage_events(id, customer_id, product X/Y/Z, event_name, occurred_at YYYY-MM-DD)'''

def metric(product, days):
    cutoff = str(date.today() - timedelta(days=days))
    sql = '''SELECT COUNT(DISTINCT c.id) AS total,
      COUNT(DISTINCT CASE WHEN e.id IS NOT NULL THEN c.id END) AS active
      FROM customers c LEFT JOIN usage_events e ON e.customer_id=c.id
      AND e.product=? AND e.event_name='product_used' AND e.occurred_at>=?
      WHERE c.created_at>=? AND c.internal=0'''
    row = query(sql, (product, cutoff, cutoff))[0]
    return {'product':product, 'days':days, 'cutoff':cutoff, **row,
            'inactive':row['total']-row['active'], 'sql':sql,
            'parameters':[product,cutoff,cutoff]}
