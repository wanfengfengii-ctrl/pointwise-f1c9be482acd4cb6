import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS revisions (
    revision INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL,
    page_count INTEGER NOT NULL CHECK (page_count > 0),
    region TEXT UNIQUE CHECK (region IN ('a', 'b'))
);
CREATE TABLE IF NOT EXISTS manifests (
    volume_id TEXT PRIMARY KEY,
    initial_revision INTEGER NOT NULL REFERENCES revisions(revision)
);
CREATE TABLE IF NOT EXISTS manifest_pages (
    volume_id TEXT NOT NULL REFERENCES manifests(volume_id),
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    source_page TEXT NOT NULL,
    identities TEXT NOT NULL,
    PRIMARY KEY (volume_id, page_index),
    UNIQUE (volume_id, source_page)
);
CREATE TABLE IF NOT EXISTS current_revisions (
    volume_id TEXT PRIMARY KEY REFERENCES manifests(volume_id),
    revision INTEGER NOT NULL UNIQUE REFERENCES revisions(revision)
);
CREATE TABLE IF NOT EXISTS reservations (
    region TEXT PRIMARY KEY CHECK (region IN ('a', 'b')),
    revision INTEGER NOT NULL UNIQUE REFERENCES revisions(revision)
);
CREATE TABLE IF NOT EXISTS candidate_pages (
    revision INTEGER NOT NULL REFERENCES reservations(revision) ON DELETE CASCADE,
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    fingerprint TEXT NOT NULL,
    PRIMARY KEY (revision, page_index)
);
CREATE TABLE IF NOT EXISTS calibration_jobs (
    revision INTEGER PRIMARY KEY REFERENCES revisions(revision),
    base_revision INTEGER NOT NULL REFERENCES revisions(revision),
    multiplier TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('writing', 'ready', 'failed', 'succeeded'))
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_id TEXT NOT NULL REFERENCES manifests(volume_id),
    revision INTEGER NOT NULL REFERENCES revisions(revision),
    next_index INTEGER NOT NULL DEFAULT 0 CHECK (next_index >= 0),
    closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1))
);
CREATE INDEX IF NOT EXISTS open_ticket_revisions ON tickets(revision) WHERE closed = 0;
CREATE TABLE IF NOT EXISTS delivery_receipts (
    ticket_id INTEGER NOT NULL REFERENCES tickets(ticket_id),
    revision INTEGER NOT NULL REFERENCES revisions(revision),
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    fingerprint TEXT NOT NULL,
    PRIMARY KEY (ticket_id, page_index)
);
CREATE TABLE IF NOT EXISTS replay_volumes (
    volume_id TEXT PRIMARY KEY REFERENCES manifests(volume_id)
);
CREATE TABLE IF NOT EXISTS replay_pages (
    volume_id TEXT NOT NULL REFERENCES replay_volumes(volume_id),
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    basis TEXT NOT NULL,
    PRIMARY KEY (volume_id, page_index)
);
CREATE TABLE IF NOT EXISTS replay_tickets (
    ticket_id INTEGER PRIMARY KEY REFERENCES tickets(ticket_id),
    multiplier TEXT NOT NULL
);
'''


class ArchiveBusyError(RuntimeError):
    pass


class Catalog:
    def __init__(self, root, *, before_write=None, after_write=None, before_read=None):
        self.root = Path(root).resolve()
        self.before_write = before_write
        self.after_write = after_write
        self.before_read = before_read

    def _connect(self):
        conn = sqlite3.connect(self.root / 'catalog.sqlite3', isolation_level=None)
        conn.execute('PRAGMA foreign_keys = ON')
        return conn

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript('BEGIN IMMEDIATE;\n' + _SCHEMA + '\nCOMMIT;')
        return self

    @contextmanager
    def _transaction(self, begin):
        with closing(self._connect()) as conn:
            conn.execute(begin)
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def read_transaction(self):
        return self._transaction('BEGIN')

    def write_transaction(self):
        return self._transaction('BEGIN IMMEDIATE')

    @contextmanager
    def writer_guard(self):
        # This separate SQLite lock spans payload writes without locking the directory.
        with closing(sqlite3.connect(self.root / 'writer.sqlite3', timeout=0, isolation_level=None)) as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise ArchiveBusyError('another volume write is in progress') from exc
                raise
            try:
                yield
            finally:
                conn.rollback()
