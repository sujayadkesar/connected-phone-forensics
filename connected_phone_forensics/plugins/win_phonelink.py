"""Microsoft Phone Link / Your Phone caches of linked phones (Windows).

Phone Link (package ``Microsoft.YourPhone_8wekyb3d8bbwe``) keeps one folder
per linked phone under
``%LOCALAPPDATA%\\Packages\\Microsoft.YourPhone_8wekyb3d8bbwe\\LocalCache\\Indexed\\<device id>\\``
with SQLite databases in ``System\\Database``:

* ``phone.db`` — SMS (``message``), MMS (``mms``/``mms_part``), RCS chat
  messages (``rcs_chat``) and file transfers (``rcs_filetransfer``) with their
  threads (``rcs_conversation``), conversations, and the SIM ``subscription``
  table, which can hold the phone's own number; contacts lived here before ~2020.
* ``contacts.db`` — the synced address book.
* ``calling.db`` — ``call_history`` (Android CallLog types).
* ``notifications.db`` — mirrored notifications as Android JSON.
* ``photos.db`` — recent photos with thumbnail and full-size BLOBs.
* ``settings.db`` / ``phoneapps.db`` — installed-app inventory.
* ``deviceData.db`` — the phone's wallpaper image (``wallpaper``).

``LocalCache\\DeviceMetadataStorage.json`` (one per Windows user) lists the
devices registered with Phone Link: for the phone, its display name,
manufacturer, model, OS and Phone Link app version, with registration and
last-seen times. An entry whose ``Metadata.Id`` equals a profile folder name
describes that profile's phone, so the name, model and OS label the phone.
The file also lists the computer itself (``OsName`` "Windows"); Windows
entries are reported but never attributed to a device.

All times inside the databases are FILETIME (UTC), except notification JSON
``postTime`` (Unix ms); the JSON file uses ISO 8601 with an offset. Every
record is attributed to the profile's phone. RCS rows are read like SMS:
``type`` 1/2 is received/sent and ``timestamp`` is FILETIME, as Phone Link's
own message library reads all message tables (see phonelink-cli). Other RCS
columns (``content_type``, ``message_type``, ``status``) are reported raw.
An RCS row's ``thread_id`` is resolved through ``rcs_conversation`` only;
``fallback_id`` there is the SMS/MMS thread of the same conversation.

Rows deleted or changed since the last checkpoint (dismissed notifications,
deleted messages, earlier versions of edited rows) are recovered from the
``-wal`` file and the main database and reported as ``*_recovered`` events.
Profiles persist after a phone is unlinked, so a profile proves the phone was
linked at some time, not that it still is. Databases Connected Phone Forensics does not parse
(``continuity.db``: phone browser history and app hand-off contexts;
``sharedContent.db``: links and content shared from the phone; their value
formats are not publicly documented) are listed as unrecognised with their
tables and row counts.

Phone Link for iPhone (Bluetooth MAP/PBAP/HFP) keeps little or nothing on
disk according to Microsoft; what it stores has not been publicly documented.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..model import Channel, Identifier, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..readers.sqlite import SafeDB
from ..readers.sqlite_wal import RowVersion, recover_table_versions
from ..timeutil import from_filetime, from_iso, from_unix, from_unix_auto

PKG = "AppData/Local/Packages/Microsoft.YourPhone_8wekyb3d8bbwe"
INDEXED = f"{PKG}/LocalCache/Indexed"
METADATA = f"{PKG}/LocalCache/DeviceMetadataStorage.json"

_CALL_TYPES = {1: "incoming", 2: "outgoing", 3: "missed", 4: "voicemail", 5: "rejected",
               6: "blocked", 7: "answered_externally"}
_MSG_TYPES = {1: "received", 2: "sent"}
_HANDLED = {"phone.db", "contacts.db", "calling.db", "notifications.db", "photos.db",
            "settings.db", "phoneapps.db", "devicedata.db"}
_RCS_TABLES = {"rcs_chat", "rcs_conversation", "rcs_filetransfer"}
# Sync bookkeeping present in every Phone Link database; holds nothing about the phone.
_BOOKKEEPING = {"content_sequence", "sqlite_sequence", "_change_notifications"}
# Databases whose tables are known by name but whose value formats are not publicly documented.
_UNPARSED = {"continuity.db": "phone browser history (browser_history) and app hand-off contexts "
                              "(continue_app_context)",
             "sharedcontent.db": "links (shareduri) and content (sharedcontent) shared from the phone"}
_OS = {"android": "android", "ios": "ios"}
_IMAGE_MAGIC = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG", ".png"), (b"GIF8", ".gif"))


def _digits(number: str | None) -> str:
    d = re.sub(r"\D", "", number or "")
    return d[-9:] if len(d) >= 9 else d


def _short(text: Any, n: int = 140) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _image_ext(blob: bytes) -> str:
    """File extension from an image's magic bytes; '.bin' when the format is not recognised."""
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    return next((ext for magic, ext in _IMAGE_MAGIC if blob.startswith(magic)), ".bin")


