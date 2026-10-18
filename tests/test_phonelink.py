"""Phone Link parser on a synthetic profile built from documented schemas."""

import shutil
import sqlite3
from datetime import datetime, timezone

from connected_phone_forensics._synth.phonelink import build_phonelink_profile
from connected_phone_forensics.engine import scan
from connected_phone_forensics.model import IdKind
from connected_phone_forensics.output import write_all
from connected_phone_forensics.plugins.win_phonelink import recovered_rows
from connected_phone_forensics.readers.sqlite import open_db

UTC = timezone.utc
BASE = datetime(2025, 6, 14, 18, 0, tzinfo=UTC)
DEVICE_ID = "3fdf7366-e7b0-484a-94d9-82b1cd5de1aa"


def _case(tmp_path):
    root = tmp_path / "C"
    (root / "Windows" / "System32" / "config").mkdir(parents=True)
    (root / "Users" / "alex").mkdir(parents=True)
    (root / "Users" / "alex" / "NTUSER.DAT").write_bytes(b"regf")
    build_phonelink_profile(root / "Users", "alex", DEVICE_ID, phone_number="+14155550142",
                            contacts=[("Jordan Lee", "+1 415 555 0177"), ("Sam Ortiz", "+1 415 555 0123")],
                            base=BASE)
    return root


def test_phonelink_profile_parsed_and_attributed(tmp_path):
    _case(tmp_path)
    result = scan(tmp_path, plugin_ids=["win_phonelink"], export_dir=tmp_path / "exports")
    ev = {}
    for o in result.observations:
        ev.setdefault(o.event, []).append(o)
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
    # Everything is attributed to one phone, identified by the profile id and its own number.
    phones = [d for d in result.graph.devices if d.confirmed]
    assert len(phones) == 1
    kinds = {i.kind: i.value for i in phones[0].identifiers}
    assert kinds[IdKind.APP_DEVICE_ID] == f"phonelink:{DEVICE_ID}"
    assert kinds[IdKind.PHONE_NUMBER] == "+14155550142"
    assert phones[0].phone_os == "android"
    # Unknown database is reported, not skipped; RCS table is flagged as unparsed.
    cov = {c.source_path.rsplit("/", 1)[-1]: c for c in result.coverage}
    assert cov["deviceData.db"].status == "unrecognised" and "wallpaper" in cov["deviceData.db"].note
    assert any("rcs_chat" in w.message for w in result.warnings)
    # WAL and SHM files are hashed for the manifest.
    hashed = result.systems[0].fs.hashed_files()
    assert any(p.endswith("notifications.db-wal") for p in hashed)


def test_phonelink_report_renders(tmp_path):
    _case(tmp_path / "ev")
    result = scan(tmp_path / "ev", plugin_ids=["win_phonelink"])
    write_all(result, tmp_path / "out")
    html = (tmp_path / "out" / "report.html").read_text()
    assert "recovered: deleted" in html and "Jordan Lee" in html


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
