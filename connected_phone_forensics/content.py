"""What was recovered about each phone, by kind: messages, calls, contacts, photos and files,
notifications, apps and connections.

Works on the plain records a scan writes (``observations.jsonl``, or ``CaseData.records``), so
the app's dashboard, the Excel workbook and the PDF summary all show the same rows, whether
the case was just scanned or reopened. Each item keeps its source file and locator, and
``record`` (the index of the record it came from) links it back to the timeline.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .casedata import CHANNEL_LABEL

PHONE_CLASSES = (None, "phone", "tablet")
UNLINKED = "Not linked to a phone"


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    width: int = 16          # characters, for spreadsheets
    kind: str = "text"       # text | time | number | long | path


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    icon: str
    description: str
    columns: tuple[Column, ...]

    def column(self, key: str) -> Column | None:
        return next((c for c in self.columns if c.key == key), None)


_TIME = Column("time", "Time (UTC)", 20, "time")
_PHONE = Column("phone", "Phone", 26)
_STATUS = Column("status", "Status", 14)
_SOURCE = Column("source", "Source", 48, "path")
_COMPUTER = Column("system", "Computer", 16)

CATEGORIES: tuple[Category, ...] = (
    Category("messages", "Messages", "ph.chat-text", "SMS, MMS, RCS and iMessage messages",
             (_TIME, _PHONE, Column("direction", "Direction", 11), Column("party", "Contact", 30),
              Column("text", "Message", 60, "long"), Column("service", "Service", 10),
              Column("attachments", "Attachments", 22), _STATUS, _COMPUTER, _SOURCE)),
    Category("calls", "Calls", "ph.phone", "Call history",
             (_TIME, _PHONE, Column("direction", "Direction", 11), Column("party", "Contact", 30),
              Column("duration", "Duration (s)", 12, "number"), Column("service", "Service", 18), _STATUS,
              _COMPUTER, _SOURCE)),
    Category("contacts", "Contacts", "ph.address-book", "The phone's address book",
             (_PHONE, Column("name", "Name", 28), Column("numbers", "Phone numbers", 34),
              Column("emails", "Email addresses", 30), Column("organisation", "Organisation", 22), _STATUS,
              _COMPUTER, _SOURCE)),
    Category("media", "Photos & files", "ph.image", "Photos, received files and attachments",
             (_TIME, _PHONE, Column("name", "File name", 34), Column("kind", "Kind", 16),
              Column("mime", "Type", 14), Column("size", "Size (bytes)", 13, "number"),
              Column("dimensions", "Dimensions", 12), Column("party", "From / to", 26),
              Column("file", "Saved copy", 40, "path"), _STATUS, _COMPUTER, _SOURCE)),
    Category("notifications", "Notifications", "ph.bell", "Notifications mirrored from the phone",
             (_TIME, _PHONE, Column("app", "App", 20), Column("title", "Title", 28),
              Column("text", "Text", 60, "long"), _STATUS, _COMPUTER, _SOURCE)),
    Category("apps", "Apps", "ph.squares-four", "Apps installed on the phone",
             (_PHONE, Column("name", "App", 26), Column("package", "Package / bundle ID", 36),
              Column("version", "Version", 14), Column("listed_by", "Listed by", 30), _COMPUTER, _SOURCE)),
    Category("connections", "Connections", "ph.link", "USB, Bluetooth, hotspot, pairing, backup and app records",
             (_TIME, _PHONE, Column("channel", "Connection", 15), Column("what", "What", 26),
              Column("text", "Details", 60, "long"), _COMPUTER, Column("user", "User", 12), _SOURCE)),
)
BY_KEY = {c.key: c for c in CATEGORIES}

_SERVICE = {"sms": "SMS", "mms": "MMS", "rcs": "RCS", "imessage": "iMessage", "imessagelite": "iMessage Lite",
            "message": "Message"}
_MESSAGE_EVENT = re.compile(r"^(sms|mms|rcs|imessage|imessagelite|message|[a-z]+)_(sent|received|type_\w+)"
                            r"(_recovered)?$")
_MESSAGE_ARTIFACTS = ("win_phonelink", "mac_messages")


def _digits(number: Any) -> str:
    d = re.sub(r"\D", "", str(number or ""))
    return d[-9:] if len(d) >= 9 else d


def _as_list(value: Any) -> list:
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _status(data: dict) -> str:
    rec = data.get("recovered")
    if not rec:
        return ""
    return {"deleted": "Deleted, recovered", "superseded": "Older version, recovered"}.get(
        str(rec), f"Recovered ({rec})")


EVENT_LABEL = {
    "usb_driver_first_install": "USB driver first installed", "usb_first_install": "USB first install",
    "usb_install": "USB install", "usb_last_arrival": "USB last connected", "usb_last_removal": "USB last removed",
    "usb_device_key_last_written": "USB device key last written", "bt_last_connected": "Bluetooth last connected",
    "bt_last_seen": "Bluetooth last seen", "bt_key_last_written": "Bluetooth key last written",
    "bt_device": "Bluetooth device", "bt_pairing_record": "Bluetooth pairing", "bt_cached_device": "Bluetooth seen",
    "bt_phone_service": "Bluetooth phone service", "hotspot_first_connected": "Hotspot first connected",
    "hotspot_last_connected": "Hotspot last connected", "hotspot_dhcp_lease": "Hotspot DHCP lease",
    "pairing_certificate_issued": "Trusted this computer", "pair_record_modified": "Pair record last written",
    "host_system_buid": "Pairing host ID", "device_backup": "Local backup", "phonelink_profile": "Phone Link profile",
    "phone_subscription": "SIM / phone line", "ms_account_device": "Same Microsoft account",
    "crossdevice_settings": "Cross-device settings", "kdeconnect_paired": "KDE Connect pairing",
    "kdeconnect_identity_created": "KDE Connect identity created", "biome_peer_last_sync": "Biome sync",
    "biome_stream_first_merged": "Biome stream first merged", "biome_stream_last_merged": "Biome stream last merged",
    "knowledgec_peer_last_seen": "Continuity peer last seen", "iphone_mirroring_configured": "iPhone Mirroring set up",
    "phonelink_toast_handler_registered": "Phone Link notifications registered",
}


def _what(event: str) -> str:
    base = event.replace("_recovered", "")
    if base in EVENT_LABEL:
        return EVENT_LABEL[base] + (" (recovered)" if base != event else "")
    text = base.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


class _Names:
    """Phone number -> contact name, per phone, from the contact records."""

    def __init__(self, records: Iterable[dict]):
        self.by_device: dict[str | None, dict[str, str]] = defaultdict(dict)
        for r in records:
            if r.get("event", "").startswith("contact") and isinstance(r.get("data"), dict):
                name = r["data"].get("name")
                for n in _as_list(r["data"].get("numbers")):
                    if name and _digits(n):
                        self.by_device[r.get("device")].setdefault(_digits(n), str(name))

    def who(self, device: str | None, number: Any, name: Any = None) -> str:
        if not number and not name:
            return ""
        if not name:
            name = self.by_device.get(device, {}).get(_digits(number)) if _digits(number) else None
        if name and number and str(name) != str(number):
            return f"{name} ({number})"
        return str(name or number)


def extract(records: list[dict], devices: list[dict]) -> dict[str, list[dict]]:
    """Every recovered item, by category key, in time order (items without a time last)."""
    by_id = {d["id"]: d for d in devices}
    names = _Names(records)
    out: dict[str, list[dict]] = {c.key: [] for c in CATEGORIES}
    seen_apps: dict[tuple, dict] = {}

    for index, r in enumerate(records):
        dev = by_id.get(r.get("device") or "")
        is_phone = dev is not None and dev.get("device_class") in PHONE_CLASSES
        if dev is not None and not is_phone:
            continue  # headsets, computers, peripherals: not phone content
        event, artifact = r.get("event") or "", r.get("artifact") or ""
        data = r.get("data") if isinstance(r.get("data"), dict) else {}
        base = {"record": index, "time": r.get("datetime"), "device": r.get("device") if is_phone else None,
                "phone": dev.get("label") if is_phone else UNLINKED, "system": r.get("system") or "",
                "user": r.get("user") or "", "channel": CHANNEL_LABEL.get(r.get("channel") or "", r.get("channel")),
                "status": _status(data), "artifact": artifact, "event": event,
                "time_source": r.get("time_source") or "artifact",
                "source_path": r.get("source_path") or "", "locator": r.get("locator") or "",
                "sha256": r.get("sha256") or "",
                "source": " · ".join(x for x in (r.get("source_path"), r.get("locator")) if x)}
        device = base["device"]

        m = _MESSAGE_EVENT.match(event)
        if artifact in _MESSAGE_ARTIFACTS and m and not event.startswith(("rcs_file", "call_")):
            kind, direction = m.group(1), m.group(2)
            if artifact == "mac_messages":
                party = names.who(device, data.get("other_party"))
                text = data.get("text")
                service = data.get("service") or _SERVICE.get(kind, kind.upper())
            else:
                numbers = [data.get("from")] if direction == "received" else _as_list(data.get("to"))
                party = ", ".join(names.who(device, n) for n in numbers if n) or "unknown"
                text = data.get("body") if "body" in data else data.get("text")
                if kind == "mms" and data.get("subject"):
                    text = f"[{data['subject']}] {text or ''}".strip()
                service = _SERVICE.get(kind, kind.upper())
            atts = [a for a in _as_list(data.get("attachments")) if isinstance(a, dict)]
            out["messages"].append({**base, "category": "messages", "direction": direction.replace("_", " "),
                                    "party": party, "text": text or "", "service": service,
                                    "attachments": ", ".join(str(a.get("name") or a.get("content_type") or "file")
                                                             for a in atts)})
            for a in atts:
                exported = a.get("exported_path")
                out["media"].append({**base, "category": "media", "name": a.get("name") or "(no name)",
                                     "kind": "MMS attachment", "mime": a.get("content_type") or "",
                                     "size": a.get("size"), "dimensions": "", "party": party, "file": exported or ""})
            continue

        if event.startswith("call_"):
            direction = data.get("type") or data.get("direction") or event[5:].replace("_recovered", "")
            number = data.get("number") or data.get("address")
            service = "Phone Link" if artifact == "win_phonelink" else (
                "Cellular" if data.get("service_provider") == "com.apple.Telephony" else
                str(data.get("call_type") or data.get("service_provider") or ""))
            out["calls"].append({**base, "category": "calls", "direction": str(direction),
                                 "party": names.who(device, number, data.get("name")) or "unknown",
                                 "duration": data.get("duration_seconds"), "service": service})
            continue

        if event.startswith("contact"):
            out["contacts"].append({**base, "category": "contacts", "name": data.get("name") or "",
                                    "numbers": ", ".join(str(n) for n in _as_list(data.get("numbers"))),
                                    "emails": ", ".join(str(e) for e in _as_list(data.get("emails"))),
                                    "organisation": " · ".join(str(x) for x in (data.get("company"),
                                                                                 data.get("job_title")) if x)})
            continue

        if event.startswith("photo"):
            export = data.get("export") if isinstance(data.get("export"), dict) else {}
            dims = f"{data['width']}×{data['height']}" if data.get("width") and data.get("height") else ""
            out["media"].append({**base, "category": "media", "name": data.get("name") or "(no name)",
                                 "kind": "Photo", "mime": data.get("mime_type") or "", "size": data.get("size"),
                                 "dimensions": dims, "party": "", "file": export.get("exported_path") or ""})
            continue

        if event.startswith("rcs_file_"):
            att = data.get("attachment") if isinstance(data.get("attachment"), dict) else {}
            direction = event.replace("rcs_file_", "").replace("_recovered", "")
            numbers = [data.get("from")] if direction == "received" else _as_list(data.get("to"))
            out["media"].append({**base, "category": "media", "name": att.get("name") or "(no name)",
                                 "kind": f"RCS file ({direction})", "mime": att.get("content_type") or "",
                                 "size": att.get("size"), "dimensions": "",
                                 "party": ", ".join(names.who(device, n) for n in numbers if n),
                                 "file": att.get("exported_path") or ""})
            continue

        if event == "airdrop_received":
            url = str(data.get("file_url") or "")
            sender = " ".join(str(x) for x in (data.get("sender_name"), data.get("sender_address")) if x)
            out["media"].append({**base, "category": "media", "name": url.rstrip("/").rsplit("/", 1)[-1] or "(file)",
                                 "kind": "AirDrop", "mime": "", "size": None, "dimensions": "", "party": sender,
                                 "file": ""})
            continue

        if event.startswith("phone_wallpaper"):
            export = data.get("export") if isinstance(data.get("export"), dict) else {}
            path = export.get("exported_path") or ""
            out["media"].append({**base, "category": "media",
                                 "name": path.rsplit("/", 1)[-1] if path else "(no image data)",
                                 "kind": "Wallpaper", "mime": _mime(path), "size": export.get("size"),
                                 "dimensions": "", "party": "", "file": path})
            continue

        if event in ("notification", "notification_recovered", "notification_iphone") or \
                event.startswith("phonelink_toast") and event != "phonelink_toast_handler_registered":
            if artifact == "mac_notifications" and event == "notification":
                continue  # the Mac's own notifications, not the phone's
            if event.startswith("phonelink_toast"):
                texts = [str(t) for t in _as_list(data.get("texts")) if t]
                app, title, text = "Phone Link", texts[0] if texts else "", " — ".join(texts[1:])
            else:
                app = data.get("app") or data.get("package") or ""
                title, text = data.get("title") or "", data.get("text") or data.get("body") or ""
            out["notifications"].append({**base, "category": "notifications", "app": str(app),
                                         "title": str(title), "text": str(text)})
            continue

        if event in ("phone_app_inventory", "iphone_app_inventory", "device_backup"):
            if event == "device_backup":
                apps = [{"package": a} for a in _as_list(data.get("installed_applications"))]
                listed_by = "iPhone backup (Info.plist)"
            else:
                apps = [a for a in _as_list(data.get("apps")) if isinstance(a, dict)]
                listed_by = "Phone Link (phoneapps.db)" if event == "phone_app_inventory" else "iPhone Mirroring"
            for a in apps:
                package = str(a.get("package") or a.get("bundle_id") or "")
                key = (device, package.lower() or str(a.get("name")))
                if key in seen_apps:
                    if listed_by not in seen_apps[key]["listed_by"]:
                        seen_apps[key]["listed_by"] += f", {listed_by}"
                    continue
                item = {**base, "category": "apps", "time": None, "name": str(a.get("name") or ""),
                        "package": package, "version": str(a.get("version") or ""), "listed_by": listed_by}
                seen_apps[key] = item
                out["apps"].append(item)
            if event != "device_backup":
                continue

        if is_phone:
            what = _what(event) + (" (file time)" if base["time_source"] == "filesystem" and base["time"] else "")
            out["connections"].append({**base, "category": "connections", "what": what,
                                       "text": r.get("message") or ""})

    for items in out.values():
        items.sort(key=lambda i: (i.get("time") is None, i.get("time") or "", i["phone"]))
    return out


def _mime(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp",
            "heic": "image/heic", "bmp": "image/bmp"}.get(ext, "")


def counts(items: dict[str, list[dict]]) -> dict[str | None, Counter]:
    """{device id (None: not linked): Counter(category -> items)}."""
    out: dict[str | None, Counter] = defaultdict(Counter)
    for key, rows in items.items():
        for row in rows:
            out[row["device"]][key] += 1
    return out


def recovered_deleted(items: dict[str, list[dict]]) -> int:
    return sum(1 for rows in items.values() for r in rows if r.get("status"))


def cell(item: dict, column: Column) -> Any:
    """The value shown for ``column``: times as 'YYYY-MM-DD HH:MM:SS', the rest as recorded."""
    value = item.get(column.key)
    if column.kind == "time":
        return format_time(value)
    if value is None:
        return ""
    return value


def format_time(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).replace("T", " ")
    return text.split("+")[0].split(".")[0].rstrip("Z")


OS_LABEL = {"ios": "iOS", "android": "Android", "windows": "Windows", "macos": "macOS", "linux": "Linux"}
ID_ORDER = ("udid", "serial", "imei", "meid", "iccid", "usb_serial", "bt_mac", "wifi_mac", "app_device_id",
            "phone_number", "account", "device_name", "model", "os_version")


def phone_facts(device: dict) -> dict[str, Any]:
    """The facts a summary shows for one phone, from its ``devices.json`` entry."""
    ids = sorted(device.get("identifiers") or [],
                 key=lambda i: ID_ORDER.index(i.get("kind")) if i.get("kind") in ID_ORDER else len(ID_ORDER))

    def first(kind: str) -> dict | None:
        return next((i for i in ids if i.get("kind") == kind), None)

    model = first("model")
    model_name = ""
    if model:
        summary = (model.get("decoded") or {}).get("summary") or ""
        model_name = f"{summary} ({model['value']})" if summary.startswith("Apple ") else model["value"]
    os_name = OS_LABEL.get(device.get("phone_os") or "", "")
    version = first("os_version")
    if version and os_name:
        os_name = f"{os_name} {version['value']}"
    primary = device.get("primary_identifier") or {}
    channels = [CHANNEL_LABEL.get(c["channel"], c["channel"]) for c in device.get("channels") or []]
    return {
        "id": device.get("id"), "label": device.get("label") or device.get("id"),
        "os": os_name or "Unknown OS", "model": model_name,
        "names": [i["value"] for i in ids if i.get("kind") == "device_name"],
        "numbers": [i.get("display") or i["value"] for i in ids if i.get("kind") == "phone_number"],
        "status": "Confirmed" if device.get("confirmed") else "Unconfirmed (weak identifiers only)",
        "primary_label": primary.get("label") or "", "primary_value": primary.get("display") or primary.get("value") or "",
        "primary_summary": primary.get("summary") or "",
        "computers": device.get("systems") or [], "users": device.get("users") or [],
        "first_seen": format_time(device.get("first_seen")), "last_seen": format_time(device.get("last_seen")),
        "connections": channels, "records": device.get("observation_count") or 0,
        "identifiers": [{"label": (i.get("decoded") or {}).get("label") or i["kind"],
                         "value": i.get("display") or i["value"], "raw": i["value"],
                         "meaning": (i.get("decoded") or {}).get("summary") or "",
                         "note": (i.get("decoded") or {}).get("note") or "",
                         "fields": (i.get("decoded") or {}).get("fields") or [],
                         "strength": i.get("strength") or "", "sources": i.get("sources") or []} for i in ids],
    }
