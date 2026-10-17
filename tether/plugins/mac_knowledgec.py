"""Devices syncing CoreDuet knowledge with a Mac (knowledgeC.db ZSYNCPEER).

``~/Library/Application Support/Knowledge/knowledgeC.db`` (and the system
copy under /private/var/db/CoreDuet/Knowledge) record app usage and other
events. Events synced from other devices on the same Apple Account point
through ``ZOBJECT.ZSOURCE -> ZSOURCE.ZDEVICEID`` to ``ZSYNCPEER`` rows
holding the peer's device id, model (e.g. iPhone15,2) and last-seen date.
Biome has replaced much of this on recent versions; knowledgeC is still
present and remains valuable on older Macs.
"""

from __future__ import annotations

from collections import defaultdict

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..timeutil import from_cocoa

USER_DB = "Library/Application Support/Knowledge/knowledgeC.db"
SYSTEM_DB = "private/var/db/CoreDuet/Knowledge/knowledgeC.db"


@register
class MacKnowledgeCPeers(Plugin):
    id = "mac_knowledgec_peers"
    name = "knowledgeC sync peers (ZSYNCPEER)"
    os = "macos"
    channel = Channel.ACCOUNT_SYNC
    description = __doc__ or ""
    paths = [f"Users/*/{USER_DB}", SYSTEM_DB]
    references = ["https://doubleblak.com/blogPost.php?k=syncpeer",
                  "http://www.mac4n6.com/blog/2018/8/5/knowledge-is-power-using-the-knowledgecdb-database-on-macos-"
                  "and-ios-to-determine-precise-user-and-application-usage"]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        dbs = [(u, p) for u, p in ctx.user_files(USER_DB)] + [(None, p) for p in ctx.files(SYSTEM_DB)]
        for user, path in dbs:
            with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
                if not db.schema.has("ZSYNCPEER", "ZDEVICEID"):
                    if db.schema.has("ZOBJECT"):
                        ctx.cover(path, "parsed", 0, note="no ZSYNCPEER table")
                        continue
                    ctx.cover(path, UNRECOGNISED, note=f"no ZSYNCPEER/ZOBJECT; schema {db.schema.fingerprint()}")
                    continue
                per_peer: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(lambda: [None, None, 0]))
                if db.schema.has("ZOBJECT", "ZSOURCE") and db.schema.has("ZSOURCE", "ZDEVICEID"):
                    q = ("SELECT s.ZDEVICEID AS dev, o.ZSTREAMNAME AS stream, MIN(o.ZSTARTDATE) AS first, "
                         "MAX(o.ZSTARTDATE) AS last, COUNT(*) AS n FROM ZOBJECT o JOIN ZSOURCE s ON o.ZSOURCE = s.Z_PK "
                         "WHERE s.ZDEVICEID IS NOT NULL GROUP BY s.ZDEVICEID, o.ZSTREAMNAME")
                    for r in db.rows(q):
                        per_peer[str(r["dev"]).upper()][r["stream"]] = [r["first"], r["last"], r["n"]]
                cols = ["ZDEVICEID", "ZMODEL", "ZNAME", "ZCLOUDID", "ZVERSION", "ZLASTSEENDATE", "ZME"]
                for r in db.select("ZSYNCPEER", cols):
                    if r["ZME"]:
                        continue
                    dev = str(r["ZDEVICEID"] or "").upper()
                    model = r["ZMODEL"]
                    is_phone = str(model or "").lower().startswith(("iphone", "ipad"))
                    ids = [ident(IdKind.APP_DEVICE_ID, f"knowledgec_peer:{dev}") if dev else None,
                           ident(IdKind.MODEL, model), ident(IdKind.DEVICE_NAME, r["ZNAME"])]
                    streams = {k: {"first": from_cocoa(v[0]).isoformat() if from_cocoa(v[0]) else None,
                                   "last": from_cocoa(v[1]).isoformat() if from_cocoa(v[1]) else None,
                                   "events": v[2]} for k, v in sorted(per_peer.get(dev, {}).items())}
                    data = {"device_id": dev, "model": model, "name": r["ZNAME"], "cloud_id": r["ZCLOUDID"],
                            "version": r["ZVERSION"], "streams": streams}
                    ctx.emit(Observation(
                        artifact=self.id, event="knowledgec_peer_last_seen",
                        message=f"{model or 'device'} {r['ZNAME'] or dev} syncs CoreDuet knowledge with this Mac"
                                + (f" ({sum(s['events'] for s in streams.values())} events)" if streams else ""),
                        channel=Channel.ACCOUNT_SYNC, timestamp=from_cocoa(r["ZLASTSEENDATE"]),
                        timestamp_desc="Sync peer last seen", provenance=ctx.prov(path, f"table=ZSYNCPEER rowid={r['_rowid_']}"),
                        device_ids=ids, data=data, phone_os="ios" if is_phone else None,
                        device_class=("tablet" if str(model).lower().startswith("ipad") else "phone") if is_phone else None,
                        user=user,
                    ))
                    n[0] += 1
