"""Transaction-log replay: only logs that continue a dirty hive's sequence are applied.

Windows reuses .LOG1/.LOG2 without clearing them, so clean hives usually sit next
to logs full of already-flushed entries. Applying those rolls the registry back.
"""

import struct

import pytest

from tests.fixtures.regf import (
    REG_BINARY,
    REG_QWORD,
    HiveBuilder,
    hive_bins_entry,
    new_format_log,
    old_format_log,
    set_sequence,
)
from tether.engine import scan
from tether.readers.regf_log import APPLIED, CLEAN, NOT_APPLIED, marvin32, replay
from tether.readers.registry import open_hive
from tether.timeutil import from_filetime

DEV = r"ControlSet001\Services\BTHPORT\Parameters\Devices"
PHONE = f"\\{DEV}\\a4c337112233"
OLD, MID, NEW = 133_400_000_000_000_000, 133_450_000_000_000_000, 133_500_000_000_000_000


def _hive(last_connected: int, extra_devices: int = 0) -> bytes:
    h = HiveBuilder()
    h.value("Select", "Current", 1)
    h.value(f"{DEV}\\a4c337112233", "Name", b"Jane's iPhone\x00", REG_BINARY)
    h.value(f"{DEV}\\a4c337112233", "COD", 0x7A020C)
    h.value(f"{DEV}\\a4c337112233", "LastConnected", last_connected, REG_QWORD)
    for i in range(extra_devices):
        h.value(f"{DEV}\\0000000{i:05x}", "Name", f"Device {i}\x00".encode(), REG_BINARY)
    return h.build()


def _write(tmp_path, hive: bytes, **logs: bytes):
    cfg = tmp_path / "C" / "Windows" / "System32" / "config"
    cfg.mkdir(parents=True)
    (cfg / "SYSTEM").write_bytes(hive)
    for suffix, data in logs.items():
        (cfg / f"SYSTEM.{suffix}").write_bytes(data)
    users = tmp_path / "C" / "Users" / "jdoe"
    users.mkdir(parents=True)
    (users / "NTUSER.DAT").write_bytes(b"regf")
    return cfg / "SYSTEM"


def _last_connected(path):
    with open_hive(path) as h:
        return h.key(PHONE).get("LastConnected"), h.replay


def test_marvin32_reference_vectors():
    seed = 0x004FB61A001BDBCC  # .NET runtime Marvin test vectors
    assert marvin32(bytes.fromhex("af"), seed) == 0x48E73FC77D75DDC1
    assert marvin32(bytes.fromhex("e70f"), seed) == 0xB5F6E1FC485DBFF8
    assert marvin32(bytes.fromhex("37f495"), seed) == 0xF0B07C789B8CF7E8
    assert marvin32(bytes.fromhex("8642dc59"), seed) == 0x7008F2E87E9CF556


def test_clean_hive_ignores_stale_log_entries(tmp_path):
    # The review's case: a clean hive (10/10) and a LOG1 left over from before the last
    # flush (entry 9, the older page). The current value must win, and coverage says why.
    hive = set_sequence(_hive(NEW), 10, 10)
    log1 = new_format_log(hive, 9, [hive_bins_entry(9, _hive(OLD))])
    path = _write(tmp_path, hive, LOG1=log1)
    value, rep = _last_connected(path)
    assert value == NEW and rep.status == CLEAN
    result = scan(tmp_path, plugin_ids=["win_bluetooth"])
    obs = next(o for o in result.observations if o.event == "bt_last_connected")
    assert obs.timestamp == from_filetime(NEW)
    cov = next(c for c in result.coverage if c.source_path.endswith("SYSTEM.LOG1"))
    assert cov.status == "empty" and "hive is clean" in cov.note
    assert result.warnings == []


def test_dirty_hive_is_recovered(tmp_path):
    hive = set_sequence(_hive(OLD), 11, 10)
    path = _write(tmp_path, hive, LOG1=new_format_log(hive, 10, [hive_bins_entry(10, _hive(NEW))]))
    value, rep = _last_connected(path)
    assert value == NEW and rep.status == APPLIED and rep.logs[0].applied == 1


@pytest.mark.parametrize("swap", [False, True])
def test_entries_are_applied_in_sequence_across_log1_and_log2(tmp_path, swap):
    # Windows alternates between the two logs, so the earlier entries can be in either file.
    # regipy applies LOG2 then LOG1, which lets entry 10 overwrite the newer entry 11.
    hive = set_sequence(_hive(OLD), 12, 10)
    earlier = new_format_log(hive, 10, [hive_bins_entry(10, _hive(MID))])
    later = new_format_log(hive, 11, [hive_bins_entry(11, _hive(NEW))])
    logs = {"LOG1": later, "LOG2": earlier} if swap else {"LOG1": earlier, "LOG2": later}
    value, rep = _last_connected(_write(tmp_path, hive, **logs))
    assert value == NEW
    assert rep.summary == "applied transaction-log entries 10-11 from " + (
        "SYSTEM.LOG2, SYSTEM.LOG1" if swap else "SYSTEM.LOG1, SYSTEM.LOG2")


