import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from connected_phone_forensics import timeutil
from connected_phone_forensics.correlate import build_graph
from connected_phone_forensics.evidence import EvidenceFS, discover_systems, display_path, long_path
from connected_phone_forensics.model import (
    Channel,
    Identifier,
    IdKind,
    Observation,
    Provenance,
    ident,
    normalize,
)
from connected_phone_forensics.readers.sqlite import open_db

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



def test_reads_evidence_deeper_than_windows_max_path(tmp_path):
    # Without LongPathsEnabled, Windows refuses paths over 260 characters. A Phone Link
    # database below a case folder is that deep; it must be found and read, not look missing.
    deep = long_path(tmp_path).joinpath("case", "C", "Users", "alex", *["x" * 60] * 5)
    deep.mkdir(parents=True)
    (deep / "phone.db").write_bytes(b"data")
    (long_path(tmp_path) / "case" / "C" / "Users" / "alex" / "NTUSER.DAT").write_bytes(b"regf")
    fs = EvidenceFS(tmp_path / "case" / "C")
    hit = fs.find("users/alex/**/phone.db")
    assert hit is not None and len(display_path(hit)) > 300
    assert fs.read_bytes(hit) == b"data"
    assert fs.sha256(hit) == hashlib.sha256(b"data").hexdigest()
    assert fs.rel(hit) == "Users/alex/" + "/".join(["x" * 60] * 5) + "/phone.db"
    assert display_path(fs.root) == str((tmp_path / "case" / "C").resolve())
    (sysroot,) = discover_systems(tmp_path / "case")
    assert [u.name for u in sysroot.users] == ["alex"]

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
    # Drive-letter roots are labelled with their parent folder when no computer name is recorded.
    assert kinds == [("macos", "macdata"), ("windows", "auto/C%3A"), ("windows", "kape_out/C")]
    win = next(s for s in systems if s.label == "kape_out/C")
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
    assert len(g.possible_matches) == 1
    m = g.possible_matches[0]
    assert m["independent_signals"] == 2
    assert sorted(s["kind"] for s in m["signals"]) == ["device_name", "phone_number"]


def test_weak_only_records_are_unconfirmed_and_unattributed_kept():
    obs = [_obs((IdKind.DEVICE_NAME, "Jane's iPhone"), channel=Channel.WIFI_HOTSPOT),
           _obs()]
    g = build_graph(obs)
    assert len(g.devices) == 1 and not g.devices[0].confirmed
    assert g.unattributed == [1]


def test_mac_heuristics_suggest_but_never_merge():
    obs = [
        _obs((IdKind.UDID, "U1"), (IdKind.WIFI_MAC, "a4:c3:37:5e:10:2c")),
        _obs((IdKind.BT_MAC, "a4:c3:37:5e:10:2b")),
        _obs((IdKind.APP_DEVICE_ID, "wifi_ap_mac:6ac3e15e102b")),
        _obs((IdKind.BT_MAC, "00:11:22:33:44:55")),
    ]
    g = build_graph(obs)
    assert len(g.devices) == 4
    kinds = sorted(s["kind"] for m in g.possible_matches for s in m["signals"])
    assert kinds == ["adjacent_mac", "mac_suffix"]  # hotspot shares its suffix with the BT MAC only


def test_examiner_assertion_merges_and_is_explained(tmp_path):
    import pytest as _pytest

    from connected_phone_forensics.demo import IPHONE_BT, IPHONE_UDID, build_demo_evidence
    from connected_phone_forensics.engine import parse_assertion, scan

    with _pytest.raises(ValueError):
        parse_assertion("device_name=Jane,udid=X")  # weak kinds cannot be asserted
    ev = build_demo_evidence(tmp_path)
    before = scan(ev)
    after = scan(ev, assume_same=[f"udid={IPHONE_UDID},bt_mac={IPHONE_BT}"])
    assert len(after.graph.devices) == len(before.graph.devices) - 1
    merged = next(d for d in after.graph.devices if any(r["artifact"] == "examiner_assertion"
                                                       for r in d.merge_reasons))
    assert {"usb", "bluetooth"} <= set(merged.channels)
    assert after.assertions == [[f"udid={IPHONE_UDID.replace('-', '')}", f"bt_mac={IPHONE_BT}"]]


