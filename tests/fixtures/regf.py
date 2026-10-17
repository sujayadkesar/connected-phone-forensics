"""Minimal Windows registry hive (REGF) writer for building test fixtures.

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
        elif vtype in (REG_QWORD,) or vtype == DEVPROP_FILETIME:
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
