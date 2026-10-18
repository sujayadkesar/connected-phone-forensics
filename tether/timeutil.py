"""Timestamp conversion for the encodings found in phone-related artifacts.

All functions return timezone-aware UTC datetimes, or None when the input is
empty, zero, or outside a plausible range. Returning None instead of a
1601-01-01 or 1970-01-01 placeholder keeps bogus dates out of timelines.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
_HFS_EPOCH = datetime(1904, 1, 1, tzinfo=UTC)

# Anything outside this window is treated as garbage rather than evidence.
PLAUSIBLE_MIN = datetime(1995, 1, 1, tzinfo=UTC)
PLAUSIBLE_MAX = datetime(2100, 1, 1, tzinfo=UTC)


def _check(dt: datetime | None) -> datetime | None:
    if dt is None or not (PLAUSIBLE_MIN <= dt < PLAUSIBLE_MAX):
        return None
    return dt


def _num(value) -> int | float | None:
    """Parse a number, keeping integers exact.

    FILETIME and nanosecond values exceed the 53-bit precision of a float, so
    integers (and integer strings) must never pass through float().
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value or None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text) or None
        except ValueError:
            pass
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n if n != 0 else None


def _delta_us(n: int | float, per_second: int) -> timedelta:
    """timedelta for n units where per_second units make one second, exact for ints."""
    if isinstance(n, int):
        if per_second <= 1_000_000:
            return timedelta(microseconds=n * 1_000_000 // per_second)
        return timedelta(microseconds=n // (per_second // 1_000_000))
    return timedelta(seconds=n / per_second)


def from_filetime(value) -> datetime | None:
    """Windows FILETIME: 100-ns intervals since 1601-01-01 UTC. Accepts int or 8 LE bytes."""
    if isinstance(value, (bytes, bytearray)):
        if len(value) < 8:
            return None
        value = struct.unpack_from("<Q", value)[0]
    n = _num(value)
    if n is None:
        return None
    try:
        return _check(_FILETIME_EPOCH + _delta_us(n, 10_000_000))
    except OverflowError:
        return None


def from_unix(value, unit: str = "s") -> datetime | None:
    """Unix epoch in seconds ('s'), milliseconds ('ms'), microseconds ('us') or nanoseconds ('ns')."""
    n = _num(value)
    if n is None:
        return None
    per_second = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[unit]
    try:
        return _check(_UNIX_EPOCH + _delta_us(n, per_second))
    except OverflowError:
        return None


def from_unix_auto(value) -> datetime | None:
    """Unix epoch with the unit guessed from magnitude (s, ms, us or ns)."""
    n = _num(value)
    if n is None:
        return None
    a = abs(n)
    unit = "s" if a < 1e11 else "ms" if a < 1e14 else "us" if a < 1e17 else "ns"
    return from_unix(n, unit)


def from_cocoa(value) -> datetime | None:
    """Apple Cocoa / Mac absolute time: seconds since 2001-01-01 UTC.

    Some Apple databases store nanoseconds since 2001 (e.g. Messages since
    macOS 10.13); values too large to be seconds are treated as nanoseconds.
    """
    n = _num(value)
    if n is None:
        return None
    try:
        if abs(n) > 1e11:
            return _check(_COCOA_EPOCH + _delta_us(n, 1_000_000_000))
        return _check(_COCOA_EPOCH + _delta_us(n, 1))
    except OverflowError:
        return None


def from_hfs(value) -> datetime | None:
    """HFS+ time: seconds since 1904-01-01."""
    n = _num(value)
    if n is None:
        return None
    try:
        return _check(_HFS_EPOCH + _delta_us(n, 1))
    except OverflowError:
        return None


def from_webkit(value) -> datetime | None:
    """Chromium/WebKit time: microseconds since 1601-01-01 UTC."""
    n = _num(value)
    if n is None:
        return None
    try:
        return _check(_FILETIME_EPOCH + _delta_us(n, 1_000_000))
    except OverflowError:
        return None


def from_systemtime(value: bytes) -> datetime | None:
    """Windows SYSTEMTIME struct (16 bytes: year, month, dow, day, hour, min, sec, ms).

    The struct carries no timezone. Windows writes NetworkList dates in local
    time, so callers must say which zone applies; this returns the wall-clock
    value tagged as UTC and the caller records that caveat.
    """
    if not isinstance(value, (bytes, bytearray)) or len(value) < 16:
        return None
    year, month, _dow, day, hour, minute, second, ms = struct.unpack_from("<8H", value)
    try:
        return _check(datetime(year, month, day, hour, minute, second, ms * 1000, tzinfo=UTC))
    except ValueError:
        return None


def from_datetime(value) -> datetime | None:
    """Normalise a datetime (e.g. from plistlib) to aware UTC."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return _check(value.astimezone(UTC))


def from_iso(value) -> datetime | None:
    """Parse an ISO-8601 string; naive values are assumed to be UTC."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return from_datetime(dt)


def iso(dt: datetime | None) -> str | None:
    """Format as ISO-8601 with microseconds and an explicit +00:00 offset."""
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec="microseconds")
