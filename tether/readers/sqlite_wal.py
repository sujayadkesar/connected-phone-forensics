"""Recover earlier versions of rows from a SQLite write-ahead log.

A WAL file holds whole page images, one frame per page write, appended until
a checkpoint copies them into the database. A row that was updated or
deleted after the last checkpoint therefore still exists in older frames
(and in the main database file). Opening the database normally shows only
the newest state; this module reads every page version of a table and
returns each distinct row version with where it came from.

Pure Python, read-only, independent of the sqlite3 library. Implements the
file format documented at https://www.sqlite.org/fileformat2.html (database
header, b-tree pages, record format, overflow pages) and
https://www.sqlite.org/walformat.html (WAL header, frames, checksums).
"""

from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WAL_MAGIC_LE = 0x377F0682
WAL_MAGIC_BE = 0x377F0683


@dataclass
class Frame:
    index: int            # 0-based frame number in the WAL
    page: int
    commit_size: int      # non-zero for the last frame of a transaction
    data: bytes
    valid: bool           # salts and cumulative checksum match
    committed: bool = False  # part of a committed transaction
    txn_end: int = -1     # index of the last frame of this frame's transaction


@dataclass
class RowVersion:
    rowid: int
    values: list[Any]
    source: str           # "db" (main file) or "wal"
    frame: int | None     # WAL frame index, None for the main file
    committed: bool = True


@dataclass
class _Pages:
    db: bytes | mmap.mmap
    page_size: int
    usable: int
    frames: list[Frame] = field(default_factory=list)

    def from_db(self, pgno: int) -> bytes | None:
        off = (pgno - 1) * self.page_size
        if off < 0 or off + self.page_size > len(self.db):
            return None
        return self.db[off:off + self.page_size]

    def at(self, pgno: int, upto_frame: int | None) -> bytes | None:
        """Newest image of a page at or before a frame (None = main file only)."""
        if upto_frame is not None:
            for fr in reversed(self.frames[:upto_frame + 1]):
                if fr.page == pgno and fr.valid:
                    return fr.data
        return self.from_db(pgno)

    def current(self, pgno: int) -> bytes | None:
        """Page as SQLite would see it: last committed frame, else the main file."""
        for fr in reversed(self.frames):
            if fr.page == pgno and fr.valid and fr.committed:
                return fr.data
        return self.from_db(pgno)


def _wal_checksum(data: bytes, s0: int, s1: int, big_endian: bool) -> tuple[int, int]:
    fmt = ">" if big_endian else "<"
    n = len(data) // 4
    words = struct.unpack(f"{fmt}{n}I", data[:n * 4])
    for i in range(0, n - 1, 2):
        s0 = (s0 + words[i] + s1) & 0xFFFFFFFF
        s1 = (s1 + words[i + 1] + s0) & 0xFFFFFFFF
    return s0, s1


def read_wal(wal: bytes) -> tuple[int, list[Frame]]:
    """Parse a WAL file; returns (page size, frames). Invalid frames are kept but marked."""
    if len(wal) < 32:
        return 0, []
    magic, _version, page_size, _ckpt, salt1, salt2, c1, c2 = struct.unpack(">8I", wal[:32])
    if magic not in (WAL_MAGIC_LE, WAL_MAGIC_BE) or page_size < 512:
        return 0, []
    big = magic == WAL_MAGIC_BE
    s0, s1 = _wal_checksum(wal[:24], 0, 0, big)
    header_ok = (s0, s1) == (c1, c2)
    frames: list[Frame] = []
    off = 32
    idx = 0
    chain_ok = header_ok
    pending: list[Frame] = []
    while off + 24 + page_size <= len(wal):
        pgno, commit, fs1, fs2, fc1, fc2 = struct.unpack(">6I", wal[off:off + 24])
        data = wal[off + 24:off + 24 + page_size]
        valid = False
        if chain_ok and (fs1, fs2) == (salt1, salt2):
            s0, s1 = _wal_checksum(wal[off:off + 8], s0, s1, big)
            s0, s1 = _wal_checksum(data, s0, s1, big)
            valid = (s0, s1) == (fc1, fc2)
        if not valid:
            chain_ok = False  # SQLite ignores everything after the first bad frame
        fr = Frame(idx, pgno, commit, data, valid)
        frames.append(fr)
        if valid:
            pending.append(fr)
            if commit:
                for p in pending:
                    p.committed = True
                    p.txn_end = idx
                pending = []
        off += 24 + page_size
        idx += 1
    for p in pending:  # uncommitted tail: its transaction extends to the last frame
        p.txn_end = len(frames) - 1
    return page_size, frames


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    for i in range(8):
        b = buf[pos + i]
        result = (result << 7) | (b & 0x7F)
        if not b & 0x80:
            return result, pos + i + 1
    return (result << 8) | buf[pos + 8], pos + 9