def test_entries_older_than_the_hive_and_stale_tails_are_not_applied(tmp_path):
    hive = set_sequence(_hive(OLD), 11, 10)
    # LOG1: the current entry 10, then a stale entry 4 from an earlier cycle at the tail.
    log1 = new_format_log(hive, 10, [hive_bins_entry(10, _hive(NEW)), hive_bins_entry(4, _hive(MID))])
    # LOG2: entries from before the hive's last write.
    log2 = new_format_log(hive, 7, [hive_bins_entry(7, _hive(MID))])
    value, rep = _last_connected(_write(tmp_path, hive, LOG1=log1, LOG2=log2))
    assert value == NEW
    assert rep.logs[0].note == "applied log entries 10"
    assert rep.logs[1].note == "not applied: its entries start at sequence 7, before the hive's 10"


def test_damaged_entry_stops_replay_with_a_warning(tmp_path):
    hive = set_sequence(_hive(OLD), 12, 10)
    log1 = bytearray(new_format_log(hive, 10, [hive_bins_entry(10, _hive(MID)), hive_bins_entry(11, _hive(NEW))]))
    log1[-100] ^= 0xFF  # corrupt entry 11's pages: its data hash no longer matches
    _write(tmp_path, hive, LOG1=bytes(log1))
    result = scan(tmp_path, plugin_ids=["win_bluetooth"])
    obs = next(o for o in result.observations if o.event == "bt_last_connected")
    assert obs.timestamp == from_filetime(MID)  # entry 10 only
    assert any("log entry 11 is damaged" in w.message for w in result.warnings)
    cov = next(c for c in result.coverage if c.source_path.endswith("SYSTEM.LOG1"))
    assert cov.status == "parsed" and cov.records == 1 and "bad data hash" in cov.note


def test_dirty_hive_without_applicable_entries_warns(tmp_path):
    hive = set_sequence(_hive(OLD), 11, 10)
    _write(tmp_path, hive, LOG1=new_format_log(hive, 9, [hive_bins_entry(9, _hive(MID))]))
    result = scan(tmp_path, plugin_ids=["win_bluetooth"])
    obs = next(o for o in result.observations if o.event == "bt_last_connected")
    assert obs.timestamp == from_filetime(OLD)  # the primary as it is
    assert any("hive is dirty (sequence 11/10)" in w.message for w in result.warnings)


def test_log_entry_that_grows_the_hive(tmp_path):
    # A write that added hive bins: the entry's hive bins data size exceeds the primary file.
    small, big = _hive(OLD), _hive(NEW, extra_devices=300)
    assert len(big) > len(small)
    hive = set_sequence(small, 11, 10)
    path = _write(tmp_path, hive, LOG1=new_format_log(hive, 10, [hive_bins_entry(10, big)]))
    with open_hive(path) as h:
        assert h.key(PHONE).get("LastConnected") == NEW
        assert len(list(h.subkeys(f"\\{DEV}"))) == 301
        assert h.replay.status == APPLIED


def test_old_format_log_needs_a_matching_timestamp(tmp_path):
    hive = set_sequence(_hive(OLD), 11, 10)
    after = _hive(NEW)
    pages = list(range((len(after) - 4096) // 512))
    stamp = struct.unpack_from("<Q", hive, 12)[0]
    path = _write(tmp_path / "match", hive, LOG=old_format_log(after, 11, pages))
    value, rep = _last_connected(path)
    assert value == NEW and rep.status == APPLIED and rep.logs[0].format == "old"
    path = _write(tmp_path / "other", hive, LOG=old_format_log(after, 11, pages, timestamp=stamp + 1))
    value, rep = _last_connected(path)
    assert value == OLD and rep.status == NOT_APPLIED and rep.warnings


def test_old_format_log_is_ignored_next_to_a_new_format_log():
    # An upgraded system can keep a pre-8.1 SYSTEM.LOG; only the new-format logs count.
    hive = set_sequence(_hive(OLD), 11, 10)
    after = _hive(MID)
    old = old_format_log(after, 11, list(range((len(after) - 4096) // 512)))
    new = new_format_log(hive, 10, [hive_bins_entry(10, _hive(NEW))])
    rep = replay(hive, [("SYSTEM.LOG", old), ("SYSTEM.LOG1", new)])
    assert rep.status == APPLIED and rep.logs[0].applied == 0 and rep.logs[1].applied == 1
    assert "ignored next to a new-format log" in rep.logs[0].note


def test_damaged_base_block_uses_only_the_log_with_the_latest_entries():
    hive = bytearray(set_sequence(_hive(OLD), 12, 10))
    hive[508] ^= 0xFF  # wrong checksum: the base block itself is untrustworthy
    earlier = new_format_log(bytes(hive), 10, [hive_bins_entry(10, _hive(MID))])
    later = new_format_log(bytes(hive), 11, [hive_bins_entry(11, _hive(NEW))])
    rep = replay(bytes(hive), [("SYSTEM.LOG1", earlier), ("SYSTEM.LOG2", later)])
    assert rep.status == APPLIED and [lg.applied for lg in rep.logs] == [0, 1]
    assert "base block is damaged" in rep.logs[0].note
    assert rep.data[:4] == b"regf" and struct.unpack_from("<II", rep.data, 4) == (11, 11)
