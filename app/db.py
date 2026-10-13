"""SQLite storage. Every statement goes through `Database.execute` so tests can count queries."""
import sqlite3
import threading

DDL = """
CREATE TABLE agent (id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('AGENT','MANAGER')));
CREATE TABLE lead (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    state TEXT NOT NULL,
    product TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','CONTACTED','QUOTED','SOLD','LOST')),
    agent_id INTEGER REFERENCES agent(id),
    created_at TEXT NOT NULL
);
-- Every list query filters by owner and usually by status, then pages by id.
CREATE INDEX lead_owner_status ON lead (agent_id, status, id);
"""


class Result:
    def __init__(self, rows, rowcount):
        self.rows = rows
        self.rowcount = rowcount

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Database:
    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DDL)
        self.lock = threading.Lock()
        self.query_count = 0

    def execute(self, sql, args=()):
        """Run one statement and read its result while holding the lock.

        The connection is shared between request threads, so rows and rowcount are
        collected before the lock is released; a cursor read later could see another
        thread's work.
        """
        with self.lock:
            self.query_count += 1
            cursor = self.conn.execute(sql, args)
            return Result(cursor.fetchall(), cursor.rowcount)

    def seed(self, agents, leads):
        """Synthetic demo data. `leads` rows: (full_name, state, product, status, agent_id)."""
        for name, role in agents:
            self.conn.execute("INSERT INTO agent (name, role) VALUES (?,?)", (name, role))
        for i, (full_name, state, product, status, agent_id) in enumerate(leads):
            self.conn.execute(
                "INSERT INTO lead (full_name, state, product, status, agent_id, created_at) VALUES (?,?,?,?,?,?)",
                (full_name, state, product, status, agent_id, f"2026-01-{(i % 28) + 1:02d}T09:00:00"))
        self.query_count = 0
