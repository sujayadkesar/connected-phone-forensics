"""Phone notifications mirrored into the Windows notification database (wpndatabase.db).

Phone Link shows phone notifications and messages as Windows toasts. The
Windows Push Notification platform stores them per user in
``%LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db``:
``NotificationHandler`` (one row per app; Phone Link is
``Microsoft.YourPhone_8wekyb3d8bbwe!App``, ``CreatedTime`` marks its first
registration on this profile) and ``Notification`` (toast XML ``Payload``,
``ArrivalTime``/``ExpiryTime`` as FILETIME).

This is independent of Phone Link's own cache, so it can survive a cache
reset, and it also receives notifications in iPhone mode. Toasts expire
within days; expired or deleted ones are recovered from the WAL and the main
file. A toast does not name the phone it came from, so these records are not
attributed to a device; the user's Phone Link profiles are listed for context.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..model import Channel, Observation, Validation
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..timeutil import from_filetime, from_iso
from .win_phonelink import INDEXED, recovered_rows

WPN = "AppData/Local/Microsoft/Windows/Notifications/wpndatabase.db"
_HANDLERS = ("Microsoft.YourPhone_8wekyb3d8bbwe!", "MicrosoftWindows.CrossDevice_cw5n1h2txyewy!")


def toast_text(payload) -> tuple[list[str], str | None]:
    """Text lines and launch argument of a toast XML payload."""
    if isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload).decode("utf-8", errors="replace")
    if not payload:
        return [], None
    try:
        root = ET.fromstring(payload.strip().lstrip("﻿"))
    except ET.ParseError:
        return [re.sub(r"<[^>]+>", " ", payload).strip()[:500]], None
    texts = [t.text.strip() for t in root.iter("text") if t.text and t.text.strip()]
    return texts, root.get("launch")


@register
class WindowsPhoneNotificationsWPN(Plugin):
    id = "win_wpn_phonelink"
    name = "Phone Link toasts in the Windows notification database"
    os = "windows"
    channel = Channel.PHONE_LINK
    description = __doc__ or ""
    paths = [f"Users/*/{WPN}"]
    references = [
        "https://github.com/log2timeline/plaso/blob/main/plaso/parsers/sqlite_plugins/windows_push_notification.py",
        "https://github.com/AndrewRathbun/DFIRArtifactMuseum/tree/main/Windows/WindowsNotificationDB",
    ]
    validation = Validation.PUBLIC_SAMPLE

    def run(self, ctx: Context) -> None:
        for user, path in ctx.user_files(WPN):
            profiles = [d.name for _u, d in ctx.user_dirs(f"{INDEXED}/*") if _u == user]
            with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
                if not db.schema.has("NotificationHandler", "PrimaryId") or not db.schema.has("Notification", "Payload"):
                    ctx.cover(path, UNRECOGNISED, note=f"unexpected schema {db.schema.fingerprint()}")
                    continue
                handlers = {}
                for h in db.select("NotificationHandler", ["RecordId", "PrimaryId", "CreatedTime", "ModifiedTime"]):
                    if str(h["PrimaryId"] or "").startswith(_HANDLERS):
                        handlers[h["RecordId"]] = h["PrimaryId"]
                        ctx.emit(Observation(
                            artifact=self.id, event="phonelink_toast_handler_registered",
                            message=f"{h['PrimaryId']} registered for Windows notifications on this profile",
                            channel=Channel.PHONE_LINK, timestamp=from_iso(h["CreatedTime"]),
                            timestamp_desc="Notification handler created (first registration)",
                            provenance=ctx.prov(path, f"table=NotificationHandler rowid={h['_rowid_']}"),
                            data={"handler": h["PrimaryId"], "modified": h["ModifiedTime"],
                                  "phonelink_profiles": profiles}, user=user,
                        ))
                        n[0] += 1
                if not handlers:
                    continue
                cols = ["Id", "HandlerId", "Type", "Payload", "Tag", "Group", "ExpiryTime", "ArrivalTime"]
                ids = ",".join(str(int(k)) for k in handlers)
                for r in db.select("Notification", cols, where=f"HandlerId IN ({ids})"):
                    n[0] += self._toast(ctx, path, user, dict(r), r["_rowid_"], handlers, profiles)
                for status, row, ver in recovered_rows(db, path, "Notification"):
                    if row.get("HandlerId") in handlers:
                        n[0] += self._toast(ctx, path, user, row, ver.rowid, handlers, profiles, ver, status)

    def _toast(self, ctx, path, user, r, rowid, handlers, profiles, ver=None, status=None) -> int:
        texts, launch = toast_text(r.get("Payload"))
        data = {"handler": handlers.get(r.get("HandlerId")), "type": r.get("Type"), "tag": r.get("Tag"),
                "group": r.get("Group"), "texts": texts, "launch": launch,
                "expiry": from_filetime(r.get("ExpiryTime")).isoformat() if from_filetime(r.get("ExpiryTime")) else None,
                "phonelink_profiles": profiles}
        msg = f"Phone Link toast: {' — '.join(texts)[:200] or '(no text)'}"
        event = "phonelink_toast"
        loc = f"table=Notification rowid={rowid}"
        if status:
            event += "_recovered"
            data.update(recovered=status, recovered_from=ver.source)
            msg = f"[recovered: {status}] {msg}"
            loc += f" recovered_from={ver.source}" + (f" frame={ver.frame}" if ver.frame is not None else "")
        ctx.emit(Observation(
            artifact=self.id, event=event, message=msg, channel=Channel.PHONE_LINK,
            timestamp=from_filetime(r.get("ArrivalTime")), timestamp_desc="Toast arrival",
            provenance=ctx.prov(path, loc), data=data, user=user,
        ))
        return 1
