"""Recover earlier versions of rows from a SQLite write-ahead log.

A WAL file holds whole page images, one frame per page write, appended until
a checkpoint copies them into the database. A row that was updated or
deleted after the last checkpoint therefore still exists in older frames
(and in the main database file). Opening the database normally shows only
the newest state; this module reads every committed state of the database
and returns each distinct row version of a table with where it came from.

Page numbers are reused: a page freed by one table can be reallocated to
another, so an image of page N says nothing by itself about which table its
rows belong to. Rows are therefore attributed per state. The main file is one
state; the end of every committed transaction in the WAL is another (the main
file overlaid with every frame up to that commit). In each state the table's
root page is read from that state's ``sqlite_master`` and the table's b-tree
is walked using only that state's page images, overflow chains included. Only
cells of leaves reachable from the root in the same state are returned.
Pages reachable in no state (freelist pages, other tables' pages) are never
decoded as rows of the table.

Frames after the last commit belong to a transaction that was still open or
was rolled back. They do not form a consistent state, so they are counted
(``Recovery.uncommitted_frames``) but not searched. Frames after a checksum or
salt mismatch are ignored, as SQLite ignores them.

When the WAL has been checkpointed but not yet restarted, the main file holds
the state of a later commit, and the page images that earlier states need
were overwritten. Such pages are treated as unavailable in those states
instead of being guessed at, as are pages that a later checkpoint which
stopped part-way may have copied (pages are copied in ascending order, so
every lower page written by then must already be in the main file too; a
page SQLite merely rewrote unchanged does not count). The main file is then
not a state of its own, as it holds the state of a WAL commit.
``Recovery.incomplete`` counts the states that could only be read in part.

Not recovered: rows deleted before the oldest state (left in free space of
live pages or on freelist pages), rows of WITHOUT ROWID tables, and page
images inside a transaction that were overwritten before it committed.

Pure Python, read-only, independent of the sqlite3 library. Implements the
file format documented at https://www.sqlite.org/fileformat2.html (database
header, b-tree pages, record format, overflow pages, text encoding) and
https://www.sqlite.org/walformat.html (WAL header, frames, checksums).
"""

from __future__ import annotations

import bisect
import mmap
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WAL_MAGIC_LE = 0x377F0682
WAL_MAGIC_BE = 0x377F0683
MAIN = -1  # page image source: the main database file
_ENCODINGS = {1: "utf-8", 2: "utf-16-le", 3: "utf-16-be"}


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
    frame: int | None     # WAL frame holding the leaf page image, None for the main file


@dataclass
class _Pages:
    db: bytes | mmap.mmap
    page_size: int
    usable: int
    frames: list[Frame] = field(default_factory=list)
    encoding: str = "utf-8"

    def __post_init__(self) -> None:
        self._frames_of: dict[int, list[int]] = {}   # page -> its committed frames, ascending
        # The last commit up to which every page written in the WAL is in the main file
        # as of that commit: a checkpoint copied the WAL up to there (-1: nothing copied).
        self.checkpointed = -1
        # (commit frame, lowest page written by then whose image as of that commit is
        # not the main file's, None if there is none)
        commits: list[tuple[int, int | None]] = []
        differs: set[int] = set()
        for fr in self.frames:
            if not (fr.valid and fr.committed):
                break
            self._frames_of.setdefault(fr.page, []).append(fr.index)
            if self.from_db(fr.page) == fr.data:
                differs.discard(fr.page)
            else:
                differs.add(fr.page)
            if fr.commit_size:
                commits.append((fr.index, min(differs, default=None)))
                if not differs:
                    self.checkpointed = fr.index
        # Pages whose main-file image is not their image before the WAL, so it is not
        # used for older states: overwritten by that checkpoint, or possibly copied by a
        # later one that stopped part-way. Page 1 is exempt from the second test: its
        # header counters often return to earlier values, and its image is identical to
        # that frame's whichever way it came about.
        self._replaced: set[int] = set()
        for pgno, idxs in self._frames_of.items():
            if idxs[0] <= self.checkpointed or (pgno != 1 and self._copied_part_way(pgno, idxs, commits)):
                self._replaced.add(pgno)

    def _copied_part_way(self, pgno: int, idxs: list[int], commits: list[tuple[int, int | None]]) -> bool:
        """Whether the main-file image of a page can be a checkpoint's copy of one of its frames.

        A checkpoint up to a commit copies pages in ascending order, each from
        its last frame up to that commit. If it stopped after this page, every
        lower page written by then is in the main file as of that commit too.
        A frame that merely has the same bytes (SQLite rewrites pages
        unchanged) does not meet that test.
        """
        main = self.from_db(pgno)
        ends = [m for m, _ in commits]
        for j, f in enumerate(idxs):
            if self.frames[f].data != main:
                continue
            nxt = idxs[j + 1] if j + 1 < len(idxs) else len(self.frames)
            for k in range(bisect.bisect_left(ends, f), bisect.bisect_left(ends, nxt)):
                lowest = commits[k][1]
                if lowest is None or lowest > pgno:
                    return True
        return False

    def from_db(self, pgno: int) -> bytes | None:
        off = (pgno - 1) * self.page_size
        if off < 0 or off + self.page_size > len(self.db):
            return None
        return self.db[off:off + self.page_size]

    def source(self, pgno: int, upto: int | None) -> int | None:
        """Where the image of a page in the state ending at frame ``upto`` is.

        ``upto=None`` is the main file's own state. Returns a frame index,
        ``MAIN`` for the main file, or None when the main file no longer
        holds the page's image for that state.
        """
        idxs = self._frames_of.get(pgno) if upto is not None else None
        if idxs:
            i = bisect.bisect_right(idxs, upto)
            if i:
                return idxs[i - 1]
        return None if pgno in self._replaced else MAIN

    def data(self, pgno: int, src: int) -> bytes | None:
        """Bytes of a page image found by ``source`` (None if the page is past the end of the file)."""
        return self.frames[src].data if src != MAIN else self.from_db(pgno)

    def at(self, pgno: int, upto: int | None) -> bytes | None:
        """Image of a page in the state ending at frame ``upto`` (None = the main file's own state)."""
        src = self.source(pgno, upto)
        return None if src is None else self.data(pgno, src)


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


