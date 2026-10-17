"""Core data model: identifiers, observations and provenance.

Every fact Tether reports is an :class:`Observation` that carries a
:class:`Provenance` pointing at the exact file and record it came from.
Observations name the phone they concern through :class:`Identifier`
values; the correlation stage (``tether.correlate``) merges identifiers into
devices using only identifiers strong enough to stand up to scrutiny.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class IdKind(str, Enum):
    """Kinds of identifier that can name a phone."""

    UDID = "udid"                      # Apple unique device identifier
    SERIAL = "serial"                  # hardware serial number
    IMEI = "imei"
    MEID = "meid"
    ICCID = "iccid"                    # SIM card, identifies the SIM not the handset
    PHONE_NUMBER = "phone_number"      # identifies the subscriber line, not the handset
    BT_MAC = "bt_mac"                  # Bluetooth device address
    WIFI_MAC = "wifi_mac"              # hardware Wi-Fi MAC (not a randomised one)
    USB_SERIAL = "usb_serial"          # iSerialNumber reported over USB
    APP_DEVICE_ID = "app_device_id"    # opaque per-application id, value is "<namespace>:<id>"
    DEVICE_NAME = "device_name"        # user-chosen name, e.g. "Jane's iPhone"
    MODEL = "model"                    # marketing or hardware model, e.g. "iPhone15,2"
    OS_VERSION = "os_version"
    ACCOUNT = "account"                # account identifier, value is "<service>:<id>"


class Strength(str, Enum):
    """How safely an identifier can be used to merge two records into one device."""

    STRONG = "strong"   # unique hardware or vendor-assigned id: merge automatically
    LINK = "link"       # identifies a subscriber or account: report as a link, never merge
    WEAK = "weak"       # descriptive only: never merge, may suggest a possible match


STRENGTH: dict[IdKind, Strength] = {
    IdKind.UDID: Strength.STRONG,
    IdKind.SERIAL: Strength.STRONG,
    IdKind.IMEI: Strength.STRONG,
    IdKind.MEID: Strength.STRONG,
    IdKind.BT_MAC: Strength.STRONG,
    IdKind.WIFI_MAC: Strength.STRONG,
    IdKind.USB_SERIAL: Strength.STRONG,
    IdKind.APP_DEVICE_ID: Strength.STRONG,
    IdKind.ICCID: Strength.LINK,
    IdKind.PHONE_NUMBER: Strength.LINK,
    IdKind.ACCOUNT: Strength.LINK,
    IdKind.DEVICE_NAME: Strength.WEAK,
    IdKind.MODEL: Strength.WEAK,
    IdKind.OS_VERSION: Strength.WEAK,
}


class Channel(str, Enum):
    """How the phone and the computer were connected, or how the data arrived."""

    USB = "usb"
    BLUETOOTH = "bluetooth"
    WIFI_HOTSPOT = "wifi_hotspot"
    USB_TETHERING = "usb_tethering"
    PAIRING = "pairing"                # trust / lockdown pairing record
    BACKUP = "backup"                  # local device backup
    PHONE_LINK = "phone_link"          # Microsoft Phone Link / Link to Windows
    CONTINUITY = "continuity"          # Apple Continuity (Handoff, Mirroring, relay...)
    ACCOUNT_SYNC = "account_sync"      # data synced through a shared cloud account
    MESSAGING_CLIENT = "messaging_client"
    COMPANION_APP = "companion_app"    # other vendor companion software
    DEVELOPER = "developer"            # adb, Xcode and similar developer tooling
    OTHER = "other"


class Validation(str, Enum):
    """How thoroughly a parser has been checked against real data.

    Shown next to every result so an examiner knows how much weight a parser
    has earned. Parsers start at SYNTHETIC and move up as real data is tested.
    """

    SYNTHETIC = "synthetic"            # tested only against fixtures built from public documentation
    PUBLIC_SAMPLE = "public_sample"    # tested against a public sample (e.g. DFIR Artifact Museum)
    GROUND_TRUTH = "ground_truth"      # tested against data generated with known actions on real devices


_MAC_RE = re.compile(r"^[0-9a-f]{12}$")


def normalize(kind: IdKind, value: str) -> str:
    """Return the canonical comparison form of an identifier value."""
    v = str(value).strip()
    if kind in (IdKind.BT_MAC, IdKind.WIFI_MAC):
        hexonly = re.sub(r"[^0-9a-fA-F]", "", v).lower()
        if _MAC_RE.match(hexonly):
            return ":".join(hexonly[i:i + 2] for i in range(0, 12, 2))
        return v.lower()
    if kind in (IdKind.UDID, IdKind.USB_SERIAL, IdKind.SERIAL):
        # New-style UDIDs ("00008030-001A35E22E88802E") are reported over USB
        # without the dash, so compare them dash-less and upper-case.
        return v.replace("-", "").upper()
    if kind in (IdKind.IMEI, IdKind.MEID, IdKind.ICCID):
        return re.sub(r"[^0-9A-Fa-f]", "", v).upper()
    if kind == IdKind.PHONE_NUMBER:
        digits = re.sub(r"[^0-9+]", "", v)
        return digits
    return v


@dataclass(frozen=True)
class Identifier:
    """An identifier in canonical form; ``raw`` keeps the value exactly as recorded."""

    kind: IdKind
    value: str
    raw: str = field(default="", compare=False, hash=False)

    def __post_init__(self) -> None:
        if not self.raw:
            object.__setattr__(self, "raw", str(self.value))
        object.__setattr__(self, "value", normalize(self.kind, self.value))

    @property
    def strength(self) -> Strength:
        return STRENGTH[self.kind]

    def key(self) -> str:
        return f"{self.kind.value}={self.value}"

    def __str__(self) -> str:
        return self.key()


def ident(kind: IdKind, value: Any) -> Identifier | None:
    """Build an Identifier, returning None for empty or placeholder values."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.hex()
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "unknown", "0", "00:00:00:00:00:00"}:
        return None
    return Identifier(kind, text)


