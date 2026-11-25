"""Server-side admin commands.

    songvault reset-pin               (in the container: docker exec songvault songvault reset-pin)
    songvault snapshot-db <file>      consistent copy of the database for backups (spec 2.5)
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from .auth import reset_pin
from .config import load_settings
from .db import connect, migrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="songvault", description="Song Vault admin commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "reset-pin",
        aliases=["reset-password"],
        help="Forget the PIN and sign out every device. The next visit shows the setup screen.",
    )
    snap = sub.add_parser(
        "snapshot-db",
        help="Write a consistent copy of the database (safe while the app is running).",
    )
    snap.add_argument("dest", type=Path)
    args = parser.parse_args(argv)

    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        if args.command in ("reset-pin", "reset-password"):
            reset_pin(conn)
            print("PIN cleared and all devices signed out. Open the app to choose a new PIN.")
        elif args.command == "snapshot-db":
            snapshot_db(conn, args.dest)
            print(f"Database snapshot written to {args.dest}")
    finally:
        conn.close()
    return 0


def snapshot_db(conn: sqlite3.Connection, dest: Path) -> None:
    """VACUUM INTO a temp file, then rename, so a reader never sees a half-written copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    conn.execute("VACUUM INTO ?", (str(tmp),))
    tmp.replace(dest)