def _decode_record(payload: bytes) -> list[Any]:
    hdr_len, pos = _varint(payload, 0)
    types = []
    while pos < hdr_len:
        t, pos = _varint(payload, pos)
        types.append(t)
    values: list[Any] = []
    pos = hdr_len
    for t in types:
        if t == 0:
            values.append(None)
        elif 1 <= t <= 6:
            size = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8}[t]
            values.append(int.from_bytes(payload[pos:pos + size], "big", signed=True))
            pos += size
        elif t == 7:
            values.append(struct.unpack(">d", payload[pos:pos + 8])[0])
            pos += 8
        elif t in (8, 9):
            values.append(t - 8)
        elif t >= 12 and t % 2 == 0:
            n = (t - 12) // 2
            values.append(payload[pos:pos + n])
            pos += n
        elif t >= 13:
            n = (t - 13) // 2
            values.append(payload[pos:pos + n].decode("utf-8", errors="replace"))
            pos += n
        else:
            values.append(None)  # reserved serial types 10, 11
    return values


def _leaf_cells(pages: _Pages, page: bytes, pgno: int, upto_frame: int | None) -> list[tuple[int, list[Any]]]:
    hdr = 100 if pgno == 1 else 0
    if len(page) < hdr + 8 or page[hdr] != 0x0D:
        return []
    ncells = struct.unpack_from(">H", page, hdr + 3)[0]
    out = []
    u = pages.usable
    max_local = u - 35
    min_local = ((u - 12) * 32 // 255) - 23
    for i in range(ncells):
        try:
            ptr = struct.unpack_from(">H", page, hdr + 8 + 2 * i)[0]
            psize, p = _varint(page, ptr)
            rowid, p = _varint(page, p)
            if rowid & (1 << 63):
                rowid -= 1 << 64
            if psize <= max_local:
                payload = page[p:p + psize]
            else:
                k = min_local + ((psize - min_local) % (u - 4))
                local = k if k <= max_local else min_local
                payload = bytearray(page[p:p + local])
                nxt = struct.unpack_from(">I", page, p + local)[0]
                guard = 0
                while nxt and len(payload) < psize and guard < 100000:
                    ov = pages.at(nxt, upto_frame)
                    if ov is None:
                        break
                    nxt = struct.unpack_from(">I", ov, 0)[0]
                    payload += ov[4:u][: psize - len(payload)]
                    guard += 1
                payload = bytes(payload)
                if len(payload) < psize:
                    continue  # overflow chain incomplete; skip rather than guess
            out.append((rowid, _decode_record(payload)))
        except (IndexError, struct.error, ValueError):
            continue
    return out


def _table_pages(pages: _Pages, root: int) -> tuple[set[int], set[int]]:
    """(leaf pages, interior pages) of a table b-tree in its current state."""
    leaves, interiors = set(), set()
    stack, seen = [root], set()
    while stack:
        pgno = stack.pop()
        if pgno in seen or pgno < 1:
            continue
        seen.add(pgno)
        page = pages.current(pgno)
        if page is None:
            continue
        hdr = 100 if pgno == 1 else 0
        kind = page[hdr]
        if kind == 0x0D:
            leaves.add(pgno)
        elif kind == 0x05:
            interiors.add(pgno)
            ncells = struct.unpack_from(">H", page, hdr + 3)[0]
            for i in range(ncells):
                ptr = struct.unpack_from(">H", page, hdr + 12 + 2 * i)[0]
                stack.append(struct.unpack_from(">I", page, ptr)[0])
            stack.append(struct.unpack_from(">I", page, hdr + 8)[0])
    return leaves, interiors


def _schema(pages: _Pages) -> dict[str, tuple[int, str]]:
    """table name -> (root page, CREATE sql) from sqlite_master in the current state."""
    out = {}
    leaves, _ = _table_pages(pages, 1)
    for pgno in leaves:
        for _rowid, vals in _leaf_cells(pages, pages.current(pgno) or b"", pgno, None):
            if len(vals) >= 5 and vals[0] == "table" and isinstance(vals[3], int):
                out[str(vals[1])] = (vals[3], str(vals[4] or ""))
    return out


@dataclass
class Recovery:
    table: str
    versions: list[RowVersion]       # every distinct version found, oldest first
    frames_total: int
    frames_valid: int
    note: str = ""


def recover_table_versions(db_path: Path, table: str, wal_path: Path | None = None) -> Recovery:
    """Every distinct version of every row of ``table`` in the main file and the WAL.

    Rows are attributed to the table through the pages its b-tree uses in the
    current state; page versions in older frames are decoded with the same
    attribution. Versions are de-duplicated on (rowid, values).
    """
    db_path = Path(db_path)
    if db_path.stat().st_size < 100:
        # A new WAL-mode database can have an empty main file with every page in the WAL.
        return _recover(db_path, b"", table, wal_path)
    with open(db_path, "rb") as fh:
        # Memory-map read-only: photo databases can be gigabytes.
        db = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            return _recover(db_path, db, table, wal_path)
        finally:
            db.close()


def _recover(db_path: Path, db, table: str, wal_path: Path | None) -> Recovery:
    wal_path = wal_path or db_path.with_name(db_path.name + "-wal")
    frames: list[Frame] = []
    wal_page_size = 0
    if wal_path.is_file():
        wal_page_size, frames = read_wal(wal_path.read_bytes())
    if len(db) >= 100 and db[:16] == b"SQLite format 3\x00":
        page_size = struct.unpack_from(">H", db, 16)[0]
        page_size = 65536 if page_size == 1 else page_size
        reserved = db[20]
        if wal_page_size and wal_page_size != page_size:
            frames = []
    elif wal_page_size and len(db) < 100:
        page_size = wal_page_size
        first = next((f.data for f in frames if f.page == 1 and f.valid), b"")
        if first[:16] != b"SQLite format 3\x00":
            return Recovery(table, [], len(frames), sum(f.valid for f in frames), "no database header")
        reserved = first[20]
        db = b""
    else:
        return Recovery(table, [], 0, 0, "not a SQLite database")
    pages = _Pages(db, page_size, page_size - reserved, frames)
    schema = _schema(pages)
    if table not in schema:
        return Recovery(table, [], len(frames), sum(f.valid for f in frames), "table not found")
    leaves, _ = _table_pages(pages, schema[table][0])

    seen: set[tuple] = set()
    versions: list[RowVersion] = []

    def add(rows, source, frame, committed):
        for rowid, vals in rows:
            key = (rowid, tuple(v if not isinstance(v, float) else repr(v) for v in vals))
            if key in seen:
                continue
            seen.add(key)
            versions.append(RowVersion(rowid, vals, source, frame, committed))

    for pgno in sorted(leaves):
        page = pages.from_db(pgno)
        if page is not None:
            add(_leaf_cells(pages, page, pgno, None), "db", None, True)
    for fr in frames:
        if fr.valid and fr.page in leaves:
            # Overflow pages of a cell may be written later in the same transaction.
            add(_leaf_cells(pages, fr.data, fr.page, max(fr.index, fr.txn_end)), "wal", fr.index, fr.committed)
    return Recovery(table, versions, len(frames), sum(f.valid for f in frames))
