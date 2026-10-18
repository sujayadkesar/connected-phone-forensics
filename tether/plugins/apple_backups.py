"""Local iPhone/iPad backups made by iTunes, Finder or the Apple Devices app.

Each backup is a folder named after the device UDID containing Info.plist
(device identity, phone number, IMEI, installed apps, last backup date),
Manifest.plist (encryption flag, passcode flag, lockdown summary),
Status.plist (backup state and date) and Manifest.db (file index; itself
encrypted when the backup is encrypted).

Even when the backup content is encrypted, the identity and date metadata in
Info.plist is in plain text. A backup can also contain far more phone data
than any other artifact on the computer, so its presence is a lead to process
the backup itself with a mobile parser such as iLEAPP.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..readers.sqlite import is_sqlite
from ..timeutil import from_datetime

_REFS = [
    "https://github.com/abrignoni/iLEAPP (backup metadata parsing)",
    "https://github.com/EricZimmerman/KapeFiles/blob/master/Targets/Apps/iTunesBackup.tkape",
    "https://support.apple.com/en-us/108809 (backup locations)",
]

_INFO_IDS = [
    ("Unique Identifier", IdKind.UDID),
    ("Target Identifier", IdKind.UDID),
    ("Serial Number", IdKind.SERIAL),
    ("IMEI", IdKind.IMEI),
    ("IMEI 2", IdKind.IMEI),
    ("MEID", IdKind.MEID),
    ("ICCID", IdKind.ICCID),
    ("Phone Number", IdKind.PHONE_NUMBER),
    ("Device Name", IdKind.DEVICE_NAME),
    ("Product Type", IdKind.MODEL),
    ("Product Version", IdKind.OS_VERSION),
]


def apple_device_class(product_type: str | None) -> str:
    pt = (product_type or "").lower()
    if pt.startswith("ipad"):
        return "tablet"
    if pt.startswith(("ipod", "watch", "appletv", "audioaccessory")):
        return "other"
    return "phone"


def _load(ctx: Context, path: Path | None):
    if path is None or not path.is_file():
        return None
    try:
        return ctx.plist(path)
    except Exception:  # noqa: BLE001 - caller records coverage
        return None


def _parse_backup(ctx: Context, folder: Path) -> None:
    info_p = ctx.fs.child(folder, "Info.plist")
    manifest_p = ctx.fs.child(folder, "Manifest.plist")
    status_p = ctx.fs.child(folder, "Status.plist")
    mdb_p = ctx.fs.child(folder, "Manifest.db")
    anchor = info_p or manifest_p or status_p
    if anchor is None:
        return
    with ctx.file_scope(anchor) as n:
        info = _load(ctx, info_p) or {}
        manifest = _load(ctx, manifest_p) or {}
        status = _load(ctx, status_p) or {}
        if not isinstance(info, dict) or not (info or manifest or status):
            ctx.cover(anchor, UNRECOGNISED, note="backup plists unreadable")
            return
        lockdown = manifest.get("Lockdown", {}) if isinstance(manifest, dict) else {}
        ids = [ident(kind, info.get(key)) for key, kind in _INFO_IDS]
        ids += [
            ident(IdKind.UDID, lockdown.get("UniqueDeviceID")),
            ident(IdKind.SERIAL, lockdown.get("SerialNumber")),
            ident(IdKind.DEVICE_NAME, lockdown.get("DeviceName")),
            ident(IdKind.MODEL, lockdown.get("ProductType")),
            ident(IdKind.OS_VERSION, lockdown.get("ProductVersion")),
        ]
        if not any(i and i.kind == IdKind.UDID for i in ids):
            ids.append(ident(IdKind.UDID, folder.name))  # folder is named after the UDID
        ids = list(dict.fromkeys(i for i in ids if i))
        apps = info.get("Installed Applications") or []
        manifest_apps = manifest.get("Applications") if isinstance(manifest, dict) else None
        encrypted = manifest.get("IsEncrypted") if isinstance(manifest, dict) else None
        mdb_kind = None
        if mdb_p is not None:
            ctx.fs.sha256(mdb_p)
            mdb_kind = "sqlite" if is_sqlite(mdb_p) else "encrypted_or_unknown"
        data = {
            "backup_folder": ctx.fs.rel(folder),
            "device_name": info.get("Device Name") or lockdown.get("DeviceName"),
            "display_name": info.get("Display Name"),
            "product_type": info.get("Product Type") or lockdown.get("ProductType"),
            "product_version": info.get("Product Version") or lockdown.get("ProductVersion"),
            "build_version": info.get("Build Version") or lockdown.get("BuildVersion"),
            "phone_number": info.get("Phone Number"),
            "itunes_version": info.get("iTunes Version"),
            "is_encrypted": encrypted,
            "was_passcode_set": manifest.get("WasPasscodeSet") if isinstance(manifest, dict) else None,
            "is_full_backup": status.get("IsFullBackup") if isinstance(status, dict) else None,
            "snapshot_state": status.get("SnapshotState") if isinstance(status, dict) else None,
            "backup_uuid": status.get("UUID") if isinstance(status, dict) else None,
            "manifest_db": mdb_kind,
            "installed_applications": list(apps) if isinstance(apps, list) else [],
            "manifest_application_count": len(manifest_apps) if isinstance(manifest_apps, dict) else None,
        }
        label = data["device_name"] or folder.name
        dclass = apple_device_class(data["product_type"])
        times: list[tuple[datetime | None, str, str, Path | None]] = [
            (from_datetime(info.get("Last Backup Date")), "Last Backup Date (Info.plist)",
             "plist:$['Last Backup Date']", info_p),
            (from_datetime(status.get("Date")) if isinstance(status, dict) else None,
             "Backup status date (Status.plist)", "plist:$.Date", status_p),
            (from_datetime(manifest.get("Date")) if isinstance(manifest, dict) else None,
             "Backup manifest date (Manifest.plist)", "plist:$.Date", manifest_p),
        ]
        emitted = 0
        for ts, desc, locator, src in times:
            if ts is None or src is None:
                continue
            enc = " (encrypted)" if encrypted else ""
            ctx.emit(Observation(
                artifact=ctx.plugin.id, event="device_backup",
                message=f"Local backup of {label}{enc}: {desc}",
                channel=Channel.BACKUP, timestamp=ts, timestamp_desc=desc,
                provenance=ctx.prov(src, locator), device_ids=ids, data=data, phone_os="ios",
                device_class=dclass,
            ))
            emitted += 1
        if not emitted:
            ctx.emit(Observation(
                artifact=ctx.plugin.id, event="device_backup",
                message=f"Local backup of {label} (no backup date recorded)",
                channel=Channel.BACKUP, provenance=ctx.prov(anchor, "folder"),
                device_ids=ids, data=data, phone_os="ios", device_class=dclass,
            ))
            emitted = 1
        n[0] += emitted


def _backup_folders(ctx: Context, patterns: list[str]) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in patterns:
        for _user, d in ctx.user_dirs(pattern):
            seen[d] = None
    return list(seen)


_WIN_PATTERNS = [
    "AppData/Roaming/Apple Computer/MobileSync/Backup/*",
    "AppData/Roaming/Apple/MobileSync/Backup/*",
    "Apple/MobileSync/Backup/*",
]
_MAC_PATTERNS = ["Library/Application Support/MobileSync/Backup/*"]


@register
class WindowsAppleBackups(Plugin):
    id = "win_apple_backups"
    name = "iOS device backups (iTunes / Apple Devices)"
    os = "windows"
    channel = Channel.BACKUP
    description = __doc__ or ""
    paths = [f"Users/*/{p}/{f}" for p in _WIN_PATTERNS
             for f in ("Info.plist", "Manifest.plist", "Status.plist", "Manifest.db")]
    references = _REFS
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for folder in _backup_folders(ctx, _WIN_PATTERNS):
            _parse_backup(ctx, folder)


@register
class MacAppleBackups(Plugin):
    id = "mac_apple_backups"
    name = "iOS device backups (Finder / iTunes)"
    os = "macos"
    channel = Channel.BACKUP
    description = __doc__ or ""
    paths = [f"Users/*/{p}/{f}" for p in _MAC_PATTERNS
             for f in ("Info.plist", "Manifest.plist", "Status.plist", "Manifest.db")]
    references = _REFS
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for folder in _backup_folders(ctx, _MAC_PATTERNS):
            _parse_backup(ctx, folder)
