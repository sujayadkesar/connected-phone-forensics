"""Synthetic Phone Link profile built from publicly documented schemas.

Table layouts follow the SQLECmd maps (Windows_YourPhone_*.smap), kacos2000's
Win10/YourPhone queries, YPA and psst's notifications/phone_apps DDL. The RCS
tables, deviceData.db, continuity.db, sharedContent.db and
DeviceMetadataStorage.json copy the DDL and key layout of a live Phone Link
1.26 install (Android phone, Windows 11, 2026); no values were copied. Rows
are invented. Some rows are deleted or updated after a checkpoint so they
exist only in the main file or in older WAL frames, as on a live system.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
JPEG_STUB = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

# DDL as found in phone.db of Phone Link 1.26 (RCS tables).
RCS_DDL = """
    CREATE TABLE "rcs_chat"(
        "message_id" INTEGER PRIMARY KEY NOT NULL, "thread_id" INTEGER NOT NULL, "content_type" TEXT NOT NULL,
        "status" INTEGER NOT NULL, "from_address" TEXT NOT NULL, "type" INTEGER NOT NULL, "body" TEXT NOT NULL,
        "timestamp" INTEGER NOT NULL, "pc_status" INTEGER NOT NULL, "subscription_id" INTEGER NOT NULL DEFAULT -1,
        "message_type" INTEGER NOT NULL, "session_id" TEXT NOT NULL,
        "is_user_action_required" INTEGER NOT NULL DEFAULT 0, "checksum" INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE "rcs_conversation"(
        "thread_id" INTEGER PRIMARY KEY NOT NULL, "fallback_id" INTEGER NOT NULL DEFAULT -1,
        "recipient_list" TEXT NOT NULL, "timestamp" INTEGER NOT NULL DEFAULT 0, "msg_count" INTEGER NOT NULL DEFAULT 0,
        "unread_count" INTEGER NOT NULL DEFAULT 0, "type" INTEGER NOT NULL DEFAULT 0,
        "checksum" INTEGER NOT NULL DEFAULT 0, "phone_unread_count" INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE "rcs_filetransfer"(
        "message_id" INTEGER PRIMARY KEY NOT NULL, "thread_id" INTEGER NOT NULL, "content_type" TEXT NOT NULL,
        "status" INTEGER NOT NULL, "from_address" TEXT NOT NULL, "type" INTEGER NOT NULL,
        "timestamp" INTEGER NOT NULL, "pc_status" INTEGER NOT NULL, "subscription_id" INTEGER NOT NULL DEFAULT -1,
        "message_type" INTEGER NOT NULL, "session_id" TEXT NOT NULL, "name" TEXT NOT NULL, "size" INTEGER NOT NULL,
        "blob" BLOB, "is_user_action_required" INTEGER NOT NULL DEFAULT 0, "checksum" INTEGER NOT NULL DEFAULT 0);
"""
_SYNC_DDL = """
    CREATE TABLE _change_notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL,
        operation INTEGER NOT NULL, row_id INTEGER NOT NULL, sender_id TEXT NOT NULL);
    CREATE TABLE "content_sequence"("content_sequence_id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
        "content_type" TEXT UNIQUE NOT NULL, "sequence" INTEGER NOT NULL DEFAULT -1,
        "expected" INTEGER NOT NULL DEFAULT -1);
"""
CONTINUITY_DDL = """
    CREATE TABLE "browser_history"("browser_history_id" INTEGER PRIMARY KEY AUTOINCREMENT,
        "package_name" TEXT NOT NULL, "browser_history_title" TEXT NOT NULL, "checksum" INTEGER NOT NULL DEFAULT 0,
        "browser_history_rank" INTEGER NOT NULL DEFAULT 0, "task_id" INTEGER NOT NULL, "favicon" BLOB,
        "web_uri" TEXT, "source" TEXT);
    CREATE TABLE "content_sequence"("content_sequence_id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
        "content_type" TEXT UNIQUE NOT NULL, "sequence" INTEGER NOT NULL DEFAULT -1,
        "expected" INTEGER NOT NULL DEFAULT -1);
    CREATE TABLE "continue_app_context"("id" INTEGER PRIMARY KEY AUTOINCREMENT, "context_id" TEXT UNIQUE NOT NULL,
        "task_id" INTEGER NOT NULL DEFAULT 0, "type" TEXT NOT NULL, "team_id" TEXT, "intent_uri" TEXT,
        "app_id" TEXT NOT NULL, "title" TEXT, "web_link" TEXT, "preview" BLOB, "extras" TEXT,
        "create_time" INTEGER NOT NULL DEFAULT 0, "last_updated_time" INTEGER NOT NULL DEFAULT 0,
        "life_time" INTEGER NOT NULL DEFAULT -1);
"""
SHARED_CONTENT_DDL = """
    CREATE TABLE "content_sequence"("content_sequence_id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
        "content_type" TEXT UNIQUE NOT NULL, "sequence" INTEGER NOT NULL DEFAULT -1,
        "expected" INTEGER NOT NULL DEFAULT -1);
    CREATE TABLE "sharedcontent"("data_id" INTEGER PRIMARY KEY, "group_id" INTEGER NOT NULL,
        "item_id" INTEGER NOT NULL, "mime_type" TEXT NOT NULL, "size" INTEGER NOT NULL DEFAULT 0,
        "timestamp" INTEGER NOT NULL, "data" BLOB, "alt_data" BLOB, "name" TEXT,
        "custom_type" INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE "shareduri"("uri_id" INTEGER PRIMARY KEY AUTOINCREMENT, "original_uri" TEXT NOT NULL,
        "shared_time" INTEGER NOT NULL, "type" INTEGER NOT NULL DEFAULT 0, "is_deleted" INTEGER NOT NULL DEFAULT 0,
        "uri" TEXT, "title" TEXT, "description" TEXT, "image" BLOB, "icon" BLOB);
    CREATE INDEX "shared_timestamp_idx" ON "shareduri"("shared_time");
"""


def ft(dt: datetime) -> int:
    return int((dt - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 10_000_000)


class _LiveDB:
    """A WAL-mode database whose files are copied while the WAL is still live."""

    def __init__(self, name: str):
        self.tmp = Path(tempfile.mkdtemp(prefix="cpf-synth-"))
        self.path = self.tmp / name
        self.c = sqlite3.connect(self.path, isolation_level=None)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA wal_autocheckpoint=0")

    def checkpoint(self) -> None:
        self.c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def save(self, dest_dir: Path) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            src = self.path.with_name(self.path.name + suffix)
            if src.exists():
                shutil.copyfile(src, dest_dir / src.name)
        self.c.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def write_device_metadata(local_cache: Path, entries: list[dict]) -> Path:
    """DeviceMetadataStorage.json with the key layout seen on Phone Link 1.26.

    Each entry: id, name, os_name, os_version, manufacturer, model, client_type,
    registered and last_seen (datetimes), optional model_version and custom (dict).
    """
    out = []
    for e in entries:
        out.append({
            "Certificates": {"SelfSigned": ["MIIBsynthetic0000certificate0000placeholder=="]},
            "IsLinked": True,
            "Metadata": {
                "Id": e["id"], "DistinguishedDeviceId": e.get("ddid", "a:0123456789abcdef"),
                "RegitrationTime": e["registered"].isoformat(), "LastSeenTime": e["last_seen"].isoformat(),
                "Metadata": {
                    "ClientType": e["client_type"], "ClientVersion": e.get("client_version", "1.25081.111.0"),
                    "DisplayName": e["name"], "OsName": e["os_name"], "OsVersion": e["os_version"],
                    "Capabilities": ["CLIPBOARD", "FILE_TRANSFER", "DEVICE_STATUS"],
                    "Manufacture": e["manufacturer"], "ModelName": e["model"],
                    "ModelVersion": e.get("model_version", ""), "ConsentVersion": 4, "IsEnabled": True,
                    "CustomData": json.dumps(e["custom"]) if e.get("custom") else "",
                },
            },
        })
    local_cache.mkdir(parents=True, exist_ok=True)
    path = local_cache / "DeviceMetadataStorage.json"
    path.write_text(json.dumps({"ConfigVersion": 1, "DeviceMetadatas": {"00A1B2C3D4E5F607": out}}),
                    encoding="utf-8")
    return path


def build_phonelink_profile(users_dir: Path, user: str, device_id: str, *, phone_number: str,
                            contacts: list[tuple[str, str]], base: datetime,
                            device: dict | None = None, computer_name: str | None = None) -> Path:
    """Create Users/<user>/AppData/.../Indexed/<device_id>/System/Database with documented schemas.

    With ``device`` (name, manufacturer, model, os_version), also write
    DeviceMetadataStorage.json describing the phone and, with ``computer_name``,
    the computer itself.
    """
    local_cache = (users_dir / user / "AppData" / "Local" / "Packages" / "Microsoft.YourPhone_8wekyb3d8bbwe" /
                   "LocalCache")
    dbdir = local_cache / "Indexed" / device_id / "System" / "Database"
    if device is not None:
        entries = []
        if computer_name:
            entries.append({"id": "0c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f", "name": computer_name,
                            "os_name": "Windows", "os_version": "10.0.26100", "manufacturer": "Contoso",
                            "model": "Laptop 7", "client_type": "WEA", "registered": base - timedelta(days=30),
                            "last_seen": base + timedelta(days=1)})
        entries.append({"id": device_id, "name": device["name"], "os_name": "Android",
                        "os_version": device["os_version"], "manufacturer": device["manufacturer"],
                        "model": device["model"], "model_version": "AP3A.241105.008", "client_type": "LTW",
                        "registered": base - timedelta(days=20), "last_seen": base + timedelta(hours=2),
                        "custom": {"androidSdkVersion": "35", "defaultLocale": "en"}})
        write_device_metadata(local_cache, entries)

    # contacts.db
    d = _LiveDB("contacts.db")
    d.c.executescript("""
        CREATE TABLE contact(contact_id INTEGER PRIMARY KEY, display_name TEXT, nickname TEXT,
            last_updated_time INTEGER, company TEXT, job_title TEXT, notes TEXT, name_prefix TEXT,
            given_name TEXT, middle_name TEXT, family_name TEXT, name_suffix TEXT, thumbnail BLOB);
        CREATE TABLE phonenumber(contact_id INTEGER, phone_number TEXT, display_phone_number TEXT,
            phone_number_type INTEGER, label TEXT);
        CREATE TABLE emailaddress(contact_id INTEGER, type INTEGER, label TEXT, address TEXT);
        CREATE TABLE contactdate(contact_id INTEGER, date_type INTEGER, label TEXT, display_date TEXT);
        CREATE TABLE contacturl(contact_id INTEGER, type INTEGER, label TEXT, url_address TEXT);
        CREATE TABLE postaladdress(contact_id INTEGER, type INTEGER, label TEXT, street TEXT, city TEXT,
            region TEXT, postal_code TEXT, country_code TEXT, display_address TEXT);
    """)
    for i, (name, number) in enumerate(contacts, 1):
        d.c.execute("INSERT INTO contact(contact_id, display_name, last_updated_time) VALUES (?,?,?)",
                    (i, name, ft(base)))
        d.c.execute("INSERT INTO phonenumber VALUES (?,?,?,2,NULL)", (i, number.replace(" ", ""), number))
    d.save(dbdir)

    # phone.db
    d = _LiveDB("phone.db")
    d.c.executescript("""
        CREATE TABLE message(message_id INTEGER, from_address TEXT, thread_id INTEGER, status INTEGER,
            type INTEGER, subject TEXT, body TEXT, timestamp INTEGER, tagged_body TEXT, pc_status INTEGER);
        CREATE TABLE message_to_address(message_id INTEGER, address TEXT);
        CREATE TABLE conversation(thread_id INTEGER, recipient_list TEXT, timestamp INTEGER,
            msg_count INTEGER, unread_count INTEGER);
        CREATE TABLE mms(message_id INTEGER, thread_id INTEGER, status INTEGER, type INTEGER,
            subscription_id INTEGER, subject TEXT, charset INTEGER, timestamp INTEGER, from_address TEXT,
            pc_status INTEGER);
        CREATE TABLE mms_part(part_id INTEGER, message_id INTEGER, sequence_num INTEGER, content_id TEXT,
            content_type TEXT, text TEXT, name TEXT, charset INTEGER, blob BLOB);
        CREATE TABLE sync(sync_timestamp INTEGER, table_data_id INTEGER, last_updated_time INTEGER);
        CREATE TABLE subscription(subscription_id INTEGER, sim_slot_index INTEGER, country_iso TEXT,
            name TEXT, is_roaming INTEGER, number TEXT, is_mms_enabled INTEGER,
            is_default_sms_subscription INTEGER);
    """ + RCS_DDL)
    d.c.execute("INSERT INTO subscription VALUES (1, 0, 'us', 'T-Mobile', 0, ?, 1, 1)", (phone_number,))
    friend, friend_no = contacts[0]
    d.c.execute("INSERT INTO conversation VALUES (10, ?, ?, 3, 0)", (friend_no.replace(" ", ""), ft(base)))
    msgs = [
        (1001, friend_no.replace(" ", ""), 10, 1, "Are we still on for tonight?", 60),
        (1002, None, 10, 2, "Yes, 8pm at the usual place", 120),
        (1003, friend_no.replace(" ", ""), 10, 1, "Bring the documents. Delete this.", 600),
    ]
    for mid, frm, thread, typ, body, offset in msgs:
        d.c.execute("INSERT INTO message VALUES (?,?,?,2,?,NULL,?,?,NULL,0)",
                    (mid, frm, thread, typ, body, ft(base) + offset * 10_000_000))
    d.c.execute("INSERT INTO message_to_address VALUES (1002, ?)", (friend_no.replace(" ", ""),))
    d.c.execute("INSERT INTO mms VALUES (2001, 10, 2, 1, 1, NULL, 106, ?, ?, 0)",
                (ft(base) + 900 * 10_000_000, friend_no.replace(" ", "")))
    d.c.execute("INSERT INTO mms_part VALUES (1, 2001, 0, NULL, 'text/plain', 'here it is', NULL, 106, NULL)")
    d.c.execute("INSERT INTO mms_part VALUES (2, 2001, 1, 'img', 'image/png', NULL, 'IMG_0042.png', 0, ?)",
                (PNG_1x1,))
    # RCS thread 30 falls back to SMS/MMS thread 10 with the same friend.
    d.c.execute("INSERT INTO rcs_conversation VALUES (30, 10, ?, ?, 3, 0, 0, 0, 0)",
                (friend_no.replace(" ", ""), ft(base) + 1300 * 10_000_000))
    rcs = [
        (3001, friend_no.replace(" ", ""), 1, "Sending the plans over RCS", 1200),
        (3002, "", 2, "Got them, thanks", 1260),
        (3003, friend_no.replace(" ", ""), 1, "Wipe the laptop tonight", 1300),
    ]
    for mid, frm, typ, body, offset in rcs:
        d.c.execute("INSERT INTO rcs_chat VALUES (?, 30, 'text/plain', 2, ?, ?, ?, ?, 2, 1, 0, 'session-30', 0, 0)",
                    (mid, frm, typ, body, ft(base) + offset * 10_000_000))
    d.c.execute("INSERT INTO rcs_filetransfer VALUES (3101, 30, 'image/jpeg', 2, ?, 1, ?, 2, 1, 0, 'session-30', "
                "'plans.jpg', ?, ?, 0, 0)",
                (friend_no.replace(" ", ""), ft(base) + 1210 * 10_000_000, len(JPEG_STUB), JPEG_STUB))
    d.checkpoint()
    d.c.execute("DELETE FROM message WHERE message_id=1003")  # deleted after the checkpoint
    d.c.execute("DELETE FROM rcs_chat WHERE message_id=3003")
    d.save(dbdir)

    # calling.db
    d = _LiveDB("calling.db")
    d.c.execute("CREATE TABLE call_history(call_id INTEGER PRIMARY KEY, phone_number TEXT, duration INTEGER, "
                "call_type INTEGER, start_time INTEGER, is_read INTEGER, last_updated_time INTEGER, "
                "phone_account_id TEXT)")
    d.c.execute("INSERT INTO call_history VALUES (1, ?, 184, 2, ?, 1, ?, '1')",
                (friend_no.replace(" ", ""), ft(base) + 1800 * 10_000_000, ft(base) + 1984 * 10_000_000))
    d.c.execute("INSERT INTO call_history VALUES (2, '+14155550111', 0, 3, ?, 0, ?, '1')",
                (ft(base) + 3600 * 10_000_000, ft(base) + 3600 * 10_000_000))
    d.save(dbdir)

    # notifications.db (psst DDL)
    d = _LiveDB("notifications.db")
    d.c.execute('CREATE TABLE "notifications"("id" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, '
                '"notification_id" TEXT NOT NULL UNIQUE, "package_name" TEXT NOT NULL, "json" TEXT NOT NULL, '
                '"post_time" INTEGER NOT NULL, "state" INTEGER NOT NULL DEFAULT 0, "anonymous_id" TEXT)')

    def note(key, pkg, app, title, text, when):
        ms = int(when.timestamp() * 1000)
        return json.dumps({"key": key, "packageName": pkg, "appName": app, "title": title, "text": text,
                           "bigText": text, "postTime": ms, "timestamp": ms, "flags": 17, "platform": "android"})

    t1 = datetime.fromtimestamp(base.timestamp() + 300, UTC)
    t2 = datetime.fromtimestamp(base.timestamp() + 420, UTC)
    for key, pkg, app, title, text, when in (
        ("0|com.whatsapp|1|k1", "com.whatsapp", "WhatsApp", friend, "Running 10 min late", t1),
        ("0|org.thoughtcrime.securesms|2|k2", "org.thoughtcrime.securesms", "Signal", "Unknown",
         "Your code is 482913", t2),
    ):
        d.c.execute("INSERT INTO notifications(notification_id, package_name, json, post_time) VALUES (?,?,?,?)",
                    (key, pkg, note(key, pkg, app, title, text, when), ft(when)))
    d.checkpoint()
    d.c.execute("DELETE FROM notifications WHERE notification_id LIKE '%securesms%'")  # dismissed
    d.save(dbdir)

    # photos.db
    d = _LiveDB("photos.db")
    d.c.execute("CREATE TABLE media(id INTEGER PRIMARY KEY, name TEXT, mime_type TEXT, last_updated_time INTEGER, "
                "taken_time INTEGER, last_seen_time INTEGER, height INTEGER, width INTEGER, orientation INTEGER, "
                "size INTEGER, uri TEXT, thumbnail BLOB, media BLOB)")
    d.c.execute("CREATE TABLE photo(photo_id INTEGER PRIMARY KEY, name TEXT, last_updated_time INTEGER, "
                "size INTEGER, uri TEXT, thumbnail BLOB, blob BLOB)")
    d.c.execute("INSERT INTO media VALUES (1, 'PXL_20250614_083012345.jpg', 'image/jpeg', ?, ?, ?, 1, 1, 0, ?, "
                "'content://media/external/images/media/88', ?, ?)",
                (ft(base), ft(base) - 600 * 10_000_000, ft(base), len(PNG_1x1), PNG_1x1, PNG_1x1))
    d.save(dbdir)

    # phoneapps.db (psst DDL) and an undocumented deviceData.db
    d = _LiveDB("phoneapps.db")
    d.c.execute('CREATE TABLE "phone_apps"("app_id" INTEGER PRIMARY KEY AUTOINCREMENT, "app_name" TEXT NOT NULL, '
                '"package_name" TEXT NOT NULL UNIQUE, "version" TEXT NOT NULL, "etag" INTEGER NOT NULL DEFAULT 0, '
                '"favorite_rank" INTEGER NOT NULL DEFAULT -1, "blob" BLOB, "last_updated_time" INTEGER NOT NULL '
                'DEFAULT 0, "checksum" INTEGER NOT NULL DEFAULT 0, "phoneapp_web_url" TEXT)')
    for name, pkg, ver in (("WhatsApp", "com.whatsapp", "2.25.18.75"),
                           ("Signal", "org.thoughtcrime.securesms", "7.48.2"),
                           ("Proton VPN", "ch.protonvpn.android", "5.11.62.0")):
        d.c.execute("INSERT INTO phone_apps(app_name, package_name, version, blob) VALUES (?,?,?,?)",
                    (name, pkg, ver, PNG_1x1))
    d.save(dbdir)

    # deviceData.db, continuity.db and sharedContent.db (DDL of Phone Link 1.26)
    d = _LiveDB("deviceData.db")
    d.c.executescript(_SYNC_DDL + "CREATE TABLE wallpaper(wallpaper_type INTEGER PRIMARY KEY NOT NULL, "
                                  "wallpaper_id INTEGER NOT NULL, blob BLOB);")
    d.c.execute("INSERT INTO content_sequence(content_type, sequence, expected) VALUES ('wallpaper', 2, 2)")
    d.c.execute("INSERT INTO wallpaper VALUES (0, 7, ?)", (JPEG_STUB,))
    d.save(dbdir)
    d = _LiveDB("continuity.db")
    d.c.executescript(CONTINUITY_DDL)
    d.c.execute("INSERT INTO browser_history(package_name, browser_history_title, task_id, web_uri, source) "
                "VALUES ('com.android.chrome', 'Example Domain', 12, 'https://example.com/', NULL)")
    d.save(dbdir)
    d = _LiveDB("sharedContent.db")
    d.c.executescript(SHARED_CONTENT_DDL)
    d.save(dbdir)
    return dbdir
