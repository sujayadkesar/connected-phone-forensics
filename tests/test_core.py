import hashlib
import sqlite3
from datetime import datetime, timezone

import pytest

from tether import timeutil
from tether.correlate import build_graph
from tether.evidence import EvidenceFS, discover_systems
from tether.model import Channel, Identifier, IdKind, Observation, Provenance, ident, normalize
from tether.readers.sqlite import open_db

UTC = timezone.utc


# -- timestamps ---------------------------------------------------------------

def test_filetime_int_and_bytes():
    ft = 133_000_000_000_000_000
    dt = timeutil.from_filetime(ft)
    assert dt == datetime.fromtimestamp(ft / 10**7 - 11_644_473_600, UTC)  # 2022-06-18 04:26:40
    assert timeutil.from_filetime(ft.to_bytes(8, "little")) == dt


def test_large_integer_timestamps_are_exact():
    # 18-digit FILETIMEs and ns counts exceed float precision; conversion must be exact.
    assert timeutil.from_filetime(130_688_948_467_037_060).microsecond == 703706
    assert timeutil.from_unix(1_700_000_000_123_456_789, "ns").microsecond == 123456
    assert timeutil.from_cocoa(700_000_000_123_456_789).microsecond == 123456
    assert timeutil.from_unix("1700000000123", "ms").microsecond == 123000


def test_zero_and_garbage_timestamps_become_none():
    assert timeutil.from_filetime(0) is None
    assert timeutil.from_unix(0) is None
    assert timeutil.from_cocoa(None) is None
    assert timeutil.from_unix("not a number") is None
    assert timeutil.from_filetime(1) is None  # 1601 is not plausible evidence


def test_cocoa_seconds_and_nanoseconds():
    secs = 700_000_000
    assert timeutil.from_cocoa(secs) == datetime.fromtimestamp(978_307_200 + secs, UTC)
    assert timeutil.from_cocoa(secs * 1_000_000_000) == timeutil.from_cocoa(secs)


def test_unix_auto_units():
    s = 1_700_000_000
    assert timeutil.from_unix_auto(s) == timeutil.from_unix_auto(s * 1000) == timeutil.from_unix_auto(s * 10**6)


def test_systemtime():
    import struct
    raw = struct.pack("<8H", 2024, 5, 3, 17, 9, 30, 15, 250)
    assert timeutil.from_systemtime(raw) == datetime(2024, 5, 17, 9, 30, 15, 250000, tzinfo=UTC)


def test_webkit():
    us = 13_300_000_000_000_000
    assert timeutil.from_webkit(us) == datetime.fromtimestamp(us / 10**6 - 11_644_473_600, UTC)


# -- identifiers ---------------------------------------------------------------

