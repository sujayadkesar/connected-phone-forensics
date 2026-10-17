"""Call history and AirDrop receipts on a Mac.

**CallHistory.storedata** (``~/Library/Application Support/CallHistoryDB``)
holds calls synced from the iPhone through iCloud and calls relayed through
Continuity (``ZSERVICE_PROVIDER`` ``com.apple.Telephony`` = cellular).
A row on the Mac does not prove the Mac handled the call. Newer stores carry
``ZORIGINATINGDEVICENAME``.

**AirDrop** receipts are recorded in
``~/Library/Preferences/com.apple.LaunchServices.QuarantineEventsV2``
(``LSQuarantineEvent`` rows with agent ``sharingd``), including the sender's
name. The row survives deletion of the file.
"""

from __future__ import annotations

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..timeutil import from_cocoa

CALLS = "Library/Application Support/CallHistoryDB/CallHistory.storedata"
QUARANTINE = "Library/Preferences/com.apple.LaunchServices.QuarantineEventsV2"
_CALL_TYPES = {0: "third_party", 1: "phone", 8: "facetime_video", 16: "facetime_audio"}


@register
class MacCallHistory(Plugin):
    id = "mac_callhistory"
    name = "Call history synced from iPhone (CallHistory.storedata)"
    os = "macos"
    channel = Channel.ACCOUNT_SYNC
    description = __doc__ or ""
    paths = [f"Users/*/{CALLS}"]
    references = ["https://github.com/ydkhatri/mac_apt (CALLHISTORY)", "https://github.com/abrignoni/iLEAPP (callHistory)"]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for user, path in ctx.user_files(CALLS):
            with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
                if not db.schema.has("ZCALLRECORD", "ZDATE"):
                    ctx.cover(path, UNRECOGNISED, note=f"no ZCALLRECORD; schema {db.schema.fingerprint()}")
                    continue
                cols = ["ZDATE", "ZDURATION", "ZADDRESS", "ZANSWERED", "ZORIGINATED", "ZCALLTYPE", "ZSERVICE_PROVIDER",
                        "ZNAME", "ZISO_COUNTRY_CODE", "ZORIGINATINGDEVICENAME", "ZUNIQUE_ID", "ZREAD"]
                for r in db.select("ZCALLRECORD", cols):
                    addr = r["ZADDRESS"]
                    if isinstance(addr, (bytes, bytearray)):
                        addr = bytes(addr).decode("utf-8", errors="replace")
                    direction = "outgoing" if r["ZORIGINATED"] else ("incoming" if r["ZANSWERED"] else "missed")
                    provider = r["ZSERVICE_PROVIDER"] or ""
                    cellular = provider == "com.apple.Telephony"
                    origin = r["ZORIGINATINGDEVICENAME"]
                    ids = [ident(IdKind.DEVICE_NAME, origin)] if origin else []
                    kind = _CALL_TYPES.get(r["ZCALLTYPE"], f"type_{r['ZCALLTYPE']}")
                    data = {"address": addr, "name": r["ZNAME"], "direction": direction, "call_type": kind,
                            "service_provider": provider, "duration_seconds": r["ZDURATION"],
                            "country": r["ZISO_COUNTRY_CODE"], "originating_device": origin,
                            "unique_id": r["ZUNIQUE_ID"],
                            "caveat": "may be iCloud-synced; does not prove the Mac handled the call"}
                    ctx.emit(Observation(
                        artifact=self.id, event=f"call_{direction}",
                        message=f"{'Cellular' if cellular else kind} call {direction} "
                                f"{'to' if direction == 'outgoing' else 'from'} {r['ZNAME'] or addr or 'unknown'}"
                                + (f" ({int(r['ZDURATION'] or 0)}s)" if r["ZDURATION"] else "")
                                + (f" on {origin}" if origin else ""),
                        channel=Channel.ACCOUNT_SYNC, timestamp=from_cocoa(r["ZDATE"]), timestamp_desc="Call start",
                        provenance=ctx.prov(path, f"table=ZCALLRECORD rowid={r['_rowid_']}"),
                        device_ids=ids, data=data, phone_os="ios" if cellular else None,
                        device_class="phone" if cellular else None, user=user,
                    ))
                    n[0] += 1


@register
class MacAirDrop(Plugin):
    id = "mac_airdrop"
    name = "AirDrop files received (QuarantineEventsV2)"
    os = "macos"
    channel = Channel.CONTINUITY
    description = __doc__ or ""
    paths = [f"Users/*/{QUARANTINE}"]
    references = ["https://github.com/ydkhatri/mac_apt (QUARANTINE)",
                  "https://www.sans.org/blog/ (AirDrop forensics; LSQuarantineSenderName)"]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for user, path in ctx.user_files(QUARANTINE):
            with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
                if not db.schema.has("LSQuarantineEvent", "LSQuarantineAgentName"):
                    ctx.cover(path, UNRECOGNISED, note=f"no LSQuarantineEvent; schema {db.schema.fingerprint()}")
                    continue
                cols = ["LSQuarantineEventIdentifier", "LSQuarantineTimeStamp", "LSQuarantineAgentBundleIdentifier",
                        "LSQuarantineAgentName", "LSQuarantineDataURLString", "LSQuarantineSenderName",
                        "LSQuarantineSenderAddress", "LSQuarantineOriginURLString"]
                for r in db.select("LSQuarantineEvent", cols,
                                   where="lower(LSQuarantineAgentName) = 'sharingd' "
                                         "OR LSQuarantineAgentBundleIdentifier LIKE '%sharingd%' "
                                         "OR LSQuarantineSenderName IS NOT NULL"):
                    sender = r["LSQuarantineSenderName"]
                    data = {"event_id": r["LSQuarantineEventIdentifier"], "sender_name": sender,
                            "sender_address": r["LSQuarantineSenderAddress"], "file_url": r["LSQuarantineDataURLString"],
                            "origin_url": r["LSQuarantineOriginURLString"], "agent": r["LSQuarantineAgentName"]}
                    ctx.emit(Observation(
                        artifact=self.id, event="airdrop_received",
                        message=f"AirDrop received from {sender or 'unknown sender'}: "
                                f"{(r['LSQuarantineDataURLString'] or '').rsplit('/', 1)[-1] or '(file)'}",
                        channel=Channel.CONTINUITY, timestamp=from_cocoa(r["LSQuarantineTimeStamp"]),
                        timestamp_desc="File quarantined on receipt",
                        provenance=ctx.prov(path, f"table=LSQuarantineEvent rowid={r['_rowid_']}"),
                        data=data, user=user,
                    ))
                    n[0] += 1
