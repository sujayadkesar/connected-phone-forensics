"""Bluetooth devices paired with a Linux computer (BlueZ).

BlueZ stores one directory per remote device under
``/var/lib/bluetooth/<adapter address>/<device address>/`` with an ``info``
INI file: name, Class of Device, trust/block state, supported technologies,
link keys (``[LinkKey]`` for Classic, ``[LongTermKey]`` for LE) and a
``[DeviceID]`` vendor. Devices only discovered, never paired, appear in
``<adapter>/cache/<address>``. The files hold no timestamps, so their
filesystem modification times are reported and labelled as such.
"""

from __future__ import annotations

import configparser
import re

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..timeutil import from_unix
from .win_bluetooth import _CLASS_MAP, decode_cod

_MAC_DIR = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
# Bluetooth SIG company identifiers (DeviceID Source=1) and USB vendor ids (Source=2).
_SIG_VENDORS = {76: "Apple", 117: "Samsung", 224: "Google", 911: "Xiaomi", 0x027D: "Huawei"}
_USB_VENDORS = {0x05AC: "Apple", 0x04E8: "Samsung", 0x18D1: "Google"}


def _ini(text: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # keep key case
    cp.read_string(text)
    return cp


@register
class LinuxBluetooth(Plugin):
    id = "linux_bluetooth"
    name = "Bluetooth pairings (BlueZ)"
    os = "linux"
    channel = Channel.BLUETOOTH
    description = __doc__ or ""
    paths = ["var/lib/bluetooth/*/*/info", "var/lib/bluetooth/*/cache/*"]
    references = [
        "https://github.com/bluez/bluez/blob/master/doc/settings-storage.txt",
        "https://www.bluetooth.com/specifications/assigned-numbers/ (company identifiers)",
    ]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        paired = set()
        for info in ctx.files("var/lib/bluetooth/*/*/info"):
            adapter, device = info.parent.parent.name, info.parent.name
            if not (_MAC_DIR.match(adapter) and _MAC_DIR.match(device)):
                continue
            paired.add(device.upper())
            with ctx.file_scope(info) as n:
                try:
                    cp = _ini(ctx.read_text(info))
                except configparser.Error as exc:
                    ctx.cover(info, UNRECOGNISED, note=f"not an INI file: {exc}")
                    continue
                n[0] += self._emit(ctx, info, cp, adapter, device, paired=True)
        for cache in ctx.files("var/lib/bluetooth/*/cache/*"):
            device = cache.name
            if not _MAC_DIR.match(device) or device.upper() in paired:
                continue
            with ctx.file_scope(cache) as n:
                try:
                    cp = _ini(ctx.read_text(cache))
                except configparser.Error as exc:
                    ctx.cover(cache, UNRECOGNISED, note=f"not an INI file: {exc}")
                    continue
                n[0] += self._emit(ctx, cache, cp, cache.parent.parent.name, device, paired=False)

    def _emit(self, ctx, path, cp, adapter, device, paired) -> int:
        g = cp["General"] if cp.has_section("General") else {}
        name = g.get("Name") or g.get("Alias")
        try:
            cod = int(g.get("Class", ""), 16) if g.get("Class") else None
        except ValueError:
            cod = None
        cls = decode_cod(cod)
        vendor = None
        if cp.has_section("DeviceID"):
            try:
                src = int(cp["DeviceID"].get("Source", "0"))
                vid = int(cp["DeviceID"].get("Vendor", "0"))
                vendor = (_SIG_VENDORS if src == 1 else _USB_VENDORS if src == 2 else {}).get(vid)
            except ValueError:
                pass
        device_class = _CLASS_MAP.get(cls.get("major_class", ""), "other" if cls else None)
        phone_os = None
        if device_class == "phone" and vendor == "Apple":
            phone_os = "ios"
        elif device_class == "phone" and vendor in ("Samsung", "Google", "Xiaomi", "Huawei"):
            phone_os = "android"
        data = {
            "adapter": adapter, "address": device, "name": name, "alias": g.get("Alias"),
            **cls, "vendor": vendor, "paired": paired,
            "trusted": g.get("Trusted"), "blocked": g.get("Blocked"),
            "technologies": g.get("SupportedTechnologies"),
            "has_link_key": cp.has_section("LinkKey"), "has_le_key": cp.has_section("LongTermKey"),
            "services": [s for s in (g.get("Services") or "").split(";") if s],
        }
        what = cls.get("minor_class") or cls.get("major_class") or "device"
        ctx.emit(Observation(
            artifact=self.id, event="bt_pairing_record" if paired else "bt_cached_device",
            message=f"Bluetooth {what} {name or device} {'paired' if paired else 'seen (not paired)'} "
                    f"with adapter {adapter}",
            channel=Channel.BLUETOOTH, timestamp=from_unix(path.stat().st_mtime),
            timestamp_desc="BlueZ record file modified (filesystem time)", time_source="filesystem",
            provenance=ctx.prov(path, "file"),
            device_ids=[ident(IdKind.BT_MAC, device), ident(IdKind.DEVICE_NAME, name)],
            data=data, phone_os=phone_os, device_class=device_class,
        ))
        return 1
