"""Windows timezone conversion, using StandardStart/DaylightStart bytes copied from real hives."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tether.wintz import WindowsTimezone


class _Key(dict):
    def get(self, k, default=None):
        return super().get(k, default)


EASTERN = _Key(TimeZoneKeyName="Eastern Standard Time", Bias=300, StandardBias=0, DaylightBias=4294967236,
               DaylightStart=bytes.fromhex("00000300020002000000000000000000"),
               StandardStart=bytes.fromhex("00000b00010002000000000000000000"))
EASTER_ISLAND = _Key(TimeZoneKeyName="Easter Island Standard Time", Bias=360, StandardBias=0,
                     DaylightBias=4294967236,
                     DaylightStart=bytes.fromhex("00000900010016000000000000000600"),
                     StandardStart=bytes.fromhex("00000400010016000000000000000600"))


@pytest.mark.parametrize("key,iana", [(EASTERN, "America/New_York"), (EASTER_ISLAND, "Pacific/Easter")])
def test_matches_iana(key, iana):
    tz = WindowsTimezone.from_key(key)
    zone = ZoneInfo(iana)
    for i in range(0, 365 * 24, 7):
        local = datetime(2023, 1, 1, 0, 30) + timedelta(hours=i)
        z = local.replace(tzinfo=zone)
        roundtrip = z.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if roundtrip != local or local.replace(fold=1, tzinfo=zone).utcoffset() != z.utcoffset():
            continue  # skip non-existent / ambiguous wall-clock times at transitions
        assert tz.to_utc(local) == z.astimezone(timezone.utc), local


def test_no_rules_means_fixed_bias():
    tz = WindowsTimezone.from_key(_Key(TimeZoneKeyName="India Standard Time", Bias=4294966966))  # -330
    assert tz.to_utc(datetime(2024, 6, 1, 12, 0)) == datetime(2024, 6, 1, 6, 30, tzinfo=timezone.utc)
