"""macOS Continuity / sync parsers on synthetic data built from documented schemas."""

import sqlite3
from datetime import datetime, timezone

from connected_phone_forensics.engine import scan
from connected_phone_forensics.model import IdKind
from connected_phone_forensics.plugins.mac_messages import attributed_body_text

UTC = timezone.utc
COCOA = datetime(2001, 1, 1, tzinfo=UTC)


def cocoa(dt, ns=False):
    s = (dt - COCOA).total_seconds()
    return int(s * 1e9) if ns else s


def typedstream(text: str) -> bytes:
    """NSAttributedString typedstream as written by Messages (layout per imessage-exporter docs)."""
    raw = text.encode()
    length = bytes([len(raw)]) if len(raw) < 0x80 else b"\x81" + len(raw).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84"
            b"\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + raw +
            b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00")


def test_build_string_undoes_numeric_affinity():
    from connected_phone_forensics.plugins.mac_biome_sync import build_string

    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t(model NUMERIC)")
    for b in ("22E240", "23E5216", "21A329", "22F76"):
        c.execute("INSERT INTO t VALUES (?)", (b,))
    stored = [r[0] for r in c.execute("SELECT model FROM t")]
    assert isinstance(stored[0], float) and isinstance(stored[1], float)  # the trap
    # 23E5216 overflowed to infinity inside SQLite: lost, and reported as such rather than guessed.
    assert [build_string(v) for v in stored] == [("22E240", True), (None, True), ("21A329", False),
                                                  ("22F76", False)]



def test_build_string_survives_imprecise_sqlite_conversion():
    # Older SQLite builds (Python 3.10 on macOS) turn "22E240" into a REAL one bit off.
    import math

    from connected_phone_forensics.plugins.mac_biome_sync import build_string

    exact = float("22E240")
    for value in (exact, math.nextafter(exact, math.inf), math.nextafter(exact, 0)):
        assert build_string(value) == ("22E240", True)

def test_attributed_body():
    assert attributed_body_text(typedstream("Hello")) == "Hello"
    long = "x" * 300
    assert attributed_body_text(typedstream(long)) == long
    assert attributed_body_text(b"garbage") is None