_INT_SIZES = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8}


def _decode_record(payload: bytes, encoding: str = "utf-8") -> list[Any]:
    """Decode a record; raises ValueError if its header and body sizes do not add up."""
    hdr_len, pos = _varint(payload, 0)
    if hdr_len > len(payload):
        raise ValueError("record header longer than the payload")
    types = []
    while pos < hdr_len:
        t, pos = _varint(payload, pos)
        types.append(t)
    if pos != hdr_len or any(t in (10, 11) for t in types):
        raise ValueError("malformed record header")
    sizes = [_INT_SIZES.get(t, 8 if t == 7 else 0 if t < 12 else (t - 12) // 2) for t in types]
    if hdr_len + sum(sizes) != len(payload):
        raise ValueError("record size does not match its header")
    values: list[Any] = []
    for t, size in zip(types, sizes, strict=True):
        if t == 0:
            values.append(None)
        elif t in _INT_SIZES:
            values.append(int.from_bytes(payload[pos:pos + size], "big", signed=True))
        elif t == 7:
            values.append(struct.unpack(">d", payload[pos:pos + 8])[0])
        elif t in (8, 9):
            values.append(t - 8)
        elif t % 2 == 0:
            values.append(payload[pos:pos + size])
        else:
            values.append(payload[pos:pos + size].decode(encoding, errors="replace"))
        pos += size
    return values


def _leaf_cells(pages: _Pages, page: bytes, pgno: int,
                upto: int | None) -> tuple[list[tuple[int, list[Any]]], int]:
    """Cells of a table leaf page: ((rowid, values) list, number of cells skipped).

    Overflow pages are read from the same state (``upto``) as the leaf. A cell
    whose record does not decode, or whose overflow chain cannot be followed
    in that state, is skipped rather than guessed at.
    """
    hdr = 100 if pgno == 1 else 0
    if len(page) < hdr + 8 or page[hdr] != 0x0D:
        return [], 0
    ncells = struct.unpack_from(">H", page, hdr + 3)[0]
    out = []
    skipped = 0
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
                    ov = pages.at(nxt, upto)
                    if ov is None:
                        break
                    nxt = struct.unpack_from(">I", ov, 0)[0]
                    payload += ov[4:u][: psize - len(payload)]
                    guard += 1
                payload = bytes(payload)
            if len(payload) < psize:
                skipped += 1
                continue
            out.append((rowid, _decode_record(payload, pages.encoding)))
        except (IndexError, struct.error, ValueError):
            skipped += 1
    return out, skipped


def _node(page: bytes | None, pgno: int) -> tuple[int, list[int]]:
    """(page type, child page numbers left to right if a table interior page); type 0 if unreadable."""
    hdr = 100 if pgno == 1 else 0
    if page is None or len(page) < hdr + 12:
        return 0, []
    kind = page[hdr]
    if kind != 0x05:
        return kind, []
    try:
        ncells = struct.unpack_from(">H", page, hdr + 3)[0]
        kids = [struct.unpack_from(">I", page, struct.unpack_from(">H", page, hdr + 12 + 2 * i)[0])[0]
                for i in range(ncells)]
        kids.append(struct.unpack_from(">I", page, hdr + 8)[0])
    except struct.error:
        return 0, []
    return kind, kids


def _tree(pages: _Pages, root: int, upto: int | None,
          cache: dict) -> tuple[list[tuple[int, int]], set[int], bool, int]:
    """Leaves of a table b-tree in one state, in key order.

    Returns (leaves as (page number, source of the image), every page read,
    whether every page could be read and was a table b-tree page, page type
    of the root). ``cache`` keeps each page image's type and children, so an
    image is parsed once however many states share it.
    """
    leaves: list[tuple[int, int]] = []
    read: set[int] = set()
    complete = True
    root_kind = 0
    stack = [root]
    while stack:
        pgno = stack.pop()
        if pgno < 1 or pgno in read:
            complete = False  # no such page, or a loop: the tree is not intact here
            continue
        read.add(pgno)
        src = pages.source(pgno, upto)
        if src is None:
            complete = False
            continue
        if (pgno, src) not in cache:
            cache[pgno, src] = _node(pages.data(pgno, src), pgno)
        kind, kids = cache[pgno, src]
        if pgno == root:
            root_kind = kind
        if kind == 0x0D:
            leaves.append((pgno, src))
        elif kind == 0x05:
            stack.extend(reversed(kids))
        elif pgno != root or not kind:
            complete = False  # unreadable, or a child that is not a table b-tree page
    return leaves, read, complete, root_kind


def _schema(pages: _Pages, upto: int | None, cache: dict) -> tuple[dict[str, int], set[int], bool]:
    """Root page of every table in one state, by lower-case name, from sqlite_master.

    Returns (roots, pages read, whether sqlite_master could be read completely).
    """
    leaves, read, complete, _ = _tree(pages, 1, upto, cache)
    roots = {}
    for pgno, src in leaves:
        rows, skipped = _leaf_cells(pages, pages.data(pgno, src) or b"", pgno, upto)
        complete = complete and not skipped
        for _rowid, vals in rows:
            if (len(vals) >= 5 and vals[0] == "table" and isinstance(vals[1], str)
                    and isinstance(vals[3], int) and vals[3] > 0):
                roots[vals[1].lower()] = vals[3]
    return roots, read, complete and bool(leaves)


@dataclass
class Recovery:
    table: str
    versions: list[RowVersion]       # every distinct version found, main-file state first, then WAL order
    frames_total: int
    frames_valid: int
    note: str = ""                   # why recovery could not be performed, if it could not
    states: int = 0                  # database states in which the table's b-tree was walked
    incomplete: int = 0              # states that could only be read in part
    uncommitted_frames: int = 0      # valid frames after the last commit: not searched


def recover_table_versions(db_path: Path, table: str, wal_path: Path | None = None) -> Recovery:
    """Every distinct version of every row of ``table`` in the main file and the WAL.

    A row is attributed to the table only when its leaf page was reachable
    from the table's root in the state it was read from (see the module
    docstring). Versions are de-duplicated on (rowid, values); each is
    reported from the first state it appears in.
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
        header = bytes(db[:100])
        if wal_page_size and wal_page_size != page_size:
            frames = []
    elif wal_page_size and len(db) < 100:
        page_size = wal_page_size
        header = next((f.data[:100] for f in frames if f.page == 1 and f.valid), b"")
        if header[:16] != b"SQLite format 3\x00":
            return Recovery(table, [], len(frames), sum(f.valid for f in frames), "no database header")
        db = b""
    else:
        return Recovery(table, [], 0, 0, "not a SQLite database")
    reserved = header[20]
    encoding = _ENCODINGS.get(struct.unpack_from(">I", header, 56)[0], "utf-8")
    pages = _Pages(db, page_size, page_size - reserved, frames, encoding)
    rec = Recovery(table, [], len(frames), sum(f.valid for f in frames),
                   uncommitted_frames=sum(f.valid and not f.committed for f in frames))

    # States in order: the main file when it holds a state older than the WAL, then
    # the end of every committed transaction, each with the pages written since the
    # previous one. A main file that a checkpoint brought up to a commit holds that
    # commit's state, which is walked in its place among the WAL states.
    states: list[tuple[int | None, set[int] | None]] = []
    if len(db) and pages.checkpointed < 0:
        states.append((None, None))
    written: set[int] = set()
    for fr in frames:
        if not (fr.valid and fr.committed):
            break
        written.add(fr.page)
        if fr.commit_size:
            states.append((fr.index, written))
            written = set()

    key = table.lower()
    found = without_rowid = False
    seen: set[tuple] = set()
    decoded: set[tuple[int, int]] = set()   # leaf images (page, source) already decoded
    cache: dict = {}
    read: set[int] | None = None            # pages the last walked state read
    for upto, changed in states:
        if read is not None and changed is not None and read.isdisjoint(changed):
            continue  # nothing the last walk read has changed: the same leaves again
        roots, read, complete = _schema(pages, upto, cache)
        if key not in roots:
            rec.incomplete += not complete
            continue
        found = True
        leaves, tree_pages, complete, root_kind = _tree(pages, roots[key], upto, cache)
        read |= tree_pages
        if root_kind in (0x02, 0x0A):
            without_rowid = True
            continue
        rec.states += 1
        for pgno, src in leaves:
            if (pgno, src) in decoded:
                continue
            decoded.add((pgno, src))
            rows, skipped = _leaf_cells(pages, pages.data(pgno, src) or b"", pgno, upto)
            complete = complete and not skipped
            frame = None if src == MAIN else src
            for rowid, vals in rows:
                vkey = (rowid, tuple(v if not isinstance(v, float) else repr(v) for v in vals))
                if vkey not in seen:
                    seen.add(vkey)
                    rec.versions.append(RowVersion(rowid, vals, "db" if frame is None else "wal", frame))
        rec.incomplete += not complete
    if not found:
        rec.note = "table not found"
    elif without_rowid and not rec.states:
        rec.note = "WITHOUT ROWID table (not supported)"
    return rec
