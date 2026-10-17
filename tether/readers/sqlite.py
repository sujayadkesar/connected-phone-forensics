"""Forensically safe SQLite access.

Opening a database in place is unsafe: SQLite may checkpoint the ``-wal``
file into the main database or delete it, changing the evidence. Opening with
``immutable=1`` is safe but silently ignores the WAL, losing the newest
records. Tether instead copies the database together with its ``-wal`` and
``-shm`` files into a private work directory and opens the copy, so committed
WAL transactions are included and the original is never touched.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"


def is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


@dataclass
class Schema:
    tables: dict[str, list[str]]  # table name -> column names, in order

    def has(self, table: str, *columns: str) -> bool:
        cols = self.tables.get(table)
        if cols is None:
            return False
        low = {c.lower() for c in cols}
        return all(c.lower() in low for c in columns)

    def columns(self, table: str) -> list[str]:
        return self.tables.get(table, [])

    def fingerprint(self) -> str:
        """Stable hash of table and column names, used to spot unseen schema versions."""
        text = ";".join(f"{t}({','.join(c)})" for t, c in sorted(self.tables.items()))
        return hashlib.sha256(text.encode()).hexdigest()[:16]


class SafeDB:
    """A read-only connection to a private copy of an evidence database."""

    def __init__(self, conn: sqlite3.Connection, source: Path):
        self.conn = conn
        self.source = source
        self._schema: Schema | None = None

    @property
    def schema(self) -> Schema:
        if self._schema is None:
            tables: dict[str, list[str]] = {}
            rows = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
            ).fetchall()
            for (name,) in rows:
                try:
                    cols = [r[1] for r in self.conn.execute(f'PRAGMA table_info("{name}")')]
                except sqlite3.DatabaseError:
                    cols = []
                tables[name] = cols
            self._schema = Schema(tables)
        return self._schema

    def rows(self, sql: str, params: tuple = ()) -> Iterator[sqlite3.Row]:
        yield from self.conn.execute(sql, params)

    def select(self, table: str, columns: list[str] | None = None, where: str = "",
               order: str = "") -> Iterator[sqlite3.Row]:
        """SELECT the given columns that exist (missing ones come back as NULL), plus rowid."""
        have = {c.lower(): c for c in self.schema.columns(table)}
        wanted = columns or list(have.values())
        parts = []
        for c in wanted:
            real = have.get(c.lower())
            parts.append(f'"{real}" AS "{c}"' if real else f'NULL AS "{c}"')
        rowid = "rowid AS _rowid_" if not _is_without_rowid(self.conn, table) else "NULL AS _rowid_"
        sql = f'SELECT {rowid}, {", ".join(parts)} FROM "{table}"'
        if where:
            sql += f" WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        yield from self.conn.execute(sql)


def _is_without_rowid(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return bool(row and row[0] and "WITHOUT ROWID" in row[0].upper())


@contextmanager
def open_db(path: Path, workdir: Path | None = None) -> Iterator[SafeDB]:
    """Copy ``path`` (+ -wal, -shm, -journal) to a temp dir and open the copy read-only.

    The copy is opened read-write only long enough for SQLite to replay the
    WAL into it, then reopened read-only. The original is opened for reading
    only, through plain file copies.
    """
    path = Path(path)
    tmp = Path(tempfile.mkdtemp(prefix="tether-db-", dir=workdir))
    try:
        dst = tmp / path.name
        shutil.copyfile(path, dst)
        for suffix in ("-wal", "-shm", "-journal"):
            side = path.with_name(path.name + suffix)
            if side.is_file():
                shutil.copyfile(side, tmp / (path.name + suffix))
        # Replay the WAL into the copy so readers see committed transactions.
        if (tmp / (path.name + "-wal")).is_file():
            try:
                c = sqlite3.connect(dst)
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                c.close()
            except sqlite3.DatabaseError:
                pass
        conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
        try:
            yield SafeDB(conn, path)
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