def _text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value).strip() or None


class _DeviceMeta:
    """One entry of DeviceMetadataStorage.json."""

    def __init__(self, locator: str, account_key: str, entry: dict[str, Any], meta: dict[str, Any]):
        inner = meta.get("Metadata") if isinstance(meta.get("Metadata"), dict) else {}
        self.locator = locator
        self.id = str(meta.get("Id") or "").strip().strip("{}").lower()
        self.name = _text(inner.get("DisplayName"))
        self.os_name = _text(inner.get("OsName"))
        self.os_version = _text(inner.get("OsVersion"))
        self.manufacturer = _text(inner.get("Manufacture"))
        self.model = _text(inner.get("ModelName"))
        self.phone_os = _OS.get((self.os_name or "").lower())
        reg = meta.get("RegitrationTime", meta.get("RegistrationTime"))  # sic: Phone Link spells it "Regitration"
        self.registered, self.last_seen = from_iso(reg), from_iso(meta.get("LastSeenTime"))
        custom = inner.get("CustomData")
        if isinstance(custom, str) and custom.strip():
            with contextlib.suppress(ValueError):  # not JSON: kept as the raw string
                custom = json.loads(custom)
        self.data = {
            "device_id": self.id, "display_name": self.name, "manufacturer": self.manufacturer,
            "model": self.model, "model_version": _text(inner.get("ModelVersion")), "os_name": self.os_name,
            "os_version": self.os_version, "client_type": _text(inner.get("ClientType")),
            "client_version": _text(inner.get("ClientVersion")), "capabilities": inner.get("Capabilities"),
            "is_linked": entry.get("IsLinked"), "is_enabled": inner.get("IsEnabled"),
            "consent_version": inner.get("ConsentVersion"),
            "distinguished_device_id": _text(meta.get("DistinguishedDeviceId")),
            "registration_time_raw": reg, "last_seen_time_raw": meta.get("LastSeenTime"),
            "custom_data": custom or None, "account_key": account_key,
        }

    def ids(self) -> list[Identifier | None]:
        os_label = " ".join(v for v in (self.os_name, self.os_version) if v)
        return [ident(IdKind.APP_DEVICE_ID, f"phonelink:{self.id}"), ident(IdKind.DEVICE_NAME, self.name),
                ident(IdKind.MODEL, self.model), ident(IdKind.OS_VERSION, os_label if self.os_version else None)]

    def describe(self) -> str:
        hw = " ".join(v for v in (self.manufacturer, self.model) if v)
        os_label = " ".join(v for v in (self.os_name, self.os_version) if v)
        extra = ", ".join(v for v in (hw, os_label) if v)
        return f"{self.name or '(no name)'}" + (f" ({extra})" if extra else "")


