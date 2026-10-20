"""Build the device graph: which phones the evidence shows, and how we know.

Rules, chosen so every merge can be defended:

* Records are merged into one device only through STRONG identifiers (UDID,
  serial, IMEI, Bluetooth/Wi-Fi MAC, USB serial, vendor device ids) that a
  single record asserts together. Each merge keeps the record that caused it.
* Device names, models and OS versions never merge anything. Neither do
  phone numbers or accounts: a number moves with the SIM, not the handset.
  Shared weak or link identifiers are reported as *possible matches* for the
  examiner to judge.
* Records that only carry weak identifiers form *unconfirmed* devices.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .decode import apple_model, decode, device_family, display_value
from .model import Identifier, IdKind, Observation, Strength, phone_key
from .timeutil import iso


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra
        return True


@dataclass
class ChannelSummary:
    channel: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    observations: int = 0
    artifacts: set[str] = field(default_factory=set)

    def add(self, obs: Observation) -> None:
        self.observations += 1
        self.artifacts.add(obs.artifact)
        if obs.timestamp and obs.time_source == "artifact":
            if self.first_seen is None or obs.timestamp < self.first_seen:
                self.first_seen = obs.timestamp
            if self.last_seen is None or obs.timestamp > self.last_seen:
                self.last_seen = obs.timestamp


@dataclass
class Device:
    id: str
    confirmed: bool
    label: str = ""
    phone_os: str | None = None
    device_class: str | None = None
    identifiers: dict[Identifier, set[str]] = field(default_factory=dict)
    raw_values: dict[Identifier, set[str]] = field(default_factory=dict)
    channels: dict[str, ChannelSummary] = field(default_factory=dict)
    systems: set[str] = field(default_factory=set)
    users: set[str] = field(default_factory=set)
    events: Counter = field(default_factory=Counter)
    observation_ids: list[int] = field(default_factory=list)
    merge_reasons: list[dict[str, str]] = field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        idents = sorted(self.identifiers.items(),
                        key=lambda kv: (_STRENGTH_ORDER[kv[0].strength], kv[0].kind.value, kv[0].value))
        return {
            "id": self.id,
            "label": self.label,
            "confirmed": self.confirmed,
            "phone_os": self.phone_os,
            "device_class": self.device_class,
            "first_seen": iso(self.first_seen),
            "last_seen": iso(self.last_seen),
            "identifiers": [
                {"kind": i.kind.value, "value": i.value, "display": display_value(i.kind, i.value),
                 "strength": i.strength.value,
                 "as_recorded": sorted(self.raw_values.get(i, set()) - {i.value}),
                 "sources": sorted(src), "decoded": _decoded_dict(i, self.phone_os)} for i, src in idents
            ],
            "primary_identifier": _primary_dict(self),
            "channels": [
                {"channel": c.channel, "first_seen": iso(c.first_seen), "last_seen": iso(c.last_seen),
                 "observations": c.observations, "artifacts": sorted(c.artifacts)}
                for c in sorted(self.channels.values(), key=lambda c: c.channel)
            ],
            "systems": sorted(self.systems),
            "users": sorted(self.users),
            "events": dict(sorted(self.events.items())),
            "observation_count": len(self.observation_ids),
            "merge_reasons": self.merge_reasons,
        }


_STRENGTH_ORDER = {Strength.STRONG: 0, Strength.LINK: 1, Strength.WEAK: 2}


def _decoded_dict(i: Identifier, os: str | None) -> dict[str, Any]:
    d = decode(i.kind, i.value, os)
    return {"label": d.kind_label, "summary": d.summary, "fields": [list(f) for f in d.fields],
            "valid": d.valid, "note": d.note}


def _primary_dict(dev: Device) -> dict[str, Any] | None:
    i = primary_identifier(dev)
    if i is None:
        return None
    d = decode(i.kind, i.value, dev.phone_os)
    return {"kind": i.kind.value, "value": i.value, "display": display_value(i.kind, i.value),
            "label": d.kind_label, "summary": d.summary}


@dataclass
class DeviceGraph:
    devices: list[Device]
    assignment: dict[int, str]               # observation index -> device id
    possible_matches: list[dict[str, Any]]
    unattributed: list[int]                  # observations naming no device

    def device(self, device_id: str) -> Device | None:
        return next((d for d in self.devices if d.id == device_id), None)

    def candidate_groups(self) -> list[dict[str, Any]]:
        """Connected groups of devices linked by possible matches (never merged).

        Each group lists its members and the signals between them, strongest
        first, so an examiner can decide whether they are one phone.
        """
        adj: dict[str, set[str]] = defaultdict(set)
        for m in self.possible_matches:
            a, b = m["devices"]
            adj[a].add(b)
            adj[b].add(a)
        seen: set[str] = set()
        groups = []
        for start in sorted(adj):
            if start in seen:
                continue
            stack, members = [start], set()
            while stack:
                d = stack.pop()
                if d in members:
                    continue
                members.add(d)
                stack.extend(adj[d] - members)
            seen |= members
            links = [m for m in self.possible_matches if set(m["devices"]) <= members]
            groups.append({"devices": sorted(members), "links": links,
                           "strongest_link": max(m["independent_signals"] for m in links)})
        groups.sort(key=lambda g: (-g["strongest_link"], -len(g["devices"]), g["devices"]))
        return groups

    def to_dict(self) -> dict[str, Any]:
        return {
            "devices": [d.to_dict() for d in self.devices],
            "possible_matches": self.possible_matches,
            "candidate_groups": [{"devices": g["devices"], "strongest_link": g["strongest_link"]}
                                 for g in self.candidate_groups()],
            "unattributed_observations": len(self.unattributed),
        }


def _infer_os(dev: Device, votes: Counter) -> str | None:
    if votes:
        return votes.most_common(1)[0][0]
    for i in dev.identifiers:
        if i.kind == IdKind.UDID:
            return "ios"
        if i.kind == IdKind.MODEL and i.value.lower().startswith(("iphone", "ipad", "ipod")):
            return "ios"
    return None


def _infer_class(dev: Device, votes: Counter) -> str | None:
    # A device seen through a phone-only channel (backup, pairing, Phone Link...)
    # is a phone or tablet even if one artifact classed it otherwise.
    if votes:
        if "phone" in votes:
            return "phone"
        return votes.most_common(1)[0][0]
    if dev.phone_os:
        return "phone"
    return None


# Which identifier names a device best when it has no name: hardware ids first, app ids last.
_LABEL_ID_ORDER = [IdKind.UDID, IdKind.SERIAL, IdKind.IMEI, IdKind.MEID, IdKind.USB_SERIAL, IdKind.WIFI_MAC,
                   IdKind.BT_MAC, IdKind.APP_DEVICE_ID]


def primary_identifier(dev: Device) -> Identifier | None:
    """The strong identifier that best names the device (UDID before serial before MAC...)."""
    strong = sorted((i for i in dev.identifiers if i.strength == Strength.STRONG),
                    key=lambda i: (_LABEL_ID_ORDER.index(i.kind) if i.kind in _LABEL_ID_ORDER else 99, str(i)))
    return strong[0] if strong else None


def _noun(dev: Device) -> str:
    """What to call a device that has no name: 'iPhone', 'Android phone', 'Headphones'..."""
    cls = dev.device_class or "phone"
    if dev.phone_os == "ios":
        return {"phone": "iPhone", "tablet": "iPad"}.get(cls, "Apple device")
    if dev.phone_os == "android":
        return {"phone": "Android phone", "tablet": "Android tablet"}.get(cls, "Android device")
    return {"phone": "Phone", "tablet": "Tablet", "audio": "Audio device", "wearable": "Wearable",
            "computer": "Computer", "peripheral": "Peripheral"}.get(cls, "Device")


def _label(dev: Device, names: Counter) -> str:
    """A device's name: the name it reports, else its model or number, else what its identifier tells
    plus the identifier in full ("iPhone 16 family · UDID 00008140-0001541E3E46801C")."""
    if names:
        return names.most_common(1)[0][0]
    models = sorted(i.value for i in dev.identifiers if i.kind == IdKind.MODEL)
    if models:
        marketing = apple_model(models[0])
        return f"{marketing} ({models[0]})" if marketing else models[0]
    numbers = sorted(i.value for i in dev.identifiers if i.kind == IdKind.PHONE_NUMBER)
    if numbers:
        return f"Phone line {numbers[0]}"
    i = primary_identifier(dev)
    if i is not None:
        decoded = decode(i.kind, i.value, dev.phone_os)
        noun = device_family(i.kind.value, i.value) or _noun(dev)
        return f"{noun} · {decoded.kind_label} {display_value(i.kind, i.value)}"
    accounts = sorted(i.value for i in dev.identifiers if i.kind == IdKind.ACCOUNT)
    return f"Account {accounts[0]}" if accounts else "Unknown device"


def build_graph(observations: list[Observation]) -> DeviceGraph:
    uf = _UnionFind()
    reasons: dict[tuple[str, str], dict[str, str]] = {}

    for obs in observations:
        strong = sorted({i.key() for i in obs.device_ids if i.strength == Strength.STRONG})
        for k in strong:
            uf.add(k)
        for other in strong[1:]:
            a, b = uf.find(strong[0]), uf.find(other)
            if uf.union(strong[0], other):
                reasons[(a, b)] = {
                    "joined": f"{strong[0]} + {other}",
                    "artifact": obs.artifact,
                    "source_path": obs.provenance.source_path,
                    "locator": obs.provenance.locator,
                }

    # Group observations into components.
    comp_obs: dict[str, list[int]] = defaultdict(list)
    weak_obs: dict[str, list[int]] = defaultdict(list)
    unattributed: list[int] = []
    for idx, obs in enumerate(observations):
        strong = [i for i in obs.device_ids if i.strength == Strength.STRONG]
        if strong:
            comp_obs[uf.find(strong[0].key())].append(idx)
            continue
        if obs.device_ids:
            weak_obs[_weak_group_key(obs.device_ids)].append(idx)
        else:
            unattributed.append(idx)

    groups: list[tuple[bool, str, list[int]]] = (
        [(True, root, idxs) for root, idxs in comp_obs.items()]
        + [(False, key, idxs) for key, idxs in weak_obs.items()]
    )

    members_of: dict[str, set[str]] = defaultdict(set)
    for k in uf.parent:
        members_of[uf.find(k)].add(k)

    devices: list[Device] = []
    for confirmed, root, idxs in groups:
        dev = Device(id="", confirmed=confirmed)
        names: Counter = Counter()
        os_votes: Counter = Counter()
        class_votes: Counter = Counter()
        for idx in idxs:
            obs = observations[idx]
            dev.observation_ids.append(idx)
            for i in obs.device_ids:
                dev.identifiers.setdefault(i, set()).add(obs.artifact)
                dev.raw_values.setdefault(i, set()).add(i.raw)
                if i.kind == IdKind.DEVICE_NAME:
                    names[i.value] += 1
            if obs.phone_os:
                os_votes[obs.phone_os] += 1
            if obs.device_class:
                class_votes[obs.device_class] += 1
            dev.channels.setdefault(obs.channel.value, ChannelSummary(obs.channel.value)).add(obs)
            if obs.system:
                dev.systems.add(obs.system)
            if obs.user:
                dev.users.add(obs.user)
            dev.events[obs.event] += 1
            if obs.timestamp and obs.time_source == "artifact":
                if dev.first_seen is None or obs.timestamp < dev.first_seen:
                    dev.first_seen = obs.timestamp
                if dev.last_seen is None or obs.timestamp > dev.last_seen:
                    dev.last_seen = obs.timestamp
        if confirmed:
            members = members_of[root]
            dev.merge_reasons = [r for (a, b), r in reasons.items()
                                 if a in members or b in members]
        dev.phone_os = _infer_os(dev, os_votes)
        dev.device_class = _infer_class(dev, class_votes)
        dev.label = _label(dev, names)
        devices.append(dev)

    # Stable ordering: phones first, confirmed first, then earliest first-seen, then label.
    far = datetime.max.replace(tzinfo=None)
    devices.sort(key=lambda d: (d.device_class not in (None, "phone", "tablet"), not d.confirmed,
                                d.first_seen.replace(tzinfo=None) if d.first_seen else far,
                                d.label))
    assignment: dict[int, str] = {}
    for n, dev in enumerate(devices, 1):
        dev.id = f"DEV-{n:03d}"
        for idx in dev.observation_ids:
            assignment[idx] = dev.id

    return DeviceGraph(devices, assignment, _possible_matches(devices), unattributed)


def _weak_group_key(ids: list[Identifier]) -> str:
    """Group weak-only records by their most specific identifier."""
    order = [IdKind.PHONE_NUMBER, IdKind.ACCOUNT, IdKind.ICCID, IdKind.DEVICE_NAME,
             IdKind.MODEL, IdKind.OS_VERSION]
    for kind in order:
        vals = sorted(phone_key(i.value) if kind == IdKind.PHONE_NUMBER else i.value.lower()
                      for i in ids if i.kind == kind)
        if vals:
            return f"{kind.value}={vals[0]}"
    return "|".join(sorted(i.key() for i in ids))


# Signals derived from the same SIM or account are one piece of evidence, not several.
_SIGNAL_FAMILY = {"phone_number": "subscriber", "iccid": "subscriber", "account": "subscriber"}


def _possible_matches(devices: list[Device]) -> list[dict[str, Any]]:
    """Device pairs that may be one phone, with every signal that suggests it.

    Signals are grouped per pair; ``independent_signals`` counts distinct
    signal families (a shared name and adjacent MACs are two independent
    hints; a shared ICCID and phone number both describe one SIM).
    """
    by_pair: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for m in _raw_matches(devices):
        by_pair[tuple(m["devices"])].append({**m["shared"], "note": m["note"]})
    out = []
    for pair, signals in sorted(by_pair.items()):
        families = {_SIGNAL_FAMILY.get(s["kind"], s["kind"]) for s in signals}
        out.append({"devices": list(pair), "signals": signals, "independent_signals": len(families)})
    out.sort(key=lambda m: (-m["independent_signals"], m["devices"]))
    return out


def _raw_matches(devices: list[Device]) -> list[dict[str, Any]]:
    """Pairs of devices that share a weak or link identifier, plus MAC heuristics."""
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for d in devices:
        for i in d.identifiers:
            if i.strength == Strength.STRONG:
                continue
            if i.kind in (IdKind.MODEL, IdKind.OS_VERSION):
                continue  # far too common to suggest anything
            key = phone_key(i.value) if i.kind == IdKind.PHONE_NUMBER else i.value.lower()
            if key:
                index[(i.kind.value, key)].append(d.id)
    out = []
    for (kind, value), ids in sorted(index.items()):
        uniq = sorted(set(ids))
        if len(uniq) > 25:
            continue  # shared by so many records that it says nothing about any one phone
        for a_i in range(len(uniq)):
            for b_i in range(a_i + 1, len(uniq)):
                out.append({
                    "devices": [uniq[a_i], uniq[b_i]],
                    "shared": {"kind": kind, "value": value},
                    "note": ("same subscriber/account, possibly different handsets"
                             if kind in ("phone_number", "account", "iccid")
                             else "same device name; names are user-chosen and not unique"),
                })
    out.extend(_mac_matches(devices))
    return out


_MAC_ID_PREFIXES = ("wifi_ap_mac:",)


def _device_macs(d: Device) -> list[tuple[str, int]]:
    """(label, 48-bit value) for every MAC-address identifier of a device."""
    macs = []
    for i in d.identifiers:
        value = None
        if i.kind in (IdKind.BT_MAC, IdKind.WIFI_MAC):
            value, label = i.value, i.kind.value
        elif i.kind == IdKind.APP_DEVICE_ID and i.value.startswith(_MAC_ID_PREFIXES):
            label, value = i.value.split(":", 1)
        if value:
            hexonly = value.replace(":", "").replace("-", "")
            if len(hexonly) == 12 and all(c in "0123456789abcdefABCDEF" for c in hexonly):
                macs.append((f"{label}={value}", int(hexonly, 16)))
    return macs


def _mac_matches(devices: list[Device]) -> list[dict[str, Any]]:
    """Heuristic links between MAC addresses of different radios of one handset.

    * Adjacent addresses (same OUI, differing by 1-2): vendors, Apple among
      them, commonly assign consecutive addresses to a device's Wi-Fi and
      Bluetooth radios.
    * Same last three bytes, different first three: locally administered
      addresses (e.g. a hotspot's BSSID) are often derived from the base
      address by changing only the first byte(s).
    Never merges; reported for the examiner to verify.
    """
    out = []
    by_suffix: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    by_oui: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    for d in devices:
        for label, value in _device_macs(d):
            by_suffix[value & 0xFFFFFF].append((d.id, label, value))
            by_oui[value >> 24].append((d.id, label, value))
    seen = set()
    for entries in by_oui.values():
        entries.sort(key=lambda e: e[2])
        for i, (da, la, va) in enumerate(entries):
            for db, lb, vb in entries[i + 1:]:
                if vb - va > 2:
                    break
                if da != db and vb > va and (da, db, la, lb) not in seen:
                    seen.add((da, db, la, lb))
                    a, b = sorted((da, db))
                    out.append({"devices": [a, b], "shared": {"kind": "adjacent_mac", "value": f"{la} ~ {lb}"},
                                "note": f"MAC addresses differ by {vb - va} within the same vendor prefix; vendors "
                                        "often assign consecutive addresses to one handset's Wi-Fi and Bluetooth "
                                        "(heuristic)"})
    for entries in by_suffix.values():
        for i, (da, la, va) in enumerate(entries):
            for db, lb, vb in entries[i + 1:]:
                if da != db and (va >> 24) != (vb >> 24):
                    a, b = sorted((da, db))
                    out.append({"devices": [a, b], "shared": {"kind": "mac_suffix", "value": f"{la} ~ {lb}"},
                                "note": "last three bytes of the MAC addresses match; locally administered "
                                        "addresses such as hotspot BSSIDs are often derived from the device's "
                                        "base address (heuristic)"})
    return out
