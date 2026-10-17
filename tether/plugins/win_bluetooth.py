"""Bluetooth devices paired with or seen by a Windows computer (BTHPORT).

``SYSTEM\\<ControlSet>\\Services\\BTHPORT\\Parameters\\Devices\\<address>``
holds one key per remote Bluetooth Classic device: its name (UTF-8 bytes),
Class of Device and last-seen / last-connected FILETIMEs. A link key under
``Parameters\\Keys\\<adapter>\\`` shows the device was actually paired, not
just discovered. The Class of Device identifies phones (major class 2), so
phones are separated from headsets and keyboards. The device address is
the phone's real Bluetooth MAC, which links it to other artifacts that
record it (e.g. Phone Link).
"""

from __future__ import annotations

import re

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import Context, Plugin, register
from ._windows import SYSTEM_HIVE, key_locator, reg_dword, reg_text, reg_time, system_hive

# Bluetooth Assigned Numbers, Baseband: major device classes.
_MAJOR = {0: "misc", 1: "computer", 2: "phone", 3: "network", 4: "audio_video",
          5: "peripheral", 6: "imaging", 7: "wearable", 8: "toy", 9: "health"}
_PHONE_MINOR = {0: "uncategorized", 1: "cellular", 2: "cordless", 3: "smartphone",
                4: "wired_modem", 5: "isdn"}
_CLASS_MAP = {"phone": "phone", "computer": "computer", "audio_video": "audio",
              "peripheral": "peripheral", "wearable": "wearable"}


def decode_cod(cod: int | None) -> dict:
    if cod is None:
        return {}
    major = (cod >> 8) & 0x1F
    minor = (cod >> 2) & 0x3F
    out = {"cod": f"0x{cod:06x}", "major_class": _MAJOR.get(major, f"reserved_{major}")}
    if major == 2:
        out["minor_class"] = _PHONE_MINOR.get(minor, f"phone_{minor}")
    elif major == 1 and minor == 7:
        out["minor_class"] = "wearable_computer"
    return out


# Bluetooth services a device offers to this PC, by 128-bit UUID (Assigned Numbers).
# A remote Hands-Free Audio Gateway, Message Access Server, Phonebook Access
# Server or Apple Notification Center Service is a phone acting as a phone:
# these are what Phone Link (calls, messages, contacts, iPhone notifications) uses.
PHONE_SERVICES = {
    "0000111f-0000-1000-8000-00805f9b34fb": "Hands-Free Audio Gateway (phone calls)",
    "00001112-0000-1000-8000-00805f9b34fb": "Headset Audio Gateway",
    "00001132-0000-1000-8000-00805f9b34fb": "Message Access Server (messages)",
    "0000112f-0000-1000-8000-00805f9b34fb": "Phonebook Access Server (contacts)",
    "7905f431-b5ce-4e99-a40f-4b1e122d00d0": "Apple Notification Center Service (iPhone notifications)",
    "89d3502b-0f36-433a-8ef4-c502ad55f8dc": "Apple Media Service",
}
_UUID = re.compile(r"\{([0-9a-fA-F-]{36})\}")
_INSTANCE_MAC = re.compile(r"(?:_|&|\b)([0-9A-Fa-f]{12})(?:_|&|$)")


def _service_entries(hive, cs: str) -> dict[str, list[dict]]:
    """Phone-type Bluetooth services per remote device address (from Enum\\BTHENUM / BTHLEDevice)."""
    out: dict[str, list[dict]] = {}
    for bus in ("BTHENUM", "BTHLEDevice"):
        for svc in hive.subkeys(f"{cs}\\Enum\\{bus}"):
            m = _UUID.search(svc.name)
            if not m or m.group(1).lower() not in PHONE_SERVICES:
                continue
            for inst in hive.subkeys(svc.path):
                macs = _INSTANCE_MAC.findall(inst.name) or _INSTANCE_MAC.findall(svc.name)
                if not macs:
                    continue
                mac = macs[-1].lower()
                out.setdefault(mac, []).append({
                    "service": PHONE_SERVICES[m.group(1).lower()], "uuid": m.group(1).lower(), "bus": bus,
                    "key": inst.path, "last_written": inst.last_written,
                    "friendly_name": reg_text(inst.get("FriendlyName")),
                })
    return out


def _mac(name: str) -> str | None:
    hexonly = "".join(ch for ch in name if ch in "0123456789abcdefABCDEF")
    return hexonly if len(hexonly) == 12 else None


