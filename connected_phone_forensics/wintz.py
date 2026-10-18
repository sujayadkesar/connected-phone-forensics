"""Windows local-time conversion using the evidence's own timezone settings.

Some Windows artifacts (NetworkList dates, setupapi logs) store wall-clock
local time. The SYSTEM hive's TimeZoneInformation key holds the bias and the
standard/daylight transition rules; this converts with those rules. Windows
"dynamic DST" history for past years is not applied: the current rule is
used for every year, and each converted record says so.
"""

from __future__ import annotations

import calendar
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def _signed(v: int | None) -> int:
    if v is None:
        return 0
    return v - (1 << 32) if v & 0x80000000 else v


@dataclass
class _Rule:
    month: int
    week: int        # 1..5, 5 = last
    dow: int         # 0 = Sunday
    hour: int
    minute: int

    @classmethod
    def from_time_fields(cls, raw) -> _Rule | None:
        """Decode StandardStart/DaylightStart.

        These values use the kernel TIME_FIELDS layout (Year, Month, Day, Hour,
        Minute, Second, Milliseconds, Weekday), not SYSTEMTIME: the weekday is
        the LAST field. Verified on real SYSTEM hives (Eastern, Pacific,
        Mountain and Easter Island zones) against the IANA database.
        """
        if not isinstance(raw, (bytes, bytearray)) or len(raw) < 16:
            return None
        _y, month, day, hour, minute, _s, _ms, dow = struct.unpack_from("<8H", raw)
        if month == 0:
            return None
        return cls(month, day, dow, hour, minute)

    def on(self, year: int) -> datetime:
        """Local wall-clock instant of this transition in a given year."""
        first_dow = (calendar.weekday(year, self.month, 1) + 1) % 7  # Python Mon=0 -> Sun=0
        day = 1 + (self.dow - first_dow) % 7 + (self.week - 1) * 7
        last = calendar.monthrange(year, self.month)[1]
        while day > last:
            day -= 7
        return datetime(year, self.month, day, self.hour, self.minute)


@dataclass
class WindowsTimezone:
    name: str
    bias: int                 # minutes: UTC = local + bias
    standard_bias: int
    daylight_bias: int
    standard_start: _Rule | None
    daylight_start: _Rule | None

    @classmethod
    def from_key(cls, key) -> WindowsTimezone:
        return cls(
            name=str(key.get("TimeZoneKeyName") or key.get("StandardName") or "unknown"),
            bias=_signed(key.get("Bias")),
            standard_bias=_signed(key.get("StandardBias")),
            daylight_bias=_signed(key.get("DaylightBias")),
            standard_start=_Rule.from_time_fields(key.get("StandardStart")),
            daylight_start=_Rule.from_time_fields(key.get("DaylightStart")),
        )

    def _is_dst(self, local: datetime) -> bool:
        if not (self.standard_start and self.daylight_start):
            return False
        d = self.daylight_start.on(local.year)
        s = self.standard_start.on(local.year)
        naive = local.replace(tzinfo=None)
        if d < s:  # northern hemisphere
            return d <= naive < s
        return not (s <= naive < d)  # southern hemisphere

    def to_utc(self, local: datetime) -> datetime:
        """Convert a naive (or UTC-tagged wall-clock) local time to UTC."""
        naive = local.replace(tzinfo=None)
        offset = self.bias + (self.daylight_bias if self._is_dst(naive) else self.standard_bias)
        return (naive + timedelta(minutes=offset)).replace(tzinfo=timezone.utc)

    def describe(self) -> str:
        return f"{self.name} (bias {self.bias:+d} min; current DST rule applied to all years)"