def _db(path, script, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript(script)
    for sql, params in rows:
        c.execute(sql, params)
    c.commit()
    c.close()


def _mac(tmp_path):
    root = tmp_path / "mac"
    (root / "Library" / "Preferences").mkdir(parents=True)
    (root / "private" / "var" / "db").mkdir(parents=True)
    lib = root / "Users" / "jane" / "Library"
    _db(lib / "Biome" / "sync" / "sync.db", """
        CREATE TABLE DevicePeer(device_identifier TEXT, name TEXT, platform INTEGER, model NUMERIC, me INTEGER,
            last_sync_date INTEGER, ids_device_identifier TEXT);
        CREATE TABLE AtomMergedLog(synced_at REAL, event_created_at REAL, stream TEXT, merge_result INTEGER,
            owning_site_identifier TEXT, relayed_by_site_identifier TEXT, session_id TEXT, message_id TEXT);
        CREATE TABLE SyncMessageLog(timestamp REAL, device_identifier TEXT, session_id TEXT, message_id TEXT,
            reachable INTEGER, is_reciprocal INTEGER, atom_batch_bytes INTEGER);
    """, [
        ("INSERT INTO DevicePeer VALUES ('9F1C2A3B-0000-4000-8000-000000000001', '', 3, '25A354', 1, NULL, NULL)", ()),
        ("INSERT INTO DevicePeer VALUES ('5E7D8C9B-0000-4000-8000-000000000002', 'Jane''s iPhone', 2, '22E240', 0, ?, "
         "'6A5B4C3D-1111-2222-3333-444455556666')", (int(datetime(2025, 9, 1, 12, tzinfo=UTC).timestamp()),)),
        ("INSERT INTO AtomMergedLog VALUES (?, ?, 'App.InFocus', 3, '5E7D8C9B-0000-4000-8000-000000000002', NULL, "
         "'s1', 'm1')", (cocoa(datetime(2025, 8, 30, tzinfo=UTC)), cocoa(datetime(2025, 8, 30, tzinfo=UTC)))),
        ("INSERT INTO AtomMergedLog VALUES (?, ?, 'App.InFocus', 3, '5E7D8C9B-0000-4000-8000-000000000002', NULL, "
         "'s2', 'm2')", (cocoa(datetime(2025, 9, 1, tzinfo=UTC)), cocoa(datetime(2025, 9, 1, tzinfo=UTC)))),
        ("INSERT INTO SyncMessageLog VALUES (?, '5E7D8C9B-0000-4000-8000-000000000002', 's2', 'm2', 1, 0, 2048)",
         (cocoa(datetime(2025, 9, 1, 12, 0, 5, tzinfo=UTC)),)),
    ])
    _db(lib / "Messages" / "chat.db", """
        CREATE TABLE handle(ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
        CREATE TABLE message(ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB, handle_id INTEGER,
            service TEXT, account TEXT, date INTEGER, date_read INTEGER, date_delivered INTEGER, is_from_me INTEGER,
            destination_caller_id TEXT, service_center TEXT, cache_has_attachments INTEGER, date_edited INTEGER,
            date_retracted INTEGER);
    """, [
        ("INSERT INTO handle VALUES (1, '+14155550177', 'SMS')", ()),
        ("INSERT INTO handle VALUES (2, 'sam@icloud.com', 'iMessage')", ()),
        ("INSERT INTO message VALUES (1, 'G1', NULL, ?, 1, 'SMS', 'P:+14155550142', ?, 0, 0, 0, '+14155550142', NULL, 0, 0, 0)",
         (typedstream("Your package is at the door"), cocoa(datetime(2025, 9, 2, 9, tzinfo=UTC), ns=True))),
        ("INSERT INTO message VALUES (2, 'G2', 'see you', NULL, 2, 'iMessage', 'E:jane@icloud.com', ?, 0, 0, 1, "
         "'jane@icloud.com', NULL, 0, 0, 0)", (cocoa(datetime(2025, 9, 2, 10, tzinfo=UTC), ns=True),)),
    ])
    _db(lib / "Application Support" / "CallHistoryDB" / "CallHistory.storedata", """
        CREATE TABLE ZCALLRECORD(Z_PK INTEGER PRIMARY KEY, ZDATE TIMESTAMP, ZDURATION FLOAT, ZADDRESS BLOB,
            ZANSWERED INTEGER, ZORIGINATED INTEGER, ZCALLTYPE INTEGER, ZSERVICE_PROVIDER VARCHAR, ZNAME VARCHAR,
            ZISO_COUNTRY_CODE VARCHAR, ZUNIQUE_ID VARCHAR, ZREAD INTEGER);
    """, [("INSERT INTO ZCALLRECORD VALUES (1, ?, 62.5, ?, 1, 0, 1, 'com.apple.Telephony', 'Jordan Lee', 'us', 'U1', 1)",
           (cocoa(datetime(2025, 9, 3, 8, tzinfo=UTC)), b"+14155550177"))])
    _db(lib / "Preferences" / "com.apple.LaunchServices.QuarantineEventsV2", """
        CREATE TABLE LSQuarantineEvent(LSQuarantineEventIdentifier TEXT PRIMARY KEY NOT NULL,
            LSQuarantineTimeStamp REAL, LSQuarantineAgentBundleIdentifier TEXT, LSQuarantineAgentName TEXT,
            LSQuarantineDataURLString TEXT, LSQuarantineSenderName TEXT, LSQuarantineSenderAddress TEXT,
            LSQuarantineTypeNumber INTEGER, LSQuarantineOriginTitle TEXT, LSQuarantineOriginURLString TEXT,
            LSQuarantineOriginAlias BLOB);
    """, [
        ("INSERT INTO LSQuarantineEvent VALUES ('E1', ?, NULL, 'sharingd', 'file:///Users/jane/Downloads/IMG_0042.HEIC',"
         " 'Jane Doe', NULL, 0, NULL, NULL, NULL)", (cocoa(datetime(2025, 9, 4, 15, tzinfo=UTC)),)),
        ("INSERT INTO LSQuarantineEvent VALUES ('E2', ?, 'com.apple.Safari', 'Safari', 'https://x/y.zip', NULL, NULL,"
         " 0, NULL, NULL, NULL)", (cocoa(datetime(2025, 9, 4, 16, tzinfo=UTC)),)),
    ])
    return root


def test_macos_parsers(tmp_path):
    _mac(tmp_path)
    result = scan(tmp_path, plugin_ids=["mac_biome_sync", "mac_messages", "mac_callhistory", "mac_airdrop"])
    ev = {}
    for o in result.observations:
        ev.setdefault(o.event, []).append(o)
    peer = ev["biome_peer_last_sync"][0]
    assert peer.data["platform"] == "iPhone" and peer.data["os_build"] == "22E240"
    assert peer.device_class == "phone" and not any(o.data.get("is_this_mac") for o in ev["biome_peer_last_sync"])
    assert peer.data["streams_received"]["App.InFocus"]["events"] == 2
    assert {"biome_stream_first_merged", "biome_stream_last_merged"} <= set(ev)
    sms = ev["sms_received"][0]
    assert sms.data["text"] == "Your package is at the door"  # decoded from attributedBody
    assert sms.timestamp == datetime(2025, 9, 2, 9, tzinfo=UTC)
    assert any(i.kind == IdKind.PHONE_NUMBER and i.value == "+14155550142" for i in sms.device_ids)
    assert ev["imessage_sent"][0].channel.value == "account_sync"
    call = ev["call_incoming"][0]
    assert call.data["address"] == "+14155550177" and "iCloud" in call.data["caveat"]
    assert [o.data["sender_name"] for o in ev["airdrop_received"]] == ["Jane Doe"]
    biome_dev = next(d for d in result.graph.devices if any(
        i.kind == IdKind.APP_DEVICE_ID and i.value.startswith("biome_site:") for i in d.identifiers))
    assert biome_dev.label == "Jane's iPhone" and biome_dev.confirmed
