#!/usr/bin/env python3
"""Remove ephemeral push subscription state from a cashback backup copy."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


PUSH_TABLES = ("push_subscriptions", "push_deliveries", "push_state")


def sanitize(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"cashback database is missing: {path}")

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table in PUSH_TABLES:
            if table in tables:
                connection.execute(f'DELETE FROM "{table}"')
        connection.commit()
        connection.execute("VACUUM")

    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    arguments = parser.parse_args()
    sanitize(arguments.database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