@register
class WindowsBluetooth(Plugin):
    id = "win_bluetooth"
    name = "Bluetooth device history (BTHPORT)"
    os = "windows"
    channel = Channel.BLUETOOTH
    description = __doc__ or ""
    paths = [SYSTEM_HIVE]
    references = [
        "https://github.com/keydet89/RegRipper3.0/blob/master/plugins/bthport.pl",
        "https://www.bluetooth.com/specifications/assigned-numbers/ (Baseband: Class of Device)",
    ]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        with system_hive(ctx) as opened:
            if opened is None:
                return
            hive, path, control_sets = opened
            with ctx.file_scope(path) as n:
                # Link keys show which devices were paired.
                paired: dict[str, str] = {}
                for cs in control_sets[:1]:
                    for adapter in hive.subkeys(f"{cs}\\Services\\BTHPORT\\Parameters\\Keys"):
                        for dev in hive.subkeys(adapter.path):
                            mac = _mac(dev.name)
                            if mac:
                                paired[mac] = adapter.name
                        for value_name in adapter.values:
                            mac = _mac(value_name)
                            if mac:
                                paired[mac] = adapter.name
                    services = _service_entries(hive, cs)
                    for key in hive.subkeys(f"{cs}\\Services\\BTHPORT\\Parameters\\Devices"):
                        mac = _mac(key.name)
                        if not mac:
                            continue
                        svc = services.pop(mac.lower(), [])
                        name = reg_text(key.get("Name"))
                        cod = reg_dword(key.get("COD"))
                        cls = decode_cod(cod)
                        device_class = _CLASS_MAP.get(cls.get("major_class", ""))
                        if device_class is None and cls:
                            device_class = "other"
                        if svc:
                            device_class = "phone"  # it offers phone services to this PC
                        ids = [ident(IdKind.BT_MAC, mac), ident(IdKind.DEVICE_NAME, name)]
                        data = {
                            "address": mac, "name": name, **cls,
                            "paired": mac in paired, "adapter": paired.get(mac),
                            "phone_services": sorted({x["service"] for x in svc}),
                            "key_last_written": key.last_written.isoformat() if key.last_written else None,
                            "values": sorted(key.values),
                        }
                        what = cls.get("minor_class") or cls.get("major_class") or "device"
                        label = f"Bluetooth {what} {name or mac}"
                        hive_rel = ctx.fs.rel(path)
                        times = [
                            (reg_time(key.get("LastConnected")), "bt_last_connected",
                             "Last Connected", "LastConnected", "artifact"),
                            (reg_time(key.get("LastSeen")), "bt_last_seen", "Last Seen", "LastSeen", "artifact"),
                            (key.last_written, "bt_key_last_written", "Registry key last written",
                             None, "artifact"),
                        ]
                        emitted = False
                        for ts, event, desc, value, src in times:
                            if ts is None:
                                continue
                            ctx.emit(Observation(
                                artifact=self.id, event=event,
                                message=f"{label}{' (paired)' if mac in paired else ''}: {desc}",
                                channel=Channel.BLUETOOTH, timestamp=ts, timestamp_desc=desc,
                                time_source=src,
                                provenance=ctx.prov(path, key_locator(hive_rel, key, value)),
                                device_ids=ids, data=data, device_class=device_class,
                            ))
                            emitted = True
                        if not emitted:
                            ctx.emit(Observation(
                                artifact=self.id, event="bt_device",
                                message=f"{label}{' (paired)' if mac in paired else ''}",
                                channel=Channel.BLUETOOTH,
                                provenance=ctx.prov(path, key_locator(hive_rel, key)),
                                device_ids=ids, data=data, device_class=device_class,
                            ))
                        self._services(ctx, path, hive_rel, mac, name, svc)
                        n[0] += 1
                    # Services of devices not listed under BTHPORT (e.g. Bluetooth LE only).
                    for mac, svc in services.items():
                        self._services(ctx, path, ctx.fs.rel(path), mac, None, svc)
                        n[0] += 1

    def _services(self, ctx, path, hive_rel, mac, name, svc) -> None:
        for x in svc:
            ios = "notification center" in x["service"].lower() or "apple media" in x["service"].lower()
            ctx.emit(Observation(
                artifact=self.id, event="bt_phone_service",
                message=f"Bluetooth device {name or x['friendly_name'] or mac} offers {x['service']} to this PC",
                channel=Channel.BLUETOOTH, timestamp=x["last_written"],
                timestamp_desc="Service device key last written",
                provenance=ctx.prov(path, f"{hive_rel}:{x['key']}"),
                device_ids=[ident(IdKind.BT_MAC, mac), ident(IdKind.DEVICE_NAME, name or x["friendly_name"])],
                data={k: v for k, v in x.items() if k != "last_written"}, device_class="phone",
                phone_os="ios" if ios else None,
            ))
