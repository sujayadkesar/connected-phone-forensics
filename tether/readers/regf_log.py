"""Registry transaction-log replay, following the REGF format specification.

A hive's ``.LOG``/``.LOG1``/``.LOG2`` files hold writes that may not have
reached the primary file yet. Windows reuses log files without clearing them,
so a log usually also holds entries that were flushed long ago. Replaying those
writes older pages over the current hive, so the rules below decide which
entries are applied:

* Logs are applied only to a dirty hive: its base block has a wrong checksum or
  its primary and secondary sequence numbers differ. A clean hive is read as is.
* New format (Windows 8.1+, ``HvLE`` entries): every entry's Marvin32 hashes must
  verify. A log's first entry must carry the log base block's primary sequence
  number, which must not be below the hive's secondary sequence number. Entries
  are applied in sequence order across LOG1 and LOG2, the log with the earlier
  entries first, and replay stops at the first gap or damaged entry. The hive is
  grown when an entry's hive bins data size says so. If the hive's base block is
  damaged, it is taken from the log with the latest entries, and only that log
  is used.
* Old format (``DIRT`` vector, Windows XP to 8): a log is applied only when its
  base block is valid, its sequence numbers match and its last-written timestamp
  equals the hive's. Old-format logs are ignored when a new-format log exists.

Not implemented: the per-hive-bin checks of old-format recovery and the
replacement of invalid hive bins with dummy bins.

Reference: Maxim Suhanov, "Windows registry file format specification",
https://github.com/msuhanov/regf
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

BASE_BLOCK_SIZE = 4096
SECTOR = 512
_ENTRY_HEADER = 40
_MARVIN_SEED = 0x82EF4D887A4E55C5
_M32 = 0xFFFFFFFF

# Replay.status values
CLEAN = "clean"              # hive not dirty; logs present but not needed
APPLIED = "applied"          # at least one log entry (or old-format log) applied
NOT_APPLIED = "not_applied"  # hive dirty or unreadable, and nothing could be applied


def _rotl(x: int, n: int) -> int:
    return ((x << n) | (x >> (32 - n))) & _M32


def marvin32(data: bytes, seed: int = _MARVIN_SEED) -> int:
    """Marvin32 hash, as used for HvLE log entries (Hash-1 and Hash-2)."""
    p0, p1 = seed & _M32, seed >> 32

    def block() -> None:
        nonlocal p0, p1
        p1 ^= p0
        p0 = (_rotl(p0, 20) + p1) & _M32
        p1 = _rotl(p1, 9) ^ p0
        p0 = (_rotl(p0, 27) + p1) & _M32
        p1 = _rotl(p1, 19)

    whole = len(data) - len(data) % 4
    for (word,) in struct.iter_unpack("<I", data[:whole]):
        p0 = (p0 + word) & _M32
        block()
    tail = data[whole:]
    p0 = (p0 + (int.from_bytes(tail, "little") | (0x80 << (8 * len(tail))))) & _M32
    block()
    block()
    return (p1 << 32) | p0


def base_block_checksum(block: bytes) -> int:
    """XOR-32 of the first 508 bytes; never 0 or 0xFFFFFFFF."""
    c = 0
    for (word,) in struct.iter_unpack("<I", block[:508]):
        c ^= word
    if c == _M32:
        return 0xFFFFFFFE
    return c or 1


@dataclass
class BaseBlock:
    primary_seq: int
    secondary_seq: int
    timestamp: int
    hbins_size: int
    clustering: int
    valid: bool  # signature and checksum both correct

    @classmethod
    def parse(cls, data: bytes) -> BaseBlock | None:
        if len(data) < SECTOR:
            return None
        sig, prim, sec, ts = struct.unpack_from("<4sIIQ", data, 0)
        hbins, clustering = struct.unpack_from("<II", data, 40)
        (checksum,) = struct.unpack_from("<I", data, 508)
        valid = sig == b"regf" and checksum == base_block_checksum(data)
        return cls(prim, sec, ts, hbins, clustering, valid)

    @property
    def dirty(self) -> bool:
        return not self.valid or self.primary_seq != self.secondary_seq


@dataclass
class LogEntry:
    seq: int
    flags: int
    hbins_size: int
    pages: list[tuple[int, bytes]]  # (offset into hive bins data, page data)


@dataclass
class LogResult:
    """What became of one transaction-log file."""

    name: str
    format: str = "unknown"  # "new" | "old" | "unknown"
    applied: int = 0         # entries (new format) or dirty pages (old format) applied
    note: str = ""


@dataclass
class Replay:
    status: str
    data: bytes | None = None  # the recovered hive, when status is APPLIED
    logs: list[LogResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class _NewLog:
    result: LogResult
    base: BaseBlock | None
    raw: bytes
    entries: list[LogEntry]
    damage: str = ""                # why entry scanning stopped early, if it did
    damaged_seq: int | None = None  # sequence number of the damaged entry, if its header verified


def _parse_entries(log: _NewLog) -> None:
    """HvLE entries from offset 512 up to the first non-entry or damaged entry."""
    raw = log.raw
    off = SECTOR
    while off + _ENTRY_HEADER <= len(raw) and raw[off:off + 4] == b"HvLE":
        size, flags, seq, hbins, count = struct.unpack_from("<IIIII", raw, off + 4)
        hash1, hash2 = struct.unpack_from("<QQ", raw, off + 24)
        if marvin32(raw[off:off + 32]) != hash2:
            log.damage = f"damaged entry at offset {off:#x} (bad header hash)"
            return
        problem = ""
        if size < _ENTRY_HEADER or size % SECTOR or off + size > len(raw):
            problem = "invalid size"
        elif hbins % BASE_BLOCK_SIZE or _ENTRY_HEADER + 8 * count > size:
            problem = "invalid fields"
        elif marvin32(raw[off + _ENTRY_HEADER:off + size]) != hash1:
            problem = "bad data hash"
        pages = []
        pos = off + _ENTRY_HEADER + 8 * count
        for i in range(count if not problem else 0):
            page_off, page_size = struct.unpack_from("<II", raw, off + _ENTRY_HEADER + 8 * i)
            if pos + page_size > off + size or page_off + page_size > hbins:
                problem = "page outside the hive"
                break
            pages.append((page_off, raw[pos:pos + page_size]))
            pos += page_size
        if problem:
            log.damage = f"damaged entry {seq} at offset {off:#x} ({problem})"
            log.damaged_seq = seq
            return
        log.entries.append(LogEntry(seq, flags, hbins, pages))
        off += size


def _finish_base(buf: bytearray, seq: int, hbins: int | None = None, flags_bit: int | None = None) -> None:
    """Mark the recovered copy clean: equal sequence numbers, primary file type, new checksum."""
    struct.pack_into("<II", buf, 4, seq, seq)
    struct.pack_into("<I", buf, 28, 0)
    if hbins is not None:
        struct.pack_into("<I", buf, 40, hbins)
    if flags_bit is not None:
        (flags,) = struct.unpack_from("<I", buf, 144)
        struct.pack_into("<I", buf, 144, (flags & ~1) | (flags_bit & 1))
    struct.pack_into("<I", buf, 508, base_block_checksum(buf))


def _grow(buf: bytearray, hbins: int) -> None:
    if len(buf) < BASE_BLOCK_SIZE + hbins:
        buf.extend(b"\x00" * (BASE_BLOCK_SIZE + hbins - len(buf)))


def _seqs(first: int, last: int) -> str:
    return f"{first}" if first == last else f"{first}-{last}"


def _dirty_warning(base: BaseBlock, why: str) -> str:
    return (f"hive is dirty (sequence {base.primary_seq}/{base.secondary_seq}) but {why}; "
            "changes not yet written to the hive are missing")


def _replay_new(hive: bytes, base: BaseBlock, logs: list[_NewLog]) -> Replay:
    usable: list[_NewLog] = []
    for log in logs:
        r = log.result
        if log.base is None or not log.base.valid:
            r.note = "not applied: the log's base block is invalid"
        elif not log.entries:
            r.note = "not applied: no readable log entries"
        elif log.entries[0].seq != log.base.primary_seq:
            r.note = (f"not applied: its first entry has sequence {log.entries[0].seq}, "
                      f"its base block expects {log.base.primary_seq}")
        elif base.valid and log.base.primary_seq < base.secondary_seq:
            r.note = (f"not applied: its entries start at sequence {log.base.primary_seq}, "
                      f"before the hive's {base.secondary_seq}")
        else:
            usable.append(log)
    if not base.valid:
        *dropped, latest = sorted(usable, key=lambda lg: lg.base.primary_seq) or [None]
        for log in dropped:
            log.result.note = ("not applied: the hive's base block is damaged, so only the log "
                               "with the latest entries is used")
        usable = [latest] if latest else []
    usable.sort(key=lambda lg: lg.entries[0].seq)
    if not usable:
        return Replay(NOT_APPLIED, warnings=[_dirty_warning(base, "no transaction log holds applicable entries")],
                      summary="hive is dirty; no applicable transaction-log entries")

    buf = bytearray(hive)
    if not base.valid:
        buf[:SECTOR] = usable[0].raw[:SECTOR]
    warnings: list[str] = []
    first: int | None = None
    last: int | None = None
    last_entry: LogEntry | None = None
    for log in usable:
        applied: list[int] = []
        for entry in log.entries:
            if last is not None and not applied and entry.seq <= last:
                continue  # already applied from the log with earlier entries
            if last is not None and entry.seq != last + 1:
                break
            _grow(buf, entry.hbins_size)
            for page_off, data in entry.pages:
                at = BASE_BLOCK_SIZE + page_off
                buf[at:at + len(data)] = data
            applied.append(entry.seq)
            first = entry.seq if first is None else first
            last, last_entry = entry.seq, entry
        log.result.applied = len(applied)
        log.result.note = (f"applied log entries {_seqs(applied[0], applied[-1])}" if applied
                           else f"not applied: no entry continues after sequence {last}")
    for log in usable:
        if log.damaged_seq is not None and log.damaged_seq == last + 1:
            warnings.append(f"{log.result.name}: log entry {log.damaged_seq} is damaged; "
                            "the changes it holds, and any after it, are missing")
    _finish_base(buf, last, last_entry.hbins_size, last_entry.flags)
    used = ", ".join(lg.result.name for lg in usable if lg.result.applied)
    return Replay(APPLIED, bytes(buf), warnings=warnings,
                  summary=f"applied transaction-log entries {_seqs(first, last)} from {used}")


def _replay_old(hive: bytes, base: BaseBlock, logs: list[tuple[LogResult, bytes]]) -> Replay:
    # A damaged base block has no trustworthy timestamp; the first hive bin's is used instead.
    stamp = base.timestamp if base.valid else struct.unpack_from("<Q", hive, BASE_BLOCK_SIZE + 20)[0]
    candidates = []
    for r, raw in logs:
        lb = BaseBlock.parse(raw)
        if lb is None or not lb.valid or lb.primary_seq != lb.secondary_seq:
            r.note = "not applied: the log's base block is invalid or incomplete"
        elif lb.timestamp != stamp:
            r.note = "not applied: its last-written timestamp does not match the hive's"
        else:
            candidates.append((lb, r, raw))
    if not candidates:
        return Replay(NOT_APPLIED, warnings=[_dirty_warning(base, "no transaction log matches it")],
                      summary="hive is dirty; no matching transaction log")
    candidates.sort(key=lambda c: c[0].primary_seq)
    lb, r, raw = candidates[-1]
    for _lb, other, _raw in candidates[:-1]:
        other.note = f"not applied: {r.name} is newer"
    sector = SECTOR * min(max(lb.clustering, 1), 8)
    vector_len = lb.hbins_size // BASE_BLOCK_SIZE  # one bit per 512-byte page
    bitmap = raw[sector + 4:sector + 4 + vector_len]
    if raw[sector:sector + 4] != b"DIRT" or len(bitmap) < vector_len:
        r.note = "not applied: the dirty-page vector is missing or truncated"
        return Replay(NOT_APPLIED, warnings=[_dirty_warning(base, f"{r.name} is damaged")],
                      summary="hive is dirty; its transaction log is damaged")
    dirty = [i for i in range(lb.hbins_size // SECTOR) if bitmap[i // 8] >> (i % 8) & 1]
    pos = -(-(sector + 4 + vector_len) // sector) * sector  # pages start at the next sector
    if len(raw) < pos + SECTOR * len(dirty):
        r.note = f"not applied: the log is truncated ({len(dirty)} dirty pages expected)"
        return Replay(NOT_APPLIED, warnings=[_dirty_warning(base, f"{r.name} is truncated")],
                      summary="hive is dirty; its transaction log is truncated")
    buf = bytearray(hive)
    _grow(buf, lb.hbins_size)
    for n, page in enumerate(dirty):
        at = BASE_BLOCK_SIZE + SECTOR * page
        buf[at:at + SECTOR] = raw[pos + SECTOR * n:pos + SECTOR * (n + 1)]
    buf[:SECTOR] = raw[:SECTOR]  # the log carries the base block of the state it describes
    _finish_base(buf, lb.primary_seq)
    r.applied = len(dirty)
    r.note = f"applied {len(dirty)} dirty page(s)"
    return Replay(APPLIED, bytes(buf), summary=f"applied old-format transaction log {r.name}")


def replay(hive: bytes, logs: list[tuple[str, bytes]]) -> Replay:
    """Decide whether and how to apply ``logs`` (name, content) to ``hive``."""
    results = [LogResult(name) for name, _ in logs]
    base = BaseBlock.parse(hive[:BASE_BLOCK_SIZE])
    if base is None or hive[:4] != b"regf" or len(hive) < BASE_BLOCK_SIZE + 32:
        return Replay(NOT_APPLIED, logs=results, summary="hive base block unreadable",
                      warnings=["hive has no readable base block; transaction logs not applied"])
    new_logs: list[_NewLog] = []
    old_logs: list[tuple[LogResult, bytes]] = []
    for r, (_name, raw) in zip(results, logs, strict=True):
        magic = raw[SECTOR:SECTOR + 4]
        if magic == b"HvLE":
            r.format = "new"
            log = _NewLog(r, BaseBlock.parse(raw[:SECTOR]), raw, [])
            _parse_entries(log)
            new_logs.append(log)
        elif magic == b"DIRT":
            r.format = "old"
            old_logs.append((r, raw))
        else:
            r.note = "no log entries"

    if not base.dirty:
        for r in results:
            r.note = f"not applied: hive is clean (sequence {base.primary_seq}/{base.secondary_seq})"
        return Replay(CLEAN, logs=results, summary="hive is clean; transaction logs not applied")
    if new_logs:
        for r, _raw in old_logs:
            r.note = "not applied: old-format log ignored next to a new-format log"
        out = _replay_new(hive, base, new_logs)
        for log in new_logs:
            if log.damage:
                log.result.note += f"; {log.damage}"
    elif old_logs:
        out = _replay_old(hive, base, old_logs)
    else:
        out = Replay(NOT_APPLIED, warnings=[_dirty_warning(base, "its transaction logs hold no entries")],
                     summary="hive is dirty; transaction logs are empty")
    out.logs = results  # every log, in the order given
    return out
