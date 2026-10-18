"""Microsoft Phone Link / Your Phone caches of linked phones (Windows).

Phone Link (package ``Microsoft.YourPhone_8wekyb3d8bbwe``) keeps one folder
per linked phone under
``%LOCALAPPDATA%\\Packages\\Microsoft.YourPhone_8wekyb3d8bbwe\\LocalCache\\Indexed\\<device id>\\``
with SQLite databases in ``System\\Database``:

* ``phone.db`` — SMS (``message``), MMS (``mms``/``mms_part``), conversations,
  and the SIM ``subscription`` table, which can hold the phone's own number;
  contacts lived here before ~2020.
* ``contacts.db`` — the synced address book.
* ``calling.db`` — ``call_history`` (Android CallLog types).
* ``notifications.db`` — mirrored notifications as Android JSON.
* ``photos.db`` — recent photos with thumbnail and full-size BLOBs.
* ``settings.db`` / ``phoneapps.db`` — installed-app inventory.

All times inside the databases are FILETIME (UTC), except notification JSON
``postTime`` (Unix ms). Every record is attributed to the profile's phone.

Rows deleted or changed since the last checkpoint (dismissed notifications,
deleted messages, earlier versions of edited rows) are recovered from the
``-wal`` file and the main database and reported as ``*_recovered`` events.
Profiles persist after a phone is unlinked, so a profile proves the phone was
linked at some time, not that it still is. Databases Connected Phone Forensics does not know
(e.g. ``deviceData.db``) are listed as unrecognised with their tables.

Phone Link for iPhone (Bluetooth MAP/PBAP/HFP) keeps little or nothing on
disk according to Microsoft; what it stores has not been publicly documented.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..readers.sqlite import SafeDB
from ..readers.sqlite_wal import RowVersion, recover_table_versions
from ..timeutil import from_filetime, from_unix, from_unix_auto

PKG = "AppData/Local/Packages/Microsoft.YourPhone_8wekyb3d8bbwe"
INDEXED = f"{PKG}/LocalCache/Indexed"

_CALL_TYPES = {1: "incoming", 2: "outgoing", 3: "missed", 4: "voicemail", 5: "rejected",
               6: "blocked", 7: "answered_externally"}
_MSG_TYPES = {1: "received", 2: "sent"}
_HANDLED = {"phone.db", "contacts.db", "calling.db", "notifications.db", "photos.db",
            "settings.db", "phoneapps.db"}


def _digits(number: str | None) -> str:
    d = re.sub(r"\D", "", number or "")
    return d[-9:] if len(d) >= 9 else d


def _short(text: Any, n: int = 140) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


class _Profile:
    def __init__(self, user: str, folder: Path):
        self.user = user
        self.folder = folder
        self.device_id = folder.name
        self.ids = [ident(IdKind.APP_DEVICE_ID, f"phonelink:{folder.name.lower()}")]
        self.names: list[str] = []
        self.contacts: dict[str, str] = {}   # last digits of number -> display name
        self.phone_os: str | None = None

    def add_id(self, i) -> None:
        if i is not None and i not in self.ids:
            self.ids.append(i)

    def who(self, number: str | None) -> str:
        if not number:
            return ""
        name = self.contacts.get(_digits(number))
        return f"{name} ({number})" if name else str(number)


def _alias_index(db: SafeDB, table: str) -> int | None:
    """Column index of an INTEGER PRIMARY KEY (stored as NULL in records), if any."""
    info = list(db.rows(f'PRAGMA table_info("{table}")'))
    pks = [r for r in info if r[5]]
    if len(pks) == 1 and str(pks[0][2]).upper() == "INTEGER":
        return int(pks[0][0])
    return None


def recovered_rows(db: SafeDB, path: Path, table: str,
                   warn: Callable[[str, Path], None] | None = None) -> list[tuple[str, dict[str, Any], RowVersion]]:
    """Row versions present in the WAL/main file but not in the current table state.

    Returns (status, row-as-dict, version) with status "deleted" (rowid no
    longer exists) or "superseded" (an earlier/other version of a live row).
    When recovery cannot run on this file, ``warn`` is told why, so the
    report never implies that a file was searched for deleted rows when it
    was not.
    """
    cols = db.schema.columns(table)
    if not cols:
        return []
    alias = _alias_index(db, table)
    current: dict[int, tuple] = {}
    for r in db.rows(f'SELECT rowid AS _r, * FROM "{table}"'):
        current[r[0]] = tuple(r[1:])
    out = []
    rec = recover_table_versions(path, table)
    if rec.note:
        if warn is not None:
            warn(f"deleted/superseded-row recovery not performed for table {table}: {rec.note}", path)
        return []
    for v in rec.versions:
        vals = list(v.values) + [None] * (len(cols) - len(v.values))
        vals = vals[: len(cols)]
        if alias is not None and vals[alias] is None:
            vals[alias] = v.rowid
        key = tuple(vals)
        if v.rowid in current and current[v.rowid] == key:
            continue
        status = "superseded" if v.rowid in current else "deleted"
        out.append((status, dict(zip(cols, vals, strict=False)), v))
    return out


def _provenance_locator(table: str, rowid: Any, version: RowVersion | None = None) -> str:
    loc = f"table={table} rowid={rowid}"
    if version is not None:
        loc += f" recovered_from={version.source}" + (f" frame={version.frame}" if version.frame is not None else "")
    return loc


@register
class WindowsPhoneLink(Plugin):
    id = "win_phonelink"
    name = "Microsoft Phone Link (Your Phone) phone caches"
    os = "windows"
    channel = Channel.PHONE_LINK
    description = __doc__ or ""
    paths = [f"Users/*/{INDEXED}/**"]
    references = [
        "https://github.com/EricZimmerman/SQLECmd/tree/master/SQLMap/Maps (Windows_YourPhone_*.smap)",
        "https://github.com/kacos2000/Win10/tree/master/YourPhone",
        "https://github.com/labcif/YPA (Domingues et al., 'Digital forensic artifacts of the Your Phone "
        "application in Windows 10', FSI: Digital Investigation, 2019)",
        "https://github.com/Mxhlix/psst (notifications.db schema and WAL behaviour, 2026)",
        "https://github.com/petrroll/phonelink-cli (Phone Link 1.26091 data store, 2026)",
    ]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for user, folder in ctx.user_dirs(f"{INDEXED}/*"):
            dbdir = ctx.fs.child(folder, "System", "Database")
            if dbdir is None or not dbdir.is_dir():
                continue
            self._profile(ctx, _Profile(user, folder), dbdir)

    # -- profile ------------------------------------------------------------------

    def _profile(self, ctx: Context, prof: _Profile, dbdir: Path) -> None:
        legacy = ctx.fs.child(prof.folder, "User")
        if legacy is not None and legacy.is_dir():
            for d in sorted(legacy.iterdir()):
                if d.is_dir():
                    prof.names.append(d.name)
                    prof.add_id(ident(IdKind.DEVICE_NAME, d.name))
        dbs = {p.name.lower(): p for p in sorted(dbdir.iterdir())
               if p.is_file() and p.name.lower().endswith(".db")}
        # Contacts first so messages and calls can show names; phone.db next for identity.
        order = ["contacts.db", "phone.db", "calling.db", "notifications.db", "photos.db",
                 "settings.db", "phoneapps.db"]
        handlers = {
            "contacts.db": self._contacts_db, "phone.db": self._phone_db, "calling.db": self._calling_db,
            "notifications.db": self._notifications_db, "photos.db": self._photos_db,
            "settings.db": self._apps_db, "phoneapps.db": self._apps_db,
        }
        inventory = []
        for name in order:
            if name in dbs:
                handlers[name](ctx, prof, dbs[name])
        for name, path in dbs.items():
            wal = path.with_name(path.name + "-wal")
            entry = {"file": path.name, "size": path.stat().st_size,
                     "wal_size": wal.stat().st_size if wal.is_file() else None}
            inventory.append(entry)
            if name not in _HANDLED:
                with ctx.file_scope(path), ctx.sqlite(path) as db:
                    ctx.cover(path, UNRECOGNISED,
                              note="database not parsed by Connected Phone Forensics; tables: " + ", ".join(
                                  f"{t}({len(c)} cols)" for t, c in db.schema.tables.items()))
        mtime = from_unix(prof.folder.stat().st_mtime)
        ctx.emit(Observation(
            artifact=self.id, event="phonelink_profile",
            message=f"Phone Link profile {prof.device_id} for a linked phone"
                    + (f" ({', '.join(prof.names)})" if prof.names else "")
                    + f": {len(dbs)} database(s)",
            channel=Channel.PHONE_LINK, timestamp=mtime,
            timestamp_desc="Profile folder modified (filesystem time)", time_source="filesystem",
            provenance=ctx.prov(dbdir, "folder"), device_ids=prof.ids, phone_os=prof.phone_os,
            device_class="phone", user=prof.user,
            data={"device_id": prof.device_id, "legacy_names": prof.names, "databases": inventory,
                  "note": "profiles persist after unlinking; presence does not prove a current link"},
        ))

    def _obs(self, ctx, prof, path, table, rowid, event, message, ts, desc, data, version=None, status=None):
        if status:
            event = f"{event}_recovered"
            data = {**data, "recovered": status, "recovered_from": version.source if version else None}
            message = f"[recovered: {status}] {message}"
        ctx.emit(Observation(
            artifact=self.id, event=event, message=message, channel=Channel.PHONE_LINK,
            timestamp=ts, timestamp_desc=desc,
            provenance=ctx.prov(path, _provenance_locator(table, rowid, version)),
            device_ids=prof.ids, data=data, phone_os=prof.phone_os, device_class="phone", user=prof.user,
        ))

    # -- contacts ---------------------------------------------------------------------

    def _contacts_db(self, ctx: Context, prof: _Profile, path: Path) -> None:
        with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
            if not db.schema.has("contact", "contact_id"):
                ctx.cover(path, UNRECOGNISED, note=f"no contact table; schema {db.schema.fingerprint()}")
                return
            n[0] += self._contacts(ctx, prof, path, db, "contact", "phonenumber", "emailaddress")

    def _contacts(self, ctx, prof, path, db: SafeDB, contact_t, number_t, email_t) -> int:
        numbers: dict[Any, list[str]] = defaultdict(list)
        if db.schema.has(number_t, "contact_id"):
            num_col = "display_phone_number" if db.schema.has(number_t, "display_phone_number") else (
                "phone_number" if db.schema.has(number_t, "phone_number") else "address")
            for r in db.select(number_t, ["contact_id", num_col, "phone_number"]):
                for v in {r[num_col], r["phone_number"]} - {None, ""}:
                    numbers[r["contact_id"]].append(str(v))
        emails: dict[Any, list[str]] = defaultdict(list)
        if email_t and db.schema.has(email_t, "contact_id", "address"):
            for r in db.select(email_t, ["contact_id", "address"]):
                if r["address"]:
                    emails[r["contact_id"]].append(r["address"])
        count = 0
        cols = ["contact_id", "display_name", "nickname", "last_updated_time", "company", "job_title"]
        for r in db.select(contact_t, cols):
            name = r["display_name"] or r["nickname"]
            nums = sorted(set(numbers.get(r["contact_id"], [])))
            for num in nums:
                if name and _digits(num):
                    prof.contacts.setdefault(_digits(num), name)
            data = {"contact_id": r["contact_id"], "name": name, "numbers": nums,
                    "emails": sorted(set(emails.get(r["contact_id"], []))),
                    "company": r["company"], "job_title": r["job_title"]}
            self._obs(ctx, prof, path, contact_t, r["_rowid_"], "contact",
                      f"Contact {name or '(no name)'}" + (f": {', '.join(nums)}" if nums else ""),
                      from_filetime(r["last_updated_time"]), "Contact last updated (Phone Link sync)", data)
            count += 1
        return count

    # -- phone.db ------------------------------------------------------------------------

    def _phone_db(self, ctx: Context, prof: _Profile, path: Path) -> None:
        with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
            sc = db.schema
            if not (sc.has("message") or sc.has("mms") or sc.has("subscription")):
                ctx.cover(path, UNRECOGNISED, note=f"no message/mms/subscription tables; schema {sc.fingerprint()}")
                return
            if sc.has("subscription", "number"):
                for r in db.select("subscription", ["subscription_id", "sim_slot_index", "number", "name",
                                                    "country_iso", "is_default_sms_subscription"]):
                    prof.add_id(ident(IdKind.PHONE_NUMBER, r["number"]))
                    prof.phone_os = prof.phone_os or "android"
                    self._obs(ctx, prof, path, "subscription", r["_rowid_"], "phone_subscription",
                              f"SIM {r['sim_slot_index']}: {r['name'] or 'carrier unknown'}"
                              f" ({r['country_iso'] or '?'}), number {r['number'] or 'not exposed'}",
                              None, "", {k: r[k] for k in r.keys() if k != "_rowid_"})  # noqa: SIM118 - sqlite3.Row iterates values
                    n[0] += 1
            if sc.has("contact", "contact_id") and not sc.has("message", "contact_id"):
                # Pre-2020 schema: contacts in phone.db with numbers in 'address'.
                n[0] += self._contacts(ctx, prof, path, db, "contact", "address", None)
            recipients: dict[Any, list[str]] = defaultdict(list)
            if sc.has("message_to_address", "message_id", "address"):
                for r in db.select("message_to_address", ["message_id", "address"]):
                    recipients[r["message_id"]].append(r["address"])
            threads: dict[Any, str] = {}
            if sc.has("conversation", "thread_id"):
                for r in db.select("conversation", ["thread_id", "recipient_list"]):
                    threads[r["thread_id"]] = r["recipient_list"]
            if sc.has("message", "body"):
                cols = ["message_id", "thread_id", "from_address", "type", "body", "timestamp", "status",
                        "pc_status", "subject"]
                for r in db.select("message", cols):
                    n[0] += self._sms(ctx, prof, path, dict(r), r["_rowid_"], recipients, threads)
                for status, row, ver in recovered_rows(db, path, "message", ctx.warn):
                    n[0] += self._sms(ctx, prof, path, row, ver.rowid, recipients, threads, ver, status)
            if sc.has("mms", "message_id"):
                parts: dict[Any, list[dict]] = defaultdict(list)
                if sc.has("mms_part", "message_id"):
                    pcols = ["message_id", "part_id", "content_type", "text", "name"]
                    if sc.has("mms_part", "blob"):
                        pcols.append("blob")
                    for p in db.select("mms_part", pcols):
                        parts[p["message_id"]].append(dict(p))
                for r in db.select("mms", ["message_id", "thread_id", "from_address", "type", "timestamp",
                                           "subject", "subscription_id"]):
                    n[0] += self._mms(ctx, prof, path, dict(r), r["_rowid_"], parts, threads)
                for status, row, ver in recovered_rows(db, path, "mms", ctx.warn):
                    n[0] += self._mms(ctx, prof, path, row, ver.rowid, parts, threads, ver, status)
            for t in sorted(x for x in sc.tables if x.startswith("rcs")):
                ctx.warn(f"RCS table '{t}' present but not parsed (schema not publicly verified); "
                         f"columns: {', '.join(sc.columns(t))}", path)

    def _sms(self, ctx, prof, path, r, rowid, recipients, threads, ver=None, status=None) -> int:
        direction = _MSG_TYPES.get(r.get("type"), f"type_{r.get('type')}")
        to = recipients.get(r.get("message_id")) or ([threads[r.get("thread_id")]] if threads.get(r.get("thread_id")) else [])
        if direction == "received":
            party = f"from {prof.who(r.get('from_address'))}"
        else:
            party = f"to {', '.join(prof.who(a) for a in to) or 'unknown'}"
        data = {"message_id": r.get("message_id"), "thread_id": r.get("thread_id"), "direction": direction,
                "from": r.get("from_address"), "to": to, "body": r.get("body"), "status": r.get("status"),
                "pc_status": r.get("pc_status")}
        self._obs(ctx, prof, path, "message", rowid, f"sms_{direction}",
                  f"SMS {party}: {_short(r.get('body'))}", from_filetime(r.get("timestamp")),
                  f"Message {direction}", data, ver, status)
        return 1

    def _mms(self, ctx, prof, path, r, rowid, parts, threads, ver=None, status=None) -> int:
        direction = _MSG_TYPES.get(r.get("type"), f"type_{r.get('type')}")
        texts, attachments = [], []
        for p in parts.get(r.get("message_id"), []):
            ctype = str(p.get("content_type") or "")
            if ctype == "text/plain" and p.get("text"):
                texts.append(p["text"])
            elif ctype not in ("application/smil", ""):
                att = {"content_type": ctype, "name": p.get("name")}
                blob = p.get("blob")
                if isinstance(blob, (bytes, bytearray)) and blob:
                    att.update(ctx.export(f"{prof.device_id}/mms/{r.get('message_id')}_{p.get('part_id')}_"
                                          f"{p.get('name') or 'part'}", bytes(blob)))
                attachments.append(att)
        party = (f"from {prof.who(r.get('from_address'))}" if direction == "received"
                 else f"to {prof.who(threads.get(r.get('thread_id')))}")
        data = {"message_id": r.get("message_id"), "thread_id": r.get("thread_id"), "direction": direction,
                "from": r.get("from_address"), "subject": r.get("subject"), "text": "\n".join(texts),
                "attachments": attachments, "subscription_id": r.get("subscription_id")}
        att = f" [{len(attachments)} attachment(s)]" if attachments else ""
        self._obs(ctx, prof, path, "mms", rowid, f"mms_{direction}",
                  f"MMS {party}: {_short(' '.join(texts) or r.get('subject'))}{att}",
                  from_filetime(r.get("timestamp")), f"Message {direction}", data, ver, status)
        return 1

    # -- calling.db -------------------------------------------------------------------------

    def _calling_db(self, ctx: Context, prof: _Profile, path: Path) -> None:
        with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
            if not db.schema.has("call_history", "start_time"):
                ctx.cover(path, UNRECOGNISED, note=f"no call_history table; schema {db.schema.fingerprint()}")
                return
            cols = ["call_id", "phone_number", "duration", "call_type", "start_time", "is_read",
                    "last_updated_time", "phone_account_id"]
            for r in db.select("call_history", cols):
                n[0] += self._call(ctx, prof, path, dict(r), r["_rowid_"])
            for status, row, ver in recovered_rows(db, path, "call_history", ctx.warn):
                n[0] += self._call(ctx, prof, path, row, ver.rowid, ver, status)

    def _call(self, ctx, prof, path, r, rowid, ver=None, status=None) -> int:
        kind = _CALL_TYPES.get(r.get("call_type"), f"type_{r.get('call_type')}")
        dur = r.get("duration")
        data = {"call_id": r.get("call_id"), "number": r.get("phone_number"), "type": kind,
                "duration_seconds": dur, "is_read": r.get("is_read"),
                "phone_account_id": r.get("phone_account_id"),
                "last_updated": from_filetime(r.get("last_updated_time")).isoformat()
                if from_filetime(r.get("last_updated_time")) else None}
        self._obs(ctx, prof, path, "call_history", rowid, f"call_{kind}",
                  f"Call {kind} {prof.who(r.get('phone_number'))}" + (f", {dur}s" if dur else ""),
                  from_filetime(r.get("start_time")), "Call start", data, ver, status)
        return 1

    # -- notifications.db ----------------------------------------------------------------------

    def _notifications_db(self, ctx: Context, prof: _Profile, path: Path) -> None:
        with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
            if not db.schema.has("notifications", "json"):
                ctx.cover(path, UNRECOGNISED, note=f"no notifications table; schema {db.schema.fingerprint()}")
                return
            cols = ["id", "notification_id", "package_name", "json", "post_time", "state"]
            for r in db.select("notifications", cols):
                n[0] += self._notification(ctx, prof, path, dict(r), r["_rowid_"])
            for status, row, ver in recovered_rows(db, path, "notifications", ctx.warn):
                n[0] += self._notification(ctx, prof, path, row, ver.rowid, ver, status)

    def _notification(self, ctx, prof, path, r, rowid, ver=None, status=None) -> int:
        try:
            j = json.loads(r.get("json") or "{}")
        except (TypeError, ValueError):
            j = {}
        if not isinstance(j, dict):
            j = {}
        if j.get("platform") and not prof.phone_os:
            prof.phone_os = str(j["platform"]).lower() if str(j["platform"]).lower() in ("android", "ios") else None
        app = j.get("appName") or j.get("packageName") or r.get("package_name")
        title, text = j.get("title"), j.get("bigText") or j.get("text")
        ts = from_filetime(r.get("post_time")) or from_unix_auto(j.get("postTime"))
        desc = "Notification posted (post_time)" if from_filetime(r.get("post_time")) else "Notification posted (JSON postTime)"
        flags = j.get("flags") if isinstance(j.get("flags"), int) else 0
        data = {"notification_id": r.get("notification_id"), "package": r.get("package_name") or j.get("packageName"),
                "app": app, "title": title, "text": text, "sub_text": j.get("subText"),
                "category": j.get("category"), "is_ongoing": j.get("isOngoing"),
                "group_summary": bool(flags & 0x200), "state": r.get("state"),
                "json_post_time": from_unix_auto(j.get("postTime")).isoformat() if from_unix_auto(j.get("postTime")) else None}
        self._obs(ctx, prof, path, "notifications", rowid, "notification",
                  f"Notification {app}: {_short(title, 60)} — {_short(text)}", ts, desc, data, ver, status)
        return 1

    # -- photos.db -------------------------------------------------------------------------------

    def _photos_db(self, ctx: Context, prof: _Profile, path: Path) -> None:
        with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
            sc = db.schema
            table = "media" if sc.has("media", "name") else "photo" if sc.has("photo", "name") else None
            if table is None:
                ctx.cover(path, UNRECOGNISED, note=f"no media/photo table; schema {sc.fingerprint()}")
                return
            cols = ["id", "photo_id", "name", "mime_type", "last_updated_time", "taken_time", "last_seen_time",
                    "height", "width", "size", "uri"]
            blob_cols = [c for c in ("media", "blob", "thumbnail") if sc.has(table, c)]
            for r in db.select(table, cols + blob_cols):
                pid = r["id"] if r["id"] is not None else r["photo_id"]
                exported = None
                for c in blob_cols:  # prefer the full image over the thumbnail
                    blob = r[c]
                    if isinstance(blob, (bytes, bytearray)) and blob:
                        exported = {"column": c, **ctx.export(f"{prof.device_id}/photos/{pid}_{r['name'] or 'photo'}",
                                                              bytes(blob))}
                        break
                ts = from_filetime(r["taken_time"]) or from_filetime(r["last_updated_time"])
                desc = "Photo taken (phone clock)" if from_filetime(r["taken_time"]) else "Photo last updated"
                data = {"media_id": pid, "name": r["name"], "mime_type": r["mime_type"], "uri": r["uri"],
                        "size": r["size"], "width": r["width"], "height": r["height"],
                        "last_seen": from_filetime(r["last_seen_time"]).isoformat()
                        if from_filetime(r["last_seen_time"]) else None, "export": exported}
                self._obs(ctx, prof, path, table, r["_rowid_"], "photo",
                          f"Photo {r['name']}" + (f" ({r['uri']})" if r["uri"] else ""), ts, desc, data)
                n[0] += 1

    # -- settings.db / phoneapps.db --------------------------------------------------------------

    def _apps_db(self, ctx: Context, prof: _Profile, path: Path) -> None:
        with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
            if not db.schema.has("phone_apps", "package_name"):
                if path.name.lower() == "settings.db" and db.schema.has("settings"):
                    ctx.cover(path, "parsed", 0, note="settings only (no phone_apps table)")
                    return
                ctx.cover(path, UNRECOGNISED, note=f"no phone_apps table; schema {db.schema.fingerprint()}")
                return
            apps = [{"name": r["app_name"], "package": r["package_name"], "version": r["version"],
                     "last_updated_raw": r["last_updated_time"]}
                    for r in db.select("phone_apps", ["app_name", "package_name", "version", "last_updated_time"])]
            if not apps:
                return
            ctx.emit(Observation(
                artifact=self.id, event="phone_app_inventory",
                message=f"{len(apps)} app(s) installed on the linked phone (from {path.name})",
                channel=Channel.PHONE_LINK, provenance=ctx.prov(path, "table=phone_apps"),
                device_ids=prof.ids, data={"apps": apps}, phone_os=prof.phone_os, device_class="phone",
                user=prof.user,
            ))
            n[0] += 1
