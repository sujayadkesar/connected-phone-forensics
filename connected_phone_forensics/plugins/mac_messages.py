"""Messages on a Mac (chat.db): SMS/RCS relayed from the iPhone, and iMessages.

``~/Library/Messages/chat.db``. With Text Message Forwarding or Messages in
iCloud, SMS and RCS that travelled over the iPhone's cellular line appear on
the Mac with ``service`` 'SMS'/'RCS' (satellite: 'iMessageLite').
``destination_caller_id`` holds the address that received the message: for
SMS, the iPhone's own phone number. Those messages are attributed to that
number (a subscriber line, not a handset). iMessages are listed with the
Apple Account handle they used.

Dates are Mac absolute time in nanoseconds since 2001 (seconds before
macOS 10.13). Since macOS 13 the text is often only in ``attributedBody``
(a typedstream-serialised NSAttributedString), which is decoded here.

A message on the Mac can be iCloud-synced rather than relayed, so it does
not prove the iPhone was near the Mac; it does prove the content passed
through the user's devices.
"""

from __future__ import annotations

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..timeutil import from_cocoa

CHAT_DB = "Library/Messages/chat.db"
_PHONE_SERVICES = {"sms", "rcs", "imessagelite"}


def attributed_body_text(blob) -> str | None:
    """Extract the plain string from a typedstream NSAttributedString blob.

    The string follows the ``NSString`` class name: a type marker ``+``
    (0x2B), then the length (one byte; 0x81 + 2-byte LE; 0x82 + 4-byte LE),
    then UTF-8 bytes.
    """
    if not isinstance(blob, (bytes, bytearray)):
        return None
    data = bytes(blob)
    i = data.find(b"NSString")
    if i < 0:
        return None
    j = data.find(b"+", i + 8, i + 32)
    if j < 0:
        return None
    pos = j + 1
    if pos >= len(data):
        return None
    n = data[pos]
    pos += 1
    if n == 0x81:
        n = int.from_bytes(data[pos:pos + 2], "little")
        pos += 2
    elif n == 0x82:
        n = int.from_bytes(data[pos:pos + 4], "little")
        pos += 4
    text = data[pos:pos + n]
    if len(text) != n:
        return None
    return text.decode("utf-8", errors="replace")


@register
class MacMessages(Plugin):
    id = "mac_messages"
    name = "Messages chat.db (SMS relay, iMessage)"
    os = "macos"
    channel = Channel.CONTINUITY
    description = __doc__ or ""
    paths = [f"Users/*/{CHAT_DB}"]
    references = [
        "https://github.com/ydkhatri/mac_apt (IMESSAGE plugin)",
        "https://github.com/ReagentX/imessage-exporter/blob/develop/docs/tables/messages.md",
        "https://support.apple.com/en-us/102565 (Text Message Forwarding)",
    ]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for user, path in ctx.user_files(CHAT_DB):
            with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
                if not db.schema.has("message", "date"):
                    ctx.cover(path, UNRECOGNISED, note=f"no message table; schema {db.schema.fingerprint()}")
                    continue
                handles = {}
                if db.schema.has("handle", "id"):
                    for h in db.select("handle", ["id", "service"]):
                        handles[h["_rowid_"]] = h["id"]
                cols = ["guid", "text", "attributedBody", "handle_id", "service", "account", "date", "date_read",
                        "date_delivered", "is_from_me", "destination_caller_id", "service_center", "cache_has_attachments",
                        "date_edited", "date_retracted"]
                for r in db.select("message", cols):
                    service = str(r["service"] or "")
                    text = r["text"] or attributed_body_text(r["attributedBody"])
                    other = handles.get(r["handle_id"])
                    dest = r["destination_caller_id"]
                    phone_line = service.lower() in _PHONE_SERVICES
                    ids = []
                    if phone_line and dest and not str(dest).startswith(("e:", "E:")) and "@" not in str(dest):
                        ids.append(ident(IdKind.PHONE_NUMBER, dest))
                    account = str(r["account"] or "")
                    if account:
                        ids.append(ident(IdKind.ACCOUNT, f"apple:{account.split(':', 1)[-1].lower()}"))
                    direction = "sent" if r["is_from_me"] else "received"
                    party = f"{'to' if r['is_from_me'] else 'from'} {other or 'unknown'}"
                    data = {"guid": r["guid"], "service": service, "direction": direction, "other_party": other,
                            "text": text, "destination_caller_id": dest, "account": account or None,
                            "service_center": r["service_center"],
                            "has_attachments": bool(r["cache_has_attachments"]),
                            "read": from_cocoa(r["date_read"]).isoformat() if from_cocoa(r["date_read"]) else None,
                            "edited": from_cocoa(r["date_edited"]).isoformat() if from_cocoa(r["date_edited"]) else None,
                            "retracted": bool(from_cocoa(r["date_retracted"]))}
                    kind = service.lower() or "message"
                    ctx.emit(Observation(
                        artifact=self.id, event=f"{kind}_{direction}",
                        message=f"{service or 'Message'} {party}: {(text or '')[:140]}"
                                + (" (via the iPhone's cellular line)" if phone_line else ""),
                        channel=Channel.CONTINUITY if phone_line else Channel.ACCOUNT_SYNC,
                        timestamp=from_cocoa(r["date"]), timestamp_desc=f"Message {direction}",
                        provenance=ctx.prov(path, f"table=message rowid={r['_rowid_']}"),
                        device_ids=ids, data=data, phone_os="ios" if phone_line else None,
                        device_class="phone" if phone_line else None, user=user,
                    ))
                    n[0] += 1