def test_mac_normalisation():
    assert normalize(IdKind.BT_MAC, "AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize(IdKind.BT_MAC, "aabbccddeeff") == "aa:bb:cc:dd:ee:ff"


def test_udid_matches_usb_serial_form():
    a = Identifier(IdKind.UDID, "00008030-001A35E22E88802E")
    b = Identifier(IdKind.UDID, "00008030001a35e22e88802e")
    assert a == b


def test_ident_rejects_placeholders():
    assert ident(IdKind.SERIAL, "") is None
    assert ident(IdKind.SERIAL, None) is None
    assert ident(IdKind.BT_MAC, "00:00:00:00:00:00") is None
    assert ident(IdKind.SERIAL, "unknown") is None


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        Observation("x", "e", "m", Channel.USB, Provenance("p", "l", "x"), timestamp=datetime(2024, 1, 1))


# -- evidence filesystem ------------------------------------------------------------

def test_case_insensitive_glob(tmp_path):
    d = tmp_path / "Users" / "Alice" / "AppData" / "Local"
    d.mkdir(parents=True)
    (d / "Phone.DB").write_text("x")
    fs = EvidenceFS(tmp_path)
    assert fs.files("users/*/appdata/local/phone.db")[0].name == "Phone.DB"
    assert fs.files("**/phone.db")[0].name == "Phone.DB"
    assert fs.files("Users/*/AppData/Local/*.db")
    assert fs.files("Users/Bob/**/phone.db") == []


def _make_windows(root):
    (root / "Windows" / "System32" / "config").mkdir(parents=True)
    (root / "Users" / "alice").mkdir(parents=True)
    (root / "Users" / "alice" / "NTUSER.DAT").write_bytes(b"regf")
    (root / "Users" / "Public").mkdir()


def test_discover_kape_and_velociraptor_layouts(tmp_path):
    _make_windows(tmp_path / "kape_out" / "C")
    _make_windows(tmp_path / "velo" / "uploads" / "auto" / "C%3A")
    mac = tmp_path / "macdata"
    (mac / "Users" / "bob" / "Library").mkdir(parents=True)
    (mac / "Library" / "Preferences").mkdir(parents=True)
    (mac / "private" / "var" / "db").mkdir(parents=True)
    systems = discover_systems(tmp_path)
    kinds = sorted((s.os, s.label) for s in systems)
    assert kinds == [("macos", "macdata"), ("windows", "C"), ("windows", "C%3A")]
    win = next(s for s in systems if s.label == "C")
    assert [u.name for u in win.users] == ["alice"]  # Public skipped
    mac_sys = next(s for s in systems if s.os == "macos")
    assert [u.name for u in mac_sys.users] == ["bob"]


# -- sqlite safety ----------------------------------------------------------------

def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sqlite_reads_wal_without_touching_evidence(tmp_path):
    db = tmp_path / "evidence.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.commit()
    conn.execute("INSERT INTO t (v) VALUES ('only-in-wal')")
    conn.commit()
    # Copy the files while the writer still holds them, as an imager would.
    ev = tmp_path / "ev"
    ev.mkdir()
    for suffix in ("", "-wal", "-shm"):
        src = tmp_path / f"evidence.db{suffix}"
        if src.exists():
            (ev / src.name).write_bytes(src.read_bytes())
    conn.close()
    target = ev / "evidence.db"
    before = {p.name: _sha(p) for p in ev.iterdir()}
    assert (ev / "evidence.db-wal").stat().st_size > 0
    with open_db(target) as sdb:
        rows = [r["v"] for r in sdb.rows("SELECT v FROM t")]
        assert sdb.schema.has("t", "id", "v")
        assert list(sdb.select("t", ["v", "missing_col"]))[0]["missing_col"] is None
    assert rows == ["only-in-wal"]
    after = {p.name: _sha(p) for p in ev.iterdir()}
    assert before == after


# -- correlation ------------------------------------------------------------------

def _obs(*ids, artifact="a", ts=None, channel=Channel.USB, locator="r1"):
    return Observation(artifact, "evt", "msg", channel, Provenance("src", locator, artifact),
                       timestamp=ts, device_ids=[Identifier(k, v) for k, v in ids])


def test_strong_ids_merge_transitively_with_reasons():
    t1 = datetime(2024, 1, 1, tzinfo=UTC)
    t2 = datetime(2024, 2, 1, tzinfo=UTC)
    obs = [
        _obs((IdKind.UDID, "UDID1"), (IdKind.WIFI_MAC, "aa:aa:aa:aa:aa:aa"), artifact="lockdown", ts=t1),
        _obs((IdKind.WIFI_MAC, "aa:aa:aa:aa:aa:aa"), (IdKind.BT_MAC, "bb:bb:bb:bb:bb:bb"), artifact="x"),
        _obs((IdKind.BT_MAC, "bb:bb:bb:bb:bb:bb"), (IdKind.DEVICE_NAME, "Jane's iPhone"),
             artifact="bthport", ts=t2, channel=Channel.BLUETOOTH),
    ]
    g = build_graph(obs)
    assert len(g.devices) == 1
    d = g.devices[0]
    assert d.confirmed and d.label == "Jane's iPhone" and d.phone_os == "ios"
    assert d.first_seen == t1 and d.last_seen == t2
    assert len(d.merge_reasons) == 2
    assert set(d.channels) == {"usb", "bluetooth"}


def test_names_and_numbers_never_merge():
    obs = [
        _obs((IdKind.SERIAL, "S1"), (IdKind.DEVICE_NAME, "iPhone"), (IdKind.PHONE_NUMBER, "+1 555 0100")),
        _obs((IdKind.SERIAL, "S2"), (IdKind.DEVICE_NAME, "iPhone"), (IdKind.PHONE_NUMBER, "+15550100")),
    ]
    g = build_graph(obs)
    assert len(g.devices) == 2
    kinds = sorted(m["shared"]["kind"] for m in g.possible_matches)
    assert kinds == ["device_name", "phone_number"]


def test_weak_only_records_are_unconfirmed_and_unattributed_kept():
    obs = [_obs((IdKind.DEVICE_NAME, "Jane's iPhone"), channel=Channel.WIFI_HOTSPOT),
           _obs()]
    g = build_graph(obs)
    assert len(g.devices) == 1 and not g.devices[0].confirmed
    assert g.unattributed == [1]
