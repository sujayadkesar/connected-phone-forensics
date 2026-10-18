"""Phone Link parser on a synthetic profile built from documented schemas."""

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

from connected_phone_forensics._synth.phonelink import (
    RCS_DDL,
    build_phonelink_profile,
    write_device_metadata,
)
from connected_phone_forensics._synth.regf import HiveBuilder
from connected_phone_forensics.engine import scan
from connected_phone_forensics.model import IdKind
from connected_phone_forensics.output import write_all
from connected_phone_forensics.plugins.win_phonelink import recovered_rows
from connected_phone_forensics.readers.sqlite import open_db

UTC = timezone.utc
BASE = datetime(2025, 6, 14, 18, 0, tzinfo=UTC)
DEVICE_ID = "3fdf7366-e7b0-484a-94d9-82b1cd5de1aa"
PHONE = {"name": "Alex's Pixel", "manufacturer": "Google", "model": "Pixel 8a", "os_version": "15"}
PKG = ("AppData", "Local", "Packages", "Microsoft.YourPhone_8wekyb3d8bbwe", "LocalCache")


def _windows_root(tmp_path, computer_name="ALEX-LAPTOP"):
    root = tmp_path / "C"
    cfg = root / "Windows" / "System32" / "config"
    cfg.mkdir(parents=True)
    sy = HiveBuilder()
    sy.value("Select", "Current", 1)
    sy.value(r"ControlSet001\Control\ComputerName\ComputerName", "ComputerName", computer_name)
    (cfg / "SYSTEM").write_bytes(sy.build())
    (root / "Users" / "alex").mkdir(parents=True)
    (root / "Users" / "alex" / "NTUSER.DAT").write_bytes(b"regf")
    return root


def _case(tmp_path, device=PHONE):
    root = _windows_root(tmp_path)
    build_phonelink_profile(root / "Users", "alex", DEVICE_ID, phone_number="+14155550142",
                            contacts=[("Jordan Lee", "+1 415 555 0177"), ("Sam Ortiz", "+1 415 555 0123")],
                            base=BASE, device=device, computer_name="ALEX-LAPTOP")
    return root


def _events(result):
    ev = {}
    for o in result.observations:
        ev.setdefault(o.event, []).append(o)
    return ev


def _coverage(result):
    return {c.source_path.rsplit("/", 1)[-1]: c for c in result.coverage}


def test_phonelink_profile_parsed_and_attributed(tmp_path):
    _case(tmp_path)
    result = scan(tmp_path, plugin_ids=["win_phonelink"], export_dir=tmp_path / "exports")
    ev = _events(result)
    assert len(ev["contact"]) == 2
    assert [o.data["body"] for o in ev["sms_received"]] == ["Are we still on for tonight?"]
    sent = ev["sms_sent"][0]
    assert "Jordan Lee" in sent.message  # number resolved through contacts.db
    assert sent.timestamp == datetime(2025, 6, 14, 18, 2, tzinfo=UTC)
    # Deleted after the last checkpoint: recovered from the main database file.
    rec = ev["sms_received_recovered"][0]
    assert rec.data["body"] == "Bring the documents. Delete this." and rec.data["recovered"] == "deleted"
    assert "recovered_from=db" in rec.provenance.locator
    mms = ev["mms_received"][0]
    assert mms.data["text"] == "here it is" and mms.data["attachments"][0]["exported_path"]
    assert {o.data["type"] for o in ev["call_outgoing"] + ev["call_missed"]} == {"outgoing", "missed"}
    # Notifications: the WhatsApp one is live, the dismissed Signal OTP is recovered.
    assert [o.data["app"] for o in ev["notification"]] == ["WhatsApp"]
    otp = ev["notification_recovered"][0]
    assert otp.data["text"] == "Your code is 482913" and otp.data["recovered"] == "deleted"
    photo = ev["photo"][0]
    assert photo.data["export"]["column"] == "media"
    assert (tmp_path / "exports" / photo.data["export"]["exported_path"]).read_bytes().startswith(b"\x89PNG")
    assert len(ev["phone_app_inventory"][0].data["apps"]) == 3
    assert ev["phone_subscription"][0].data["name"] == "T-Mobile"
    # WAL and SHM files are hashed for the manifest.
    hashed = result.systems[0].fs.hashed_files()
    assert any(p.endswith("notifications.db-wal") for p in hashed)


