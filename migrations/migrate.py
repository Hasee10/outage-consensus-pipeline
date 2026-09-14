"""Migration runner.

1. Connects to the maintenance DB (DB_MAINTENANCE, default 'postgres') and
   CREATEs the target database (DB_NAME) if it does not already exist.
2. Connects to the target DB and applies 001_init.sql (idempotent).

Run:  py -m migrations.migrate
"""
from __future__ import annotations

import pathlib
import sys

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from app import config, db

SCHEMA_FILE = pathlib.Path(__file__).with_name("001_init.sql")


def ensure_database() -> None:
    target = config.db_config()["dbname"]
    conn = db.connect(maintenance=True)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)  # CREATE DATABASE needs autocommit
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,))
            if cur.fetchone():
                print(f"database '{target}' already exists")
            else:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
                print(f"created database '{target}'")
    finally:
        conn.close()


def apply_schema() -> None:
    ddl = SCHEMA_FILE.read_text(encoding="utf-8")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
        print(f"applied schema from {SCHEMA_FILE.name}")
    finally:
        conn.close()


def main() -> int:
    try:
        ensure_database()
        apply_schema()
    except psycopg2.OperationalError as exc:
        print(f"\nERROR: could not connect to Postgres.\n{exc}", file=sys.stderr)
        print("Check DB_HOST/DB_PORT/DB_USER/DB_PASSWORD in your .env file.", file=sys.stderr)
        return 1
    print("migration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
