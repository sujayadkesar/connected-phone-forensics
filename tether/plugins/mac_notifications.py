"""Notification Center database on a Mac, flagging notifications forwarded from the iPhone.

macOS 15+ shows iPhone notifications on the Mac once iPhone Mirroring is set
up. The database is ``~/Library/Group Containers/group.com.apple.usernoted/db2/db``
(macOS 15+; earlier ``/private/var/folders/*/*/0/com.apple.notificationcenter/db2/db``):
``app(app_id, identifier)`` and ``record(app_id, data, delivered_date, ...)``,
where ``data`` is a binary plist with ``req.titl``/``subt``/``body``.

How a forwarded iPhone notification is marked has not been publicly
documented. Tether flags a notification as *probably from the iPhone* when
its app is not installed on this Mac (no matching ``CFBundleIdentifier`` in
/Applications, /System/Applications or ~/Applications), which is an
inference, labelled as such. Times are Mac absolute seconds.
"""

from __future__ import annotations

from ..model import Channel, Observation, Validation
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..readers import plist as plistreader
from ..timeutil import from_cocoa

DB_NEW = "Library/Group Containers/group.com.apple.usernoted/db2/db"
DB_OLD = "private/var/folders/*/*/0/com.apple.notificationcenter/db2/db"


def installed_bundle_ids(ctx: Context) -> set[str]:
    ids: set[str] = set()
    patterns = ["Applications/*.app/Contents/Info.plist", "Applications/*/*.app/Contents/Info.plist",
                "System/Applications/*.app/Contents/Info.plist",
                "System/Applications/Utilities/*.app/Contents/Info.plist"]
    paths = [p for pat in patterns for p in ctx.files(pat)]
    paths += [p for _u, p in ctx.user_files("Applications/*.app/Contents/Info.plist")]
    for p in paths:
        try:
            info = plistreader.load(p)
        except Exception:  # noqa: BLE001 - app bundles are only a hint
            continue
        if isinstance(info, dict) and info.get("CFBundleIdentifier"):
            ids.add(str(info["CFBundleIdentifier"]).lower())
    return ids


@register
class MacNotifications(Plugin):
    id = "mac_notifications"
    name = "Notification Center (iPhone-forwarded notifications)"
    os = "macos"
    channel = Channel.CONTINUITY
    description = __doc__ or ""
    paths = [f"Users/*/{DB_NEW}", DB_OLD]
    references = ["https://github.com/ydkhatri/mac_apt/blob/master/plugins/notifications.py",
                  "https://9to5mac.com/2024/09/01/security-bite-apple-addresses-privacy-concerns-around-"
                  "notification-center-database-in-macos-sequoia/"]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        dbs = [(u, p) for u, p in ctx.user_files(DB_NEW)] + [(None, p) for p in ctx.files(DB_OLD)]
        if not dbs:
            return
        installed = installed_bundle_ids(ctx)
        for user, path in dbs:
            with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
                if not (db.schema.has("record", "data") and db.schema.has("app", "identifier")):
                    ctx.cover(path, UNRECOGNISED, note=f"unexpected schema {db.schema.fingerprint()}")
                    continue
                apps = {r["app_id"]: r["identifier"] for r in db.select("app", ["app_id", "identifier"])}
                for r in db.select("record", ["app_id", "data", "delivered_date", "request_date", "presented"]):
                    try:
                        payload = plistreader.loads(r["data"]) if r["data"] else {}
                    except Exception:  # noqa: BLE001
                        payload = {}
                    req = payload.get("req", {}) if isinstance(payload, dict) else {}
                    app = str(apps.get(r["app_id"]) or payload.get("app") or "")
                    bare = app.split(":", 1)[-1].lower()
                    from_iphone = bool(installed) and bare not in installed and not bare.startswith("com.apple.")
                    title, body = req.get("titl"), req.get("body")
                    data = {"app": app, "title": title, "subtitle": req.get("subt"), "body": body,
                            "probably_from_iphone": from_iphone,
                            "inference": "app not installed on this Mac" if from_iphone else None,
                            "installed_apps_checked": len(installed)}
                    ctx.emit(Observation(
                        artifact=self.id, event="notification_iphone" if from_iphone else "notification",
                        message=f"Notification {app}: {title or ''} — {(body or '')[:140]}"
                                + (" (probably from the iPhone)" if from_iphone else ""),
                        channel=Channel.CONTINUITY if from_iphone else Channel.OTHER,
                        timestamp=from_cocoa(r["delivered_date"]) or from_cocoa(r["request_date"]),
                        timestamp_desc="Notification delivered",
                        provenance=ctx.prov(path, f"table=record rowid={r['_rowid_']}"),
                        data=data, phone_os="ios" if from_iphone else None, user=user,
                    ))
                    n[0] += 1
