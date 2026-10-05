"""SQLite storage: users, documents, chunks (+ full-text index), embeddings, audit log."""
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    active INTEGER NOT NULL DEFAULT 1,
    session_epoch INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    warning TEXT NOT NULL DEFAULT '',
    pages INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0,
    embedded INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    uploaded_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    heading TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    ocr INTEGER NOT NULL DEFAULT 0,
    page_map TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id, idx);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    search_text,
    chunk_id UNINDEXED,
    doc_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    vector BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS embeddings_doc ON embeddings(doc_id);

CREATE TABLE IF NOT EXISTS summaries (
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    lang TEXT NOT NULL,
    text TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, lang)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit_log(ts);
"""


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    """Thread-local SQLite connections to a single database file."""

    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()

    def connect(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def init_schema(self):
        conn = self.connect()
        conn.executescript(SCHEMA)
        # databases created by earlier versions
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(chunks)")}
        if "ocr" not in columns:
            conn.execute("ALTER TABLE chunks ADD COLUMN ocr INTEGER NOT NULL DEFAULT 0")
        if "page_map" not in columns:
            conn.execute("ALTER TABLE chunks ADD COLUMN page_map TEXT NOT NULL DEFAULT ''")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
        if "attempts" not in columns:
            conn.execute("ALTER TABLE documents ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        conn.commit()

    def execute(self, sql, params=()):
        conn = self.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur

    def query(self, sql, params=()):
        return self.connect().execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        return self.connect().execute(sql, params).fetchone()

    def audit(self, username, action, detail="", ip=""):
        self.execute(
            "INSERT INTO audit_log (ts, username, action, detail, ip) VALUES (?, ?, ?, ?, ?)",
            (now_iso(), username or "", action, (detail or "")[:2000], ip or ""),
        )
