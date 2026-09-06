"""SQLite ownership, transactional schema initialization and bounded locking."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .artifacts import ArtifactStore, safe_path
from .errors import AudioError

DATABASE_NAME = "sessions.sqlite3"
DATABASE_VERSION = 1
APPLICATION_ID = 0x4D415353

# Execute individually: executescript() can implicitly commit pending work.
MIGRATIONS = {1: (
    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY, name TEXT NOT NULL, head_revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, origin_session TEXT, origin_revision INTEGER,
        CHECK ((origin_session IS NULL) = (origin_revision IS NULL)),
        FOREIGN KEY (session_id, head_revision) REFERENCES revisions(session_id, revision)
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (origin_session, origin_revision) REFERENCES revisions(session_id, revision)
    )""",
    """CREATE TABLE revisions (
        session_id TEXT NOT NULL, revision INTEGER NOT NULL, parent_revision INTEGER,
        asset_json TEXT NOT NULL, reason TEXT NOT NULL, restored_from_revision INTEGER,
        created_at TEXT NOT NULL, PRIMARY KEY (session_id, revision),
        CHECK ((revision = 1 AND parent_revision IS NULL) OR
               (revision > 1 AND parent_revision = revision - 1)),
        FOREIGN KEY (session_id) REFERENCES sessions(session_id),
        FOREIGN KEY (session_id, parent_revision) REFERENCES revisions(session_id, revision),
        FOREIGN KEY (session_id, restored_from_revision) REFERENCES revisions(session_id, revision)
    )""",
    """CREATE TABLE feedback (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, feedback_id TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL, revision INTEGER NOT NULL, source TEXT NOT NULL,
        text TEXT NOT NULL, listening_context TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY (session_id, revision) REFERENCES revisions(session_id, revision)
    )""",
    "CREATE INDEX feedback_session ON feedback(session_id, revision, sequence)",
    """CREATE TABLE mutations (
        request_id TEXT PRIMARY KEY, binding_json TEXT NOT NULL,
        response_json TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
)}


class SessionDatabase:
    def __init__(self, store: ArtifactStore):
        self.store = store
        self.path = store.root / DATABASE_NAME

    def _schema(self, connection: sqlite3.Connection, *, write: bool) -> None:
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version < 0:
            raise AudioError("session_database_unrecognized", "Invalid session database schema version")
        if version > DATABASE_VERSION:
            raise AudioError("session_schema_newer", "Session database needs a newer core",
                             details={"database_version": version, "supported_version": DATABASE_VERSION})
        if version == 0:
            existing = connection.execute("SELECT name FROM sqlite_master").fetchone()
            if application != 0 or existing is not None:
                raise AudioError("session_database_unrecognized", "Refusing to adopt an unrelated database")
        elif application != APPLICATION_ID:
            raise AudioError("session_database_unrecognized", "Wrong session database application ID")
        if version < DATABASE_VERSION:
            if not write:
                raise AudioError("session_migration_required", "A session mutation must initialize or migrate the database")
            for target in range(version + 1, DATABASE_VERSION + 1):
                for statement in MIGRATIONS[target]:
                    connection.execute(statement)
                if target == 1:
                    connection.execute("INSERT INTO metadata VALUES ('product', ?)", (self.store.product,))
                    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {target}")
        product = connection.execute("SELECT value FROM metadata WHERE key = 'product'").fetchone()
        if product is None or product[0] != self.store.product:
            raise AudioError("workspace_mismatch", "Session database belongs to a different product")

    @contextmanager
    def transaction(self, *, write: bool = False, create: bool = False):
        self.store._workspace(create=create)
        for suffix in ("", "-journal", "-wal", "-shm"):
            safe_path(self.path.with_name(self.path.name + suffix))
        if not self.path.exists() and not create:
            raise AudioError("session_database_missing", "No session database; create a session first")
        if self.path.exists() and not self.path.is_file():
            raise AudioError("session_database_unrecognized", "Session database must be a regular file")
        connection = None
        try:
            # Queries may need SQLite's rollback recovery after a process crash.
            # rw allows recovery without creating a missing file. Query paths
            # do not migrate or mutate application records.
            mode = "rwc" if create else "rw"
            options = {"autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL} if hasattr(
                sqlite3, "LEGACY_TRANSACTION_CONTROL") else {}
            connection = sqlite3.connect(self.path.as_uri() + "?mode=" + mode,
                                         uri=True, timeout=5, isolation_level=None, **options)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            if write:
                connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._schema(connection, write=write)
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise AudioError("session_busy", "Session database is busy; retry the same request ID") from exc
            raise AudioError("session_database_error", "Cannot read or commit session state",
                             details={"reason": str(exc)}) from exc
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                finally:
                    connection.close()