def test_phone_numbers_match_across_formats_and_sim_is_one_signal():
    # "+1 (415) 555-0142" (iPhone backup) and "4155550142" (a national-format export) are one line;
    # a shared ICCID describes the same SIM, so together they are one independent signal, not two.
    obs = [
        _obs((IdKind.SERIAL, "S1"), (IdKind.PHONE_NUMBER, "+1 (415) 555-0142"), (IdKind.ICCID, "8901260123456789012")),
        _obs((IdKind.SERIAL, "S2"), (IdKind.PHONE_NUMBER, "4155550142"), (IdKind.ICCID, "89 0126 0123 4567 89012")),
    ]
    g = build_graph(obs)
    assert len(g.devices) == 2
    (m,) = g.possible_matches
    assert sorted(s["kind"] for s in m["signals"]) == ["iccid", "phone_number"]
    assert m["independent_signals"] == 1


def test_x509_utctime_pivot_and_offsets():
    from connected_phone_forensics.readers.x509 import _time

    # RFC 5280: YY >= 50 is 19YY, else 20YY. Python's %y pivots at 69 instead, so 50-68 differ.
    assert _time(0x17, b"500101000000Z").year == 1950
    assert _time(0x17, b"550101000000Z").year == 1955  # %y would say 2055
    assert _time(0x17, b"491231235959Z").year == 2049
    assert _time(0x18, b"20250101120000+0530") == datetime(2025, 1, 1, 6, 30, tzinfo=UTC)
    assert _time(0x17, b"garbage") is None


def test_csv_cells_cannot_become_formulas():
    from connected_phone_forensics.output.structured import csv_safe

    assert csv_safe("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert csv_safe("-2+3+cmd|' /C calc'!A0") == "'-2+3+cmd|' /C calc'!A0"
    assert csv_safe("+1 (415) 555-0142") == "+1 (415) 555-0142"  # phone numbers stay readable
    assert csv_safe("-5") == "-5" and csv_safe("Jane's iPhone") == "Jane's iPhone" and csv_safe(7) == 7


def test_unreadable_folder_is_reported_not_treated_as_empty(tmp_path, monkeypatch):
    # A live system, or an image mounted with permissions enforced: the registry folder cannot be
    # listed. Parsers that look there must say so instead of reporting no USB or Bluetooth history.
    import os

    from connected_phone_forensics.engine import scan

    root = tmp_path / "C"
    config = root / "Windows" / "System32" / "config"
    config.mkdir(parents=True)
    (config / "SYSTEM").write_bytes(b"regf")
    (root / "Users" / "alice").mkdir(parents=True)
    (root / "Users" / "alice" / "NTUSER.DAT").write_bytes(b"regf")
    real_scandir = os.scandir

    def scandir(path="."):
        if Path(path).name.lower() == "config":
            raise PermissionError(13, "Access is denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    result = scan(tmp_path, plugin_ids=["win_usb_phones", "win_bluetooth"])
    denied = [c for c in result.coverage if c.source_path == "Windows/System32/config"]
    assert {c.plugin for c in denied} == {"win_usb_phones", "win_bluetooth"}
    assert all(c.status == "error" and "could not be read" in c.note for c in denied)
    assert sum("could not be read" in w.message for w in result.warnings) == 2


def test_devices_without_a_name_get_a_readable_label():
    def obs(*ids, **kw):
        return Observation("a", "e", "m", Channel.USB, Provenance("p", "l", "a"), device_ids=list(ids), **kw)

    graph = build_graph([
        obs(ident(IdKind.UDID, "00008140-0001541E3E46801C")),
        obs(ident(IdKind.USB_SERIAL, "E28E9340"), phone_os="android", device_class="phone"),
        obs(ident(IdKind.APP_DEVICE_ID, "phonelink:f77b925a-6e5f-43e4-9baa-097c0b5d0711"), device_class="phone"),
    ])
    # Full identifiers, never shortened, and what the identifier tells (the UDID's chip: iPhone 16).
    assert sorted(d.label for d in graph.devices) == [
        "Android phone · USB serial E28E9340",
        "Phone · Phone Link device ID f77b925a-6e5f-43e4-9baa-097c0b5d0711",
        "iPhone 16 family · UDID 00008140-0001541E3E46801C"]
    udid_dev = next(d for d in graph.devices if d.label.startswith("iPhone"))
    decoded = udid_dev.to_dict()["identifiers"][0]["decoded"]
    assert decoded["summary"].startswith("Apple A18 / A18 Pro chip")
    assert ["ECID", "0x0001541E3E46801C (exclusive chip ID, unique per device)"] in decoded["fields"]
