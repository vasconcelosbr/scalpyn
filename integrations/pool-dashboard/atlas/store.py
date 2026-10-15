"""Independent SQLite ledger; additive migration, immutable inputs and publications."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

def now():
    return datetime.now(timezone.utc).isoformat()

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()

def path():
    root = Path(os.environ['SNAPSHOT_DIR'])
    if os.environ.get('RAILWAY_SERVICE_ID') and (os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') != str(root) or not os.path.ismount(root)):
        raise RuntimeError('VOLUME_UNAVAILABLE')
    return root / 'atlas.sqlite3'

@contextmanager
def db(write=False):
    p = path()
    c = sqlite3.connect(p if write else p.resolve().as_uri()+'?mode=ro', uri=not write, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        if write:
            c.execute('BEGIN IMMEDIATE')
        yield c
        if write:
            c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()

def init():
    path().parent.mkdir(parents=True, exist_ok=True)
    with db(True) as c:
        for sql in (
          'CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)',
          'CREATE TABLE IF NOT EXISTS cycles(id TEXT PRIMARY KEY, day TEXT UNIQUE NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, inputs TEXT NOT NULL, input_hash TEXT NOT NULL, error_code TEXT)',
          'CREATE TABLE IF NOT EXISTS agents(run_id TEXT REFERENCES cycles(id),code TEXT, status TEXT NOT NULL, model TEXT, prompt_hash TEXT, session_id TEXT, started_at TEXT, finished_at TEXT, output TEXT, usage TEXT, error_code TEXT, reserved_cents INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(run_id,code))',
          'CREATE TABLE IF NOT EXISTS resources(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
          'CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,run_id TEXT UNIQUE REFERENCES cycles(id),generated_at TEXT NOT NULL,expires_at TEXT NOT NULL,body TEXT NOT NULL,sha256 TEXT NOT NULL)',
          'CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,url TEXT,hash TEXT,body TEXT NOT NULL,first_seen_at TEXT NOT NULL)',
          'CREATE TABLE IF NOT EXISTS feedback(consumer TEXT,idempotency_key TEXT,directive_id TEXT NOT NULL,payload TEXT NOT NULL,received_at TEXT NOT NULL,PRIMARY KEY(consumer,idempotency_key))',
          'CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,operation TEXT NOT NULL,entity TEXT NOT NULL,previous_hash TEXT,hash TEXT NOT NULL)',
          'CREATE TABLE IF NOT EXISTS operational(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
        ): c.execute(sql)
        c.execute('INSERT OR IGNORE INTO migrations VALUES (1,?)',(now(),))

def audit(c, operation, entity, data):
    row=c.execute('SELECT hash FROM audit ORDER BY id DESC LIMIT 1').fetchone()
    previous=row[0] if row else None
    stamp=now()
    c.execute('INSERT INTO audit(at,operation,entity,previous_hash,hash) VALUES (?,?,?,?,?)',
       (stamp,operation,entity,previous,digest([previous,stamp,operation,entity,data])))

def state(key, value):
    with db(True) as c:
        c.execute('INSERT INTO operational VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,encode(value)))

def get_state(key, default=None):
    with db() as c:
        row=c.execute('SELECT value FROM operational WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default

def resource(key, create):
    # Caller owns the daily cycle. Creation has no inference; uncertainty never starts a session.
    with db() as c: row=c.execute('SELECT value FROM resources WHERE key=?',(key,)).fetchone()
    if row: return json.loads(row[0])
    value=create()
    with db(True) as c:
        c.execute('INSERT INTO resources VALUES (?,?)',(key,encode(value)))
        audit(c,'resource_created',key,value)
    return value
