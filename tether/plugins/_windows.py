"""Helpers shared by Windows plugins (not a plugin module)."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ..plugin import Context
from ..readers.registry import Hive, RegKey, control_set_paths
from ..timeutil import from_datetime, from_filetime

SYSTEM_HIVE = "Windows/System32/config/SYSTEM"
SOFTWARE_HIVE = "Windows/System32/config/SOFTWARE"


def hive_path(ctx: Context, rel: str) -> Path | None:
    hits = ctx.files(rel)
    return hits[0] if hits else None


@contextmanager
def system_hive(ctx: Context) -> Iterator[tuple[Hive, Path, list[str]] | None]:
    """Open SYSTEM; yields (hive, path, control set roots) or None if absent."""
    p = hive_path(ctx, SYSTEM_HIVE)
    if p is None:
        yield None
        return
    with ctx.hive(p) as h:
        yield h, p, control_set_paths(h)


@contextmanager
def software_hive(ctx: Context) -> Iterator[tuple[Hive, Path] | None]:
    p = hive_path(ctx, SOFTWARE_HIVE)
    if p is None:
        yield None
        return
    with ctx.hive(p) as h:
        yield h, p


def windows_timezone(hive: Hive, control_sets: list[str]):
    """The evidence system's timezone, or None if the key is missing."""
    from ..wintz import WindowsTimezone

    for cs in control_sets:
        k = hive.key(f"{cs}\\Control\\TimeZoneInformation")
        if k is not None:
            return WindowsTimezone.from_key(k)
    return None


def reg_time(value: Any) -> datetime | None:
    """A registry value holding a FILETIME as QWORD, 8 bytes, or a decoded datetime."""
    if isinstance(value, datetime):
        return from_datetime(value)
    if isinstance(value, (bytes, bytearray)):
        return from_filetime(bytes(value[:8])) if len(value) >= 8 else None
    if isinstance(value, int):
        return from_filetime(value)
    return None


def reg_text(value: Any) -> str | None:
    """Decode a registry string that may be stored as REG_SZ or as raw bytes."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.rstrip("\x00").strip() or None
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        # UTF-16LE strings have NULs in every other byte for ASCII text.
        if len(raw) >= 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
            text = raw.decode("utf-16-le", errors="replace")
        else:
            text = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return text.rstrip("\x00").strip() or None
    return str(value)


def reg_dword(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, (bytes, bytearray)) and len(value) >= 4:
        return struct.unpack_from("<I", value)[0]
    return None


def key_locator(hive_file: str, key: RegKey, value: str | None = None) -> str:
    loc = f"{hive_file}:{key.path}"
    return f"{loc}\\{value}" if value else loc