class _Profile:
    def __init__(self, user: str, folder: Path):
        self.user = user
        self.folder = folder
        self.device_id = folder.name
        self.ids = [ident(IdKind.APP_DEVICE_ID, f"phonelink:{folder.name.lower()}")]
        self.names: list[str] = []
        self.contacts: dict[str, str] = {}   # last digits of number -> display name
        self.phone_os: str | None = None
        self.meta: _DeviceMeta | None = None

    def apply(self, meta: _DeviceMeta) -> None:
        """Label the profile's phone with what DeviceMetadataStorage.json records about it."""
        self.meta = meta
        for i in meta.ids():
            self.add_id(i)
        self.phone_os = meta.phone_os or self.phone_os

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
    paths = [f"Users/*/{INDEXED}/**", f"Users/*/{METADATA}"]
    references = [
        "https://github.com/EricZimmerman/SQLECmd/tree/master/SQLMap/Maps (Windows_YourPhone_*.smap)",
        "https://github.com/kacos2000/Win10/tree/master/YourPhone",
        "https://github.com/labcif/YPA (Domingues et al., 'Digital forensic artifacts of the Your Phone "
        "application in Windows 10', FSI: Digital Investigation, 2019)",
        "https://github.com/Mxhlix/psst (notifications.db schema and WAL behaviour, 2026)",
        "https://github.com/petrroll/phonelink-cli (Phone Link 1.26091 data store, 2026; reads rcs_chat and "
        "rcs_filetransfer through Phone Link's message library, rcs_conversation.fallback_id as the SMS/MMS thread)",
    ]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        metadata: dict[str, dict[str, _DeviceMeta]] = {}
        for user, path in ctx.user_files(METADATA):
            metadata[user] = self._metadata_file(ctx, user, path)
        for user, folder in ctx.user_dirs(f"{INDEXED}/*"):
            dbdir = ctx.child(folder, "System", "Database")
            if dbdir is None or not dbdir.is_dir():
                continue
            prof = _Profile(user, folder)
            meta = metadata.get(user, {}).get(folder.name.lower())
            if meta is not None:
                prof.apply(meta)
            self._profile(ctx, prof, dbdir)

    # -- DeviceMetadataStorage.json -------------------------------------------------

    def _metadata_file(self, ctx: Context, user: str, path: Path) -> dict[str, _DeviceMeta]:
        """Report every registered device; returns the entries by device id."""
        found: dict[str, _DeviceMeta] = {}
        with ctx.file_scope(path) as n:
            try:
                doc = json.loads(ctx.read_text(path).lstrip("﻿"))
            except ValueError as exc:
                ctx.cover(path, UNRECOGNISED, note=f"not valid JSON: {exc}")
                return found
            devices = doc.get("DeviceMetadatas") if isinstance(doc, dict) else None
            if not isinstance(devices, dict):
                ctx.cover(path, UNRECOGNISED, note="no DeviceMetadatas object; structure not known")
                return found
            this_pc = (ctx.system.details.get("computer_name") or "").lower()
            for key, entries in devices.items():
                for i, entry in enumerate(entries if isinstance(entries, list) else [entries]):
                    loc = f"json:$.DeviceMetadatas.{key}[{i}]"
                    meta = entry.get("Metadata") if isinstance(entry, dict) else None
                    if not isinstance(meta, dict) or not str(meta.get("Id") or "").strip("{} "):
                        ctx.warn(f"{loc}: entry has no Metadata.Id; not parsed", path)
                        continue
                    dm = _DeviceMeta(loc, key, entry, meta)
                    found[dm.id] = dm
                    n[0] += self._registered_device(ctx, user, path, dm, this_pc)
        return found

    def _registered_device(self, ctx: Context, user: str, path: Path, dm: _DeviceMeta, this_pc: str) -> int:
        is_phone = dm.phone_os is not None
        named_like_pc = bool(this_pc) and (dm.name or "").lower() == this_pc
        # The file also lists the computer itself (and other PCs): Windows entries are never attributed.
        ids = [] if (dm.os_name or "").lower() == "windows" else dm.ids()
        what = "Phone" if is_phone else f"{dm.os_name or 'Unknown OS'} device"
        note = "; its name matches this computer's name" if named_like_pc else ""
        data = {**dm.data, "name_matches_this_computer": named_like_pc}
        events = [("phonelink_device_registered", dm.registered, "RegitrationTime", "registered with Phone Link"),
                  ("phonelink_device_last_seen", dm.last_seen, "LastSeenTime", "last seen by Phone Link")]
        count = 0
        for event, ts, field, verb in events:
            if ts is None and event == "phonelink_device_last_seen":
                continue
            ctx.emit(Observation(
                artifact=self.id, event=event, message=f"{what} {dm.describe()} {verb}{note}",
                channel=Channel.PHONE_LINK, timestamp=ts,
                timestamp_desc=f"{field} (DeviceMetadataStorage.json)" if ts else "",
                provenance=ctx.prov(path, f"{dm.locator}.Metadata.{field}"), device_ids=ids,
                data=data, phone_os=dm.phone_os, device_class="phone" if is_phone else None, user=user,
            ))
            count += 1
        return count

    # -- profile ------------------------------------------------------------------

    def _profile(self, ctx: Context, prof: _Profile, dbdir: Path) -> None:
        legacy = ctx.child(prof.folder, "User")
        if legacy is not None and legacy.is_dir():
            for d in sorted(legacy.iterdir()):
                if d.is_dir():
                    prof.names.append(d.name)
                    prof.add_id(ident(IdKind.DEVICE_NAME, d.name))
        dbs = {p.name.lower(): p for p in sorted(dbdir.iterdir())
               if p.is_file() and p.name.lower().endswith(".db")}
        # Contacts first so messages and calls can show names; phone.db next for identity.
        order = ["contacts.db", "phone.db", "calling.db", "notifications.db", "photos.db",
                 "settings.db", "phoneapps.db", "devicedata.db"]
        handlers = {
            "contacts.db": self._contacts_db, "phone.db": self._phone_db, "calling.db": self._calling_db,
            "notifications.db": self._notifications_db, "photos.db": self._photos_db,
            "settings.db": self._apps_db, "phoneapps.db": self._apps_db, "devicedata.db": self._device_data_db,
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
                self._unparsed_db(ctx, path, _UNPARSED.get(name))
        mtime = from_unix(prof.folder.stat().st_mtime)
        names = prof.names + ([prof.meta.describe()] if prof.meta else [])
        ctx.emit(Observation(
            artifact=self.id, event="phonelink_profile",
            message=f"Phone Link profile {prof.device_id} for a linked phone"
                    + (f" ({', '.join(names)})" if names else "")
                    + f": {len(dbs)} database(s)",
            channel=Channel.PHONE_LINK, timestamp=mtime,
            timestamp_desc="Profile folder modified (filesystem time)", time_source="filesystem",
            provenance=ctx.prov(dbdir, "folder"), device_ids=prof.ids, phone_os=prof.phone_os,
            device_class="phone", user=prof.user,
            data={"device_id": prof.device_id, "legacy_names": prof.names, "databases": inventory,
                  "device_metadata": prof.meta.locator if prof.meta else None,
                  "note": "profiles persist after unlinking; presence does not prove a current link"},
        ))

    def _unparsed_db(self, ctx: Context, path: Path, what: str | None) -> None:
        with ctx.file_scope(path), ctx.sqlite(path) as db:
            ctx.cover(path, UNRECOGNISED, note=self._unparsed_note(db, what))

    def _unparsed_note(self, db: SafeDB, what: str | None) -> str:
        tables = ", ".join(f"{t}({len(c)} cols, {self._count(db, t)} rows)" for t, c in db.schema.tables.items())
        known = (f"Phone Link database for {what}; not parsed because its value formats are not publicly "
                 "documented" if what else "database not parsed by Connected Phone Forensics")
        return f"{known}; tables: {tables}"

    @staticmethod
    def _count(db: SafeDB, table: str) -> int | str:
        try:
            return next(db.rows(f'SELECT count(*) FROM "{table}"'))[0]
        except Exception as exc:  # noqa: BLE001 - a broken table must not hide the others
            return f"unreadable: {type(exc).__name__}"

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
            if not (sc.has("message") or sc.has("mms") or sc.has("subscription") or sc.has("rcs_chat")):
                ctx.cover(path, UNRECOGNISED,
                          note=f"no message/mms/rcs_chat/subscription tables; schema {sc.fingerprint()}")
                return
            own_numbers: dict[Any, str] = {}
            if sc.has("subscription", "number"):
                for r in db.select("subscription", ["subscription_id", "sim_slot_index", "number", "name",
                                                    "country_iso", "is_default_sms_subscription"]):
                    if r["number"]:
                        own_numbers[r["subscription_id"]] = r["number"]
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
            n[0] += self._rcs_tables(ctx, prof, path, db, own_numbers)
            for t in sorted(x for x in sc.tables if x.startswith("rcs") and x not in _RCS_TABLES):
                ctx.warn(f"RCS table '{t}' present but not parsed (schema not publicly verified); "
                         f"columns: {', '.join(sc.columns(t))}", path)

    def _rcs_tables(self, ctx, prof, path, db: SafeDB, own_numbers: dict[Any, str]) -> int:
        sc = db.schema
        threads: dict[Any, dict[str, Any]] = {}
        if sc.has("rcs_conversation", "thread_id"):
            for r in db.select("rcs_conversation", ["thread_id", "fallback_id", "recipient_list", "type",
                                                    "msg_count"]):
                threads[r["thread_id"]] = dict(r)
        count = 0
        common = ["message_id", "thread_id", "content_type", "status", "from_address", "type", "timestamp",
                  "pc_status", "subscription_id", "message_type", "session_id", "is_user_action_required"]
        for table, extra in (("rcs_chat", ["body"]), ("rcs_filetransfer", ["name", "size", "blob"])):
            if not sc.has(table, "message_id"):
                continue
            for r in db.select(table, common + extra):
                count += self._rcs(ctx, prof, path, table, dict(r), r["_rowid_"], threads, own_numbers)
            for status, row, ver in recovered_rows(db, path, table, ctx.warn):
                count += self._rcs(ctx, prof, path, table, row, ver.rowid, threads, own_numbers, ver, status)
        return count

    def _rcs(self, ctx, prof, path, table, r, rowid, threads, own_numbers, ver=None, status=None) -> int:
        direction = _MSG_TYPES.get(r.get("type"), f"type_{r.get('type')}")
        thread = threads.get(r.get("thread_id")) or {}
        to = [thread["recipient_list"]] if thread.get("recipient_list") else []
        if direction == "received":
            party = f"from {prof.who(r.get('from_address'))}"
        else:
            party = f"to {', '.join(prof.who(a) for a in to) or 'unknown'}"
        data = {"message_id": r.get("message_id"), "thread_id": r.get("thread_id"), "direction": direction,
                "from": r.get("from_address"), "to": to, "sms_mms_thread_id": thread.get("fallback_id"),
                "thread_type": thread.get("type"), "content_type": r.get("content_type"),
                "message_type": r.get("message_type"), "session_id": r.get("session_id"),
                "status": r.get("status"), "pc_status": r.get("pc_status"),
                "subscription_id": r.get("subscription_id"), "own_number": own_numbers.get(r.get("subscription_id")),
                "is_user_action_required": r.get("is_user_action_required")}
        ts = from_filetime(r.get("timestamp"))
        if table == "rcs_chat":
            data["body"] = r.get("body")
            self._obs(ctx, prof, path, table, rowid, f"rcs_{direction}", f"RCS {party}: {_short(r.get('body'))}",
                      ts, f"Message {direction}", data, ver, status)
            return 1
        name, blob = r.get("name"), r.get("blob")
        attachment = {"name": name, "content_type": r.get("content_type"), "size": r.get("size")}
        if isinstance(blob, (bytes, bytearray)) and blob:
            where = f"recovered_{ver.source}{'' if ver.frame is None else ver.frame}_" if ver else ""
            attachment.update(ctx.export(f"{prof.device_id}/rcs/{where}{r.get('message_id')}_{name or 'file'}",
                                         bytes(blob)))
        data["attachment"] = attachment
        size = f", {r.get('size')} bytes" if r.get("size") is not None else ""
        self._obs(ctx, prof, path, table, rowid, f"rcs_file_{direction}",
                  f"RCS file {party}: {name or '(no name)'} ({r.get('content_type') or 'type unknown'}{size})",
                  ts, f"File transfer {direction}", data, ver, status)
        return 1

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

    # -- deviceData.db ------------------------------------------------------------------------------

    def _device_data_db(self, ctx: Context, prof: _Profile, path: Path) -> None:
        with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
            sc = db.schema
            if not sc.has("wallpaper", "blob"):
                ctx.cover(path, UNRECOGNISED, note=self._unparsed_note(db, None))
                return
            cols = ["wallpaper_type", "wallpaper_id", "blob"]
            for r in db.select("wallpaper", cols):
                n[0] += self._wallpaper(ctx, prof, path, dict(r), r["_rowid_"])
            for status, row, ver in recovered_rows(db, path, "wallpaper", ctx.warn):
                n[0] += self._wallpaper(ctx, prof, path, row, ver.rowid, ver, status)
            for t in sorted(set(sc.tables) - _BOOKKEEPING - {"wallpaper"}):
                ctx.warn(f"table '{t}' in deviceData.db not parsed ({self._count(db, t)} rows); "
                         f"columns: {', '.join(sc.columns(t))}", path)

    def _wallpaper(self, ctx, prof, path, r, rowid, ver=None, status=None) -> int:
        blob = r.get("blob")
        exported = None
        if isinstance(blob, (bytes, bytearray)) and blob:
            where = f"recovered_{ver.source}{'' if ver.frame is None else ver.frame}_" if ver else ""
            exported = ctx.export(f"{prof.device_id}/wallpaper/{where}{r.get('wallpaper_type')}_"
                                  f"{r.get('wallpaper_id')}{_image_ext(bytes(blob))}", bytes(blob))
        data = {"wallpaper_type": r.get("wallpaper_type"), "wallpaper_id": r.get("wallpaper_id"), "export": exported}
        size = f", {exported['size']} bytes" if exported else ", no image data"
        self._obs(ctx, prof, path, "wallpaper", rowid, "phone_wallpaper",
                  f"Phone wallpaper (type {r.get('wallpaper_type')}, id {r.get('wallpaper_id')}){size}",
                  None, "", data, ver, status)
        return 1

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