def test_phonelink_rcs_messages_and_file_transfers(tmp_path):
    _case(tmp_path)
    result = scan(tmp_path, plugin_ids=["win_phonelink"], export_dir=tmp_path / "exports")
    ev = _events(result)
    got = ev["rcs_received"][0]
    assert got.data["body"] == "Sending the plans over RCS" and got.channel.value == "phone_link"
    assert "Jordan Lee" in got.message  # sender resolved through contacts.db
    assert got.timestamp == BASE + timedelta(seconds=1200)  # FILETIME, like SMS
    assert got.provenance.locator == "table=rcs_chat rowid=3001" and got.provenance.sha256
    # Thread info comes from rcs_conversation; fallback_id is the SMS/MMS thread of the same chat.
    assert got.data["thread_id"] == 30 and got.data["sms_mms_thread_id"] == 10
    assert got.data["own_number"] == "+14155550142"  # subscription_id -> subscription.number
    sent = ev["rcs_sent"][0]
    assert sent.data["to"] == ["+14155550177"] and "to Jordan Lee" in sent.message
    # Deleted after the last checkpoint: recovered like SMS.
    rec = ev["rcs_received_recovered"][0]
    assert rec.data["body"] == "Wipe the laptop tonight" and rec.data["recovered"] == "deleted"
    assert "table=rcs_chat rowid=3003 recovered_from=db" in rec.provenance.locator
    ft = ev["rcs_file_received"][0]
    att = ft.data["attachment"]
    assert att["name"] == "plans.jpg" and att["content_type"] == "image/jpeg"
    assert (tmp_path / "exports" / att["exported_path"]).read_bytes().startswith(b"\xff\xd8")
    assert ft.timestamp == BASE + timedelta(seconds=1210)
    assert ft.provenance.locator == "table=rcs_filetransfer rowid=3101"
    # Every RCS table present is parsed, so none is reported as skipped.
    assert not any("rcs_" in w.message for w in result.warnings)


def test_phonelink_device_metadata_labels_the_phone(tmp_path):
    _case(tmp_path)
    result = scan(tmp_path, plugin_ids=["win_phonelink"], export_dir=tmp_path / "exports")
    ev = _events(result)
    phones = [d for d in result.graph.devices if d.confirmed and d.device_class == "phone"]
    assert len(phones) == 1
    phone = phones[0]
    kinds = {i.kind: i.value for i in phone.identifiers}
    assert kinds[IdKind.APP_DEVICE_ID] == f"phonelink:{DEVICE_ID}"
    assert kinds[IdKind.PHONE_NUMBER] == "+14155550142"
    assert kinds[IdKind.DEVICE_NAME] == "Alex's Pixel" and kinds[IdKind.MODEL] == "Pixel 8a"
    assert kinds[IdKind.OS_VERSION] == "Android 15"
    assert phone.label == "Alex's Pixel" and phone.phone_os == "android"
    reg = [o for o in ev["phonelink_device_registered"] if o.device_ids]
    assert reg[0].timestamp == BASE - timedelta(days=20)  # ISO 8601 with offset
    assert reg[0].data["manufacturer"] == "Google" and reg[0].data["custom_data"]["androidSdkVersion"] == "35"
    assert reg[0].provenance.locator.startswith("json:$.DeviceMetadatas.") and reg[0].provenance.sha256
    seen = [o for o in ev["phonelink_device_last_seen"] if o.device_ids]
    assert seen[0].timestamp == BASE + timedelta(hours=2)
    # The computer's own entry is reported, but never becomes a phone.
    pc = [o for o in ev["phonelink_device_registered"] if not o.device_ids]
    assert len(pc) == 1 and pc[0].data["name_matches_this_computer"] is True
    assert "matches this computer's name" in pc[0].message
    # Every record of the profile carries the phone's name.
    assert all(any(i.kind == IdKind.DEVICE_NAME for i in o.device_ids) for o in ev["sms_received"])
    profile = ev["phonelink_profile"][0]
    assert "Alex's Pixel (Google Pixel 8a, Android 15)" in profile.message
    cov = _coverage(result)
    assert cov["DeviceMetadataStorage.json"].status == "parsed"


def test_phonelink_other_databases(tmp_path):
    _case(tmp_path)
    result = scan(tmp_path, plugin_ids=["win_phonelink"], export_dir=tmp_path / "exports")
    ev = _events(result)
    wall = ev["phone_wallpaper"][0]
    assert wall.data["wallpaper_type"] == 0 and wall.data["export"]["exported_path"].endswith(".jpg")
    assert (tmp_path / "exports" / wall.data["export"]["exported_path"]).read_bytes().startswith(b"\xff\xd8")
    cov = _coverage(result)
    assert cov["deviceData.db"].status == "parsed"
    # Known by their tables, but value formats are undocumented: unrecognised, with row counts.
    assert cov["continuity.db"].status == "unrecognised"
    assert "browser history" in cov["continuity.db"].note
    assert "browser_history(9 cols, 1 rows)" in cov["continuity.db"].note
    assert cov["sharedContent.db"].status == "unrecognised"
    assert "shareduri(10 cols, 0 rows)" in cov["sharedContent.db"].note


