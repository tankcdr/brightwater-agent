"""Postgres access. pg8000 is pure Python and BSD-3 licensed."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pg8000.native

from .fixture import COLUMNS

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema" / "postgres"


def connect(banker_id: str | None = None) -> pg8000.native.Connection:
    conn = pg8000.native.Connection(
        user=os.getenv("BRIGHTWATER_PG_USER", "brightwater"),
        password=os.getenv("BRIGHTWATER_PG_PASSWORD", "brightwater"),
        host=os.getenv("BRIGHTWATER_PG_HOST", "localhost"),
        port=int(os.getenv("BRIGHTWATER_PG_PORT", "5432")),
        database=os.getenv("BRIGHTWATER_PG_DB", "brightwater"),
        application_name="brightwater",
    )
    if banker_id:
        # Read by the RLS policies in schema/postgres/002_rls.sql (enforced from Ch 8).
        conn.run("SELECT set_config('brightwater.banker_id', :b, false)", b=banker_id)
    return conn


@contextmanager
def session(banker_id: str | None = None) -> Iterator[pg8000.native.Connection]:
    conn = connect(banker_id)
    try:
        yield conn
    finally:
        conn.close()


def rows(conn: pg8000.native.Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    result = conn.run(sql, **params)
    cols = [c["name"] for c in conn.columns or []]
    return [dict(zip(cols, r)) for r in result or []]


def init_schema(conn: pg8000.native.Connection) -> list[str]:
    applied = []
    for path in sorted(SCHEMA_DIR.glob("0*.sql")):
        conn.run(path.read_text())
        applied.append(path.name)
    return applied


def load_csv(conn: pg8000.native.Connection, csv_dir: Path) -> dict[str, int]:
    counts = {}
    conn.run("BEGIN")
    try:
        for table in reversed(list(COLUMNS)):
            conn.run(f"TRUNCATE {table} CASCADE")
        for table, cols in COLUMNS.items():
            with (csv_dir / f"{table}.csv").open("rb") as f:
                conn.run(f"COPY {table} ({', '.join(cols)}) FROM STDIN WITH (FORMAT csv, HEADER true)", stream=f)
            counts[table] = conn.run(f"SELECT count(*) FROM {table}")[0][0]
        conn.run("COMMIT")
    except Exception:
        conn.run("ROLLBACK")
        raise
    return counts
