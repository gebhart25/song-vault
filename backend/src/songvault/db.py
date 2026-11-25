"""SQLite access and schema migrations.

Migrations are numbered SQL files in ``migrations/`` (``0001_initial.sql`` ...).
The applied version is kept in ``PRAGMA user_version``. Each file runs in one
transaction, so a failed migration leaves the database unchanged.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: Path) -> sqlite3.Connection:
    # FastAPI may run a request's dependency and endpoint on different worker
    # threads; a connection is still only used by one request at a time.
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations and return the resulting schema version."""
    conn.execute("PRAGMA journal_mode = WAL")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(path.name[:4])
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        current = version
    return current


def iter_rows(cursor: sqlite3.Cursor) -> Iterator[dict]:
    for row in cursor:
        yield dict(row)


def now_iso() -> str:
    """Current UTC time as an ISO 8601 string with millisecond precision."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
