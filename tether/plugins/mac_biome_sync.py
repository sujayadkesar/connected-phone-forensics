"""Apple devices that sync Biome data with this Mac (Biome sync.db).

``~/Library/Biome/sync/sync.db`` is the Biome sync engine's database
(biomesyncd). ``DevicePeer`` lists every device sharing the user's Apple
Account that exchanges Biome data with this Mac: its Biome site identifier
(the folder name of its data under ``Biome/streams/*/remote/``), platform
(2 = iPhone), OS build, IDS device identifier, name, and last sync time.
``AtomMergedLog`` records which streams (e.g. ``App.InFocus``) arrived from
which device and when; ``SyncSessionLog`` keeps the last 100 sync sessions.

Rows prove that the device synced with this Mac through the user's account,
over the local network/Bluetooth (Rapport) or CloudKit; not that it was
physically connected.
"""

from __future__ import annotations

from collections import defaultdict

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..timeutil import from_cocoa, from_unix

SYNC_DB = "Library/Biome/sync/sync.db"
# BMDevicePlatform, from biomesyncd (macOS 26)
PLATFORMS = {0: "unknown", 1: "iPad", 2: "iPhone", 3: "Mac desktop", 4: "Mac laptop", 5: "Apple TV",
             6: "Apple Watch", 7: "HomePod", 8: "Apple Vision"}
_CLASS = {1: "tablet", 2: "phone", 3: "computer", 4: "computer", 5: "other", 6: "wearable", 7: "other",
          8: "wearable"}


def build_string(value) -> tuple[str | None, bool]:
    """OS build from DevicePeer.model, undoing SQLite NUMERIC affinity.

    The column has NUMERIC affinity, so a build such as "22E240" is read as
    the number 22e240 and stored as the REAL 2.2e+241. Apple build strings
    have had a two-digit major version since 2013 (11A465 = iOS 7), which
    makes the conversion reversible. Returns (build, was_reconstructed).
    """
    import math

    if value is None or value == "":
        return None, False
    if isinstance(value, float) and not math.isfinite(value):
        return None, True  # e.g. "23E5216" overflows to infinity: the build is not recoverable
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value)), True
        if value > 0 and math.isfinite(value):
            exp = math.floor(math.log10(value))
            digits = f"{value:.15e}".split("e")[0].replace(".", "").rstrip("0") or "0"
            major = digits[:2].ljust(2, "0")
            rest = digits[2:]
            # value = major.rest × 10^exp  ->  "<major><rest>E<exp - 1 - len(rest)>"
            return f"{major}{rest}E{exp - 1 - len(rest)}", True
    if isinstance(value, int):
        return str(value), False
    return str(value), False


def peer_ids(device_identifier, ids_identifier, name, build) -> list:
    return [ident(IdKind.APP_DEVICE_ID, f"biome_site:{str(device_identifier).upper()}") if device_identifier else None,
            ident(IdKind.APP_DEVICE_ID, f"ids_device:{str(ids_identifier).upper()}") if ids_identifier else None,
            ident(IdKind.DEVICE_NAME, name),
            ident(IdKind.OS_VERSION, f"build {build}") if build not in (None, "") else None]