def test_phonelink_without_device_metadata(tmp_path):
    _case(tmp_path, device=None)
    result = scan(tmp_path, plugin_ids=["win_phonelink"])
    phones = [d for d in result.graph.devices if d.confirmed]
    assert len(phones) == 1 and phones[0].label == "Phone line +14155550142"
    assert "DeviceMetadataStorage.json" not in _coverage(result)


def test_phonelink_registered_phone_without_profile(tmp_path):
    root = _windows_root(tmp_path)
    write_device_metadata(root.joinpath("Users", "alex", *PKG), [{
        "id": "{8E1B6C0A-1F2D-4E3C-9A4B-5C6D7E8F9A0B}", "name": "Work phone", "os_name": "Android",
        "os_version": "14", "manufacturer": "Samsung", "model": "SM-A556B", "client_type": "LTW",
        "registered": BASE, "last_seen": BASE + timedelta(days=3)}])
    result = scan(tmp_path, plugin_ids=["win_phonelink"])
    phones = [d for d in result.graph.devices if d.device_class == "phone"]
    assert [p.label for p in phones] == ["Work phone"]
    kinds = {i.kind: i.value for i in phones[0].identifiers}
    assert kinds[IdKind.APP_DEVICE_ID] == "phonelink:8e1b6c0a-1f2d-4e3c-9a4b-5c6d7e8f9a0b"


def test_phonelink_unparsed_rcs_table_and_bad_metadata(tmp_path):
    root = _windows_root(tmp_path)
    cache = root.joinpath("Users", "alex", *PKG)
    dbdir = cache / "Indexed" / DEVICE_ID / "System" / "Database"
    dbdir.mkdir(parents=True)
    (cache / "DeviceMetadataStorage.json").write_text("{not json", encoding="utf-8")
    con = sqlite3.connect(dbdir / "phone.db")
    # rcs_reaction is invented: it stands for any RCS table a future Phone Link adds.
    con.executescript(RCS_DDL + "CREATE TABLE rcs_reaction(id INTEGER PRIMARY KEY, message_id INTEGER);")
    con.close()
    result = scan(tmp_path, plugin_ids=["win_phonelink"])
    rcs_warnings = [w.message for w in result.warnings if "RCS table" in w.message]
    assert len(rcs_warnings) == 1 and "rcs_reaction" in rcs_warnings[0]
    cov = _coverage(result)
    assert cov["DeviceMetadataStorage.json"].status == "unrecognised"
    assert cov["phone.db"].status == "empty"


def test_phonelink_report_renders(tmp_path):
    _case(tmp_path / "ev")
    result = scan(tmp_path / "ev", plugin_ids=["win_phonelink"])
    write_all(result, tmp_path / "out")
    html = (tmp_path / "out" / "report.html").read_text()
    assert "recovered: deleted" in html and "Jordan Lee" in html and "Alex&#x27;s Pixel" in html


def test_rows_of_another_table_on_reused_pages_are_not_recovered_as_sms(tmp_path):
    """Code review, October 2026: deleted conversation rows came back as 'recovered' SMS."""
    live = tmp_path / "phone.db"
    c = sqlite3.connect(live, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE message(message_id INTEGER, from_address TEXT, thread_id INTEGER, status INTEGER, "
              "type INTEGER, subject TEXT, body TEXT, timestamp INTEGER, tagged_body TEXT, pc_status INTEGER)")
    c.execute("CREATE TABLE conversation(thread_id INTEGER, recipient_list TEXT, timestamp INTEGER, "
              "msg_count INTEGER, unread_count INTEGER)")
    for i in range(1, 301):
        c.execute("INSERT INTO conversation VALUES (?,?,?,?,0)", (i, f"+1555{i:07d}", 132_000_000_000_000_000 + i, i))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("DELETE FROM conversation")  # the sync drops the threads ...
    for i in range(1, 251):                # ... and the messages that follow reuse their pages
        c.execute("INSERT INTO message VALUES (?,'+15550101',1,2,1,NULL,?,?,NULL,0)",
                  (i, f"message {i}", 133_000_000_000_000_000 + i))
    ev = tmp_path / "ev"
    ev.mkdir()
    for name in ("phone.db", "phone.db-wal"):
        shutil.copy(tmp_path / name, ev / name)
    c.close()
    warnings = []
    with open_db(ev / "phone.db") as db:
        messages = recovered_rows(db, ev / "phone.db", "message", lambda msg, path: warnings.append(msg))
        threads = recovered_rows(db, ev / "phone.db", "conversation", lambda msg, path: warnings.append(msg))
    assert messages == []  # every message is live; nothing was deleted or edited
    assert {status for status, _row, _v in threads} == {"deleted"}
    assert sorted(row["recipient_list"] for _s, row, _v in threads) == [f"+1555{i:07d}" for i in range(1, 301)]
    assert warnings == []
