"""Synthetic Phone Link profile built from publicly documented schemas.

Table layouts follow the SQLECmd maps (Windows_YourPhone_*.smap), kacos2000's
Win10/YourPhone queries, YPA and psst's notifications/phone_apps DDL. Rows
are invented. Some rows are deleted or updated after a checkpoint so they
exist only in the main file or in older WAL frames, as on a live system.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


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


def build_phonelink_profile(users_dir: Path, user: str, device_id: str, *, phone_number: str,
                            contacts: list[tuple[str, str]], base: datetime) -> Path:
    """Create Users/<user>/AppData/.../Indexed/<device_id>/System/Database with documented schemas."""
    dbdir = (users_dir / user / "AppData" / "Local" / "Packages" / "Microsoft.YourPhone_8wekyb3d8bbwe" /
             "LocalCache" / "Indexed" / device_id / "System" / "Database")

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
        CREATE TABLE rcs_chat(id INTEGER PRIMARY KEY, thread_id INTEGER, body TEXT, timestamp INTEGER);
    """)
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
    d.checkpoint()
    d.c.execute("DELETE FROM message WHERE message_id=1003")  # deleted after the checkpoint
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
    d = _LiveDB("deviceData.db")
    d.c.execute("CREATE TABLE wallpaper(id INTEGER PRIMARY KEY, blob BLOB)")
    d.save(dbdir)
    return dbdir