@register
class MacBiomeSync(Plugin):
    id = "mac_biome_sync"
    name = "Biome sync peers (sync.db)"
    os = "macos"
    channel = Channel.ACCOUNT_SYNC
    description = __doc__ or ""
    paths = [f"Users/*/{SYNC_DB}"]
    references = [
        "https://github.com/abrignoni/iLEAPP (biomeSync artifacts)",
        "https://belkasoft.com/biome-forensics-artifacts",
    ]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for user, path in ctx.user_files(SYNC_DB):
            with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
                if not db.schema.has("DevicePeer", "device_identifier"):
                    ctx.cover(path, UNRECOGNISED, note=f"no DevicePeer table; schema {db.schema.fingerprint()}")
                    continue
                streams: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: [None, None, 0]))
                if db.schema.has("AtomMergedLog", "owning_site_identifier", "stream"):
                    for r in db.select("AtomMergedLog", ["owning_site_identifier", "stream", "synced_at"]):
                        site = str(r["owning_site_identifier"] or "").upper()
                        t = from_cocoa(r["synced_at"])
                        s = streams[site][r["stream"]]
                        s[2] += 1
                        if t:
                            s[0] = t if s[0] is None or t < s[0] else s[0]
                            s[1] = t if s[1] is None or t > s[1] else s[1]
                last_msg = {}
                if db.schema.has("SyncMessageLog", "device_identifier", "timestamp"):
                    for r in db.select("SyncMessageLog", ["device_identifier", "timestamp", "transport"]):
                        last_msg[str(r["device_identifier"] or "").upper()] = from_cocoa(r["timestamp"])
                cols = ["device_identifier", "name", "platform", "model", "me", "last_sync_date",
                        "ids_device_identifier"]
                for r in db.select("DevicePeer", cols):
                    site = str(r["device_identifier"] or "").upper()
                    platform = r["platform"]
                    build, rebuilt = build_string(r["model"])
                    kind = PLATFORMS.get(platform, f"platform {platform}")
                    data = {
                        "device_identifier": site, "name": r["name"], "platform": kind, "os_build": build,
                        "os_build_reconstructed": rebuilt,
                        "os_build_raw": repr(r["model"]) if rebuilt else None,
                        "ids_device_identifier": r["ids_device_identifier"], "is_this_mac": bool(r["me"]),
                        "streams_received": {k: {"first": v[0].isoformat() if v[0] else None,
                                                 "last": v[1].isoformat() if v[1] else None, "events": v[2]}
                                             for k, v in sorted(streams.get(site, {}).items())},
                    }
                    if r["me"]:
                        ctx.cover(path, "parsed", 0, note=f"this Mac's Biome site id is {site}")
                        continue
                    ids = peer_ids(site, r["ids_device_identifier"], r["name"], build)
                    label = f"{kind} {r['name'] or site}"
                    dclass = _CLASS.get(platform)
                    phone_os = "ios" if platform in (1, 2) else None
                    for ts, event, desc, loc in (
                        (from_unix(r["last_sync_date"]), "biome_peer_last_sync", "Last Biome sync with this Mac",
                         "last_sync_date"),
                        (last_msg.get(site), "biome_peer_last_message", "Last sync message from device",
                         "SyncMessageLog"),
                    ):
                        if ts is None:
                            continue
                        ctx.emit(Observation(
                            artifact=self.id, event=event, message=f"{label} synced Biome data with this Mac",
                            channel=Channel.ACCOUNT_SYNC, timestamp=ts, timestamp_desc=desc,
                            provenance=ctx.prov(path, f"table=DevicePeer rowid={r['_rowid_']} field={loc}"),
                            device_ids=ids, data=data, phone_os=phone_os, device_class=dclass, user=user,
                        ))
                        n[0] += 1
                    for stream, (first, last, count) in sorted(streams.get(site, {}).items()):
                        for ts, which in ((first, "first"), (last, "last")):
                            if ts is None or (which == "last" and last == first):
                                continue
                            ctx.emit(Observation(
                                artifact=self.id, event=f"biome_stream_{which}_merged",
                                message=f"{label}: {stream} events merged into this Mac ({count} total)",
                                channel=Channel.ACCOUNT_SYNC, timestamp=ts,
                                timestamp_desc=f"{which.capitalize()} {stream} event merged (AtomMergedLog.synced_at)",
                                provenance=ctx.prov(path, f"table=AtomMergedLog owning_site_identifier={site} "
                                                          f"stream={stream}"),
                                device_ids=ids, data={"stream": stream, "events": count, "device_identifier": site},
                                phone_os=phone_os, device_class=dclass, user=user,
                            ))
                            n[0] += 1
