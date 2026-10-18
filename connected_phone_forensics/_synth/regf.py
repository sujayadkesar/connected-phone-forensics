"""Minimal Windows registry hive (REGF) writer for synthetic data.

Writes the subset of the format parsers need: base block, one hive bin,
nk (key), lh (subkey index), value-list, vk (value) and data cells. Layout
follows the public REGF documentation (Maxim Suhanov, "Windows registry file
format specification"). Not for producing evidence: fixtures only.
"""

from __future__ import annotations

import struct

REG_SZ = 1
REG_EXPAND_SZ = 2
REG_BINARY = 3
REG_DWORD = 4
REG_MULTI_SZ = 7
REG_QWORD = 11
DEVPROP_FILETIME = 0xFFFF0010  # device property stored as FILETIME


class _Key:
    def __init__(self, name: str, last_written: int):
        self.name = name
        self.last_written = last_written
        self.children: dict[str, _Key] = {}
        self.values: list[tuple[str, int, bytes]] = []


def _lh_hash(name: str) -> int:
    h = 0
    for ch in name.upper():
        h = (h * 37 + ord(ch)) & 0xFFFFFFFF
    return h


class HiveBuilder:
    def __init__(self, root_name: str = "ROOT", last_written: int = 133_000_000_000_000_000):
        self.root = _Key(root_name, last_written)
        self.default_time = last_written

    def key(self, path: str, last_written: int | None = None) -> _Key:
        node = self.root
        for part in [p for p in path.split("\\") if p]:
            child = next((c for k, c in node.children.items() if k.lower() == part.lower()), None)
            if child is None:
                child = _Key(part, self.default_time)
                node.children[part] = child
            node = child
        if last_written is not None:
            node.last_written = last_written
        return node

    def value(self, path: str, name: str, data, vtype: int | None = None) -> None:
        if vtype is None:
            vtype = REG_DWORD if isinstance(data, int) else REG_SZ if isinstance(data, str) else REG_BINARY
        if vtype in (REG_SZ, REG_EXPAND_SZ):
            raw = str(data).encode("utf-16-le") + b"\x00\x00"
        elif vtype == REG_MULTI_SZ:
            raw = b"".join(s.encode("utf-16-le") + b"\x00\x00" for s in data) + b"\x00\x00"
        elif vtype == REG_DWORD:
            raw = struct.pack("<I", data)
        elif vtype in (REG_QWORD, DEVPROP_FILETIME):
            raw = struct.pack("<Q", data)
        else:
            raw = bytes(data)
        self.key(path).values.append((name, vtype, raw))

    # -- serialisation ---------------------------------------------------------

    def build(self) -> bytes:
        cells = bytearray()

        def alloc(payload: bytes) -> int:
            size = len(payload) + 4
            size = (size + 7) & ~7
            off = len(cells)
            cells.extend(struct.pack("<i", -size))
            cells.extend(payload)
            cells.extend(b"\x00" * (size - 4 - len(payload)))
            return off + 32  # offsets count from the start of the hbin, header included

        def write_key(key: _Key, parent_off: int, is_root: bool) -> int:
            # Children first so their offsets are known.
            nk_placeholder = alloc(b"\x00" * (76 + len(key.name.encode("ascii"))))
            child_offs = [(c.name, write_key(c, nk_placeholder, False))
                          for c in sorted(key.children.values(), key=lambda c: c.name.upper())]
            subkeys_off = 0xFFFFFFFF
            if child_offs:
                payload = b"lh" + struct.pack("<H", len(child_offs))
                for name, off in child_offs:
                    payload += struct.pack("<II", off, _lh_hash(name))
                subkeys_off = alloc(payload)
            values_off = 0xFFFFFFFF
            if key.values:
                vk_offs = []
                for name, vtype, raw in key.values:
                    nb = name.encode("ascii")
                    if len(raw) <= 4:
                        data_size = len(raw) | 0x80000000
                        data_off = struct.unpack("<I", raw.ljust(4, b"\x00"))[0]
                    else:
                        data_size = len(raw)
                        data_off = alloc(raw)
                    vk = (b"vk" + struct.pack("<HIIIHH", len(nb), data_size, data_off, vtype, 1, 0) + nb)
                    vk_offs.append(alloc(vk))
                values_off = alloc(b"".join(struct.pack("<I", o) for o in vk_offs))
            nb = key.name.encode("ascii")
            flags = 0x20 | (0x04 | 0x08 if is_root else 0)
            nk = b"nk" + struct.pack(
                "<HQIIIIIIIIIIIIIIIHH",
                flags, key.last_written, 0, parent_off if not is_root else 0,
                len(child_offs), 0, subkeys_off, 0xFFFFFFFF,
                len(key.values), values_off, 0xFFFFFFFF, 0xFFFFFFFF,
                max((len(c.name) * 2 for c in key.children.values()), default=0), 0,
                max((len(n) * 2 for n, _, _ in key.values), default=0),
                max((len(r) for _, _, r in key.values), default=0), 0,
                len(nb), 0,
            ) + nb
            at = nk_placeholder - 32 + 4
            cells[at: at + len(nk)] = nk
            return nk_placeholder

        root_off = write_key(self.root, 0, True)
        hbin_size = ((len(cells) + 32 + 4095) // 4096) * 4096
        free = hbin_size - 32 - len(cells)
        if free:
            cells.extend(struct.pack("<i", free) + b"\x00" * (free - 4))
        hbin = b"hbin" + struct.pack("<IIQQI", 0, hbin_size, 0, 0, 0) + bytes(cells)

        base = bytearray(4096)
        struct.pack_into("<4sIIQIIIIIII", base, 0, b"regf", 1, 1, self.default_time,
                         1, 5, 0, 1, root_off, hbin_size, 1)
        checksum = 0
        for i in range(0, 508, 4):
            checksum ^= struct.unpack_from("<I", base, i)[0]
        struct.pack_into("<I", base, 508, checksum)
        return bytes(base) + hbin


# -- transaction logs -----------------------------------------------------------


def _checksum(block: bytes) -> int:
    from ..readers.regf_log import base_block_checksum

    return base_block_checksum(block)


def set_sequence(hive: bytes, primary: int, secondary: int) -> bytes:
    """``hive`` with new base-block sequence numbers; primary != secondary makes it dirty."""
    out = bytearray(hive)
    struct.pack_into("<II", out, 4, primary, secondary)
    struct.pack_into("<I", out, 508, _checksum(out))
    return bytes(out)


def hvle_entry(seq: int, hbins_size: int, pages: list[tuple[int, bytes]], flags: int = 0) -> bytes:
    """One new-format log entry; ``pages`` are (offset into hive bins data, data)."""
    from ..readers.regf_log import marvin32

    body = b"".join(struct.pack("<II", off, len(data)) for off, data in pages)
    body += b"".join(data for _off, data in pages)
    size = -(-(40 + len(body)) // 512) * 512
    body = body.ljust(size - 40, b"\x00")
    head = struct.pack("<4sIIIII", b"HvLE", size, flags, seq, hbins_size, len(pages))
    hash1 = marvin32(body)
    hash2 = marvin32(head + struct.pack("<Q", hash1))
    return head + struct.pack("<QQ", hash1, hash2) + body


def hive_bins_entry(seq: int, hive: bytes) -> bytes:
    """A log entry rewriting all of ``hive``'s hive bins (the simplest valid dirty-page set)."""
    return hvle_entry(seq, len(hive) - 4096, [(0, hive[4096:])])


def new_format_log(hive: bytes, first_seq: int, entries: list[bytes]) -> bytes:
    """A Windows 8.1+ ``.LOG1``/``.LOG2``: base block copy (file type 6), then entries."""
    base = bytearray(hive[:512])
    struct.pack_into("<II", base, 4, first_seq, first_seq)
    struct.pack_into("<I", base, 28, 6)
    struct.pack_into("<I", base, 508, _checksum(base))
    return bytes(base) + b"".join(entries)


def old_format_log(hive_after: bytes, seq: int, dirty_pages: list[int], timestamp: int | None = None) -> bytes:
    """A Windows XP-8 ``.LOG``: base block copy, DIRT vector, then the listed 512-byte pages of ``hive_after``."""
    base = bytearray(hive_after[:512])
    struct.pack_into("<II", base, 4, seq, seq)
    struct.pack_into("<I", base, 28, 1)
    if timestamp is not None:
        struct.pack_into("<Q", base, 12, timestamp)
    struct.pack_into("<I", base, 508, _checksum(base))
    hbins_size = struct.unpack_from("<I", hive_after, 40)[0]
    vector = bytearray(hbins_size // 4096)
    for page in dirty_pages:
        vector[page // 8] |= 1 << (page % 8)
    dirt = b"DIRT" + bytes(vector)
    dirt = dirt.ljust(-(-len(dirt) // 512) * 512, b"\x00")
    pages = b"".join(hive_after[4096 + 512 * p:4096 + 512 * (p + 1)] for p in sorted(dirty_pages))
    return bytes(base) + dirt + pages
