"""iPhone Mirroring on a Mac (macOS 15 Sequoia and later).

* ``com.apple.ScreenContinuity`` preferences (in the app's container, or
  ``~/Library/Preferences``): ``onenessPairedDeviceID`` identifies the iPhone
  paired for mirroring ("Oneness" is Apple's internal name). Reported up to
  macOS 15.3; later versions may store it differently.
* iPhone app placeholders: when mirroring is set up, the iPhone's app
  inventory is synced to the Mac as stub bundles (name, icon, version, no
  code). Sevco Security found them under ``~/Library/Daemon Containers/<UUID>/
  Data/Library/Caches/``; macOS 15.1 removed them from Spotlight but not from
  disk. Newer releases use a ``generated_placeholders.noindex`` folder.
  Tether looks for app stubs (Info.plist / iTunesMetadata.plist) in Daemon
  Containers and reports the inventory.

The placeholder layout is only partly documented publicly; this parser is
exploratory and reports where each item was found.
"""

from __future__ import annotations

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import Context, Plugin, register
from ..timeutil import from_unix

PREFS = ["Library/Containers/com.apple.ScreenContinuity/Data/Library/Preferences/com.apple.ScreenContinuity.plist",
         "Library/Preferences/com.apple.ScreenContinuity.plist"]
DAEMON = "Library/Daemon Containers"


@register
class MacIPhoneMirroring(Plugin):
    id = "mac_iphone_mirroring"
    name = "iPhone Mirroring pairing and synced iPhone app inventory"
    os = "macos"
    channel = Channel.CONTINUITY
    description = __doc__ or ""
    paths = [f"Users/*/{p}" for p in PREFS] + [f"Users/*/{DAEMON}/*/Data/Library/Caches/**"]
    references = ["https://www.sevcosecurity.com/iphone-mirroring-expose-employee-personal-information/",
                  "https://developer.apple.com/forums/thread/759206 (onenessPairedDeviceID)"]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for rel in PREFS:
            for user, path in ctx.user_files(rel):
                with ctx.file_scope(path) as n:
                    prefs = ctx.plist(path)
                    if not isinstance(prefs, dict):
                        continue
                    paired = prefs.get("onenessPairedDeviceID")
                    ids = [ident(IdKind.APP_DEVICE_ID, f"oneness:{str(paired).upper()}")] if paired else []
                    ctx.emit(Observation(
                        artifact=self.id, event="iphone_mirroring_configured",
                        message="iPhone Mirroring is set up on this Mac"
                                + (f" (paired device {paired})" if paired else ""),
                        channel=Channel.CONTINUITY, timestamp=from_unix(path.stat().st_mtime),
                        timestamp_desc="Preferences file modified (filesystem time)", time_source="filesystem",
                        provenance=ctx.prov(path, "plist:$.onenessPairedDeviceID" if paired else "plist"),
                        device_ids=ids, phone_os="ios", device_class="phone", user=user,
                        data={"onenessPairedDeviceID": paired, "keys": sorted(prefs)},
                    ))
                    n[0] += 1
        for user, base in ctx.user_dirs(f"{DAEMON}/*/Data/Library/Caches"):
            apps = []
            anchor = None
            for info in sorted(p for p in base.rglob("*") if p.name in ("Info.plist", "iTunesMetadata.plist")):
                try:
                    pl = ctx.plist(info)
                except Exception:  # noqa: BLE001 - not every plist is a stub
                    continue
                if not isinstance(pl, dict):
                    continue
                bid = pl.get("CFBundleIdentifier") or pl.get("softwareVersionBundleId")
                if not bid:
                    continue
                anchor = anchor or info
                apps.append({"bundle_id": bid,
                             "name": pl.get("CFBundleDisplayName") or pl.get("CFBundleName") or pl.get("itemName"),
                             "version": pl.get("CFBundleShortVersionString") or pl.get("bundleShortVersionString"),
                             "platforms": pl.get("CFBundleSupportedPlatforms"), "path": ctx.fs.rel(info),
                             "modified": from_unix(info.stat().st_mtime).isoformat()})
            ios_apps = [a for a in apps if not a["platforms"] or "iPhoneOS" in (a["platforms"] or [])]
            if not ios_apps or anchor is None:
                continue
            with ctx.file_scope(anchor) as n:
                ctx.emit(Observation(
                    artifact=self.id, event="iphone_app_inventory",
                    message=f"{len(ios_apps)} iPhone app placeholder(s) synced to this Mac for iPhone Mirroring",
                    channel=Channel.CONTINUITY, provenance=ctx.prov(anchor, f"dir={ctx.fs.rel(base)}"),
                    phone_os="ios", device_class="phone", user=user, data={"apps": ios_apps},
                ))
                n[0] += 1