@dataclass(frozen=True)
class Provenance:
    """Where a fact came from, precise enough to re-find it by hand."""

    source_path: str           # path inside the evidence, '/'-separated, relative to the system root
    locator: str               # record address: "table=x rowid=y", "key=...\\value", "plist:$.a.b"
    parser: str                # plugin id
    sha256: str | None = None  # hash of the source file as read

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "locator": self.locator,
            "parser": self.parser,
            "sha256": self.sha256,
        }


@dataclass
class Observation:
    """One fact: something a phone did, or something about a phone, at a point in time."""

    artifact: str                      # plugin id that produced it
    event: str                         # machine-readable event type, e.g. "bt_last_connected"
    message: str                       # one-line human-readable summary
    channel: Channel
    provenance: Provenance
    timestamp: datetime | None = None  # timezone-aware UTC
    timestamp_desc: str = ""           # what the timestamp means, e.g. "Last Connected"
    device_ids: list[Identifier] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    # "artifact": the time is recorded inside the artifact. "filesystem": it is
    # file metadata, which depends on how the evidence was acquired; such times
    # are shown in the timeline but not used for a device's first/last seen.
    time_source: str = "artifact"
    phone_os: str | None = None        # "ios" | "android" when the artifact implies it
    # What kind of device the record describes when the artifact says so:
    # "phone", "tablet", "computer", "audio", "wearable", "peripheral", "other".
    device_class: str | None = None
    user: str | None = None            # local account the artifact belongs to
    system: str | None = None          # label of the computer (system root) it was found on

    def __post_init__(self) -> None:
        self.device_ids = [i for i in self.device_ids if i is not None]
        if self.timestamp is not None and self.timestamp.tzinfo is None:
            raise ValueError(f"{self.artifact}: naive timestamp; convert to UTC first")
