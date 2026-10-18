"""First installation of phones in the Windows device-install log (setupapi.dev.log).

``C:\\Windows\\INF\\setupapi.dev.log`` records each driver installation in a
section such as::

    >>>  [Device Install (Hardware initiated) - USB\\VID_05AC&PID_12A8\\00008110000A1B2C3D4E801E]
    >>>  Section start 2024/03/15 08:00:05.123

The device instance path carries the USB serial (UDID for iPhones, adb
serial for Android), and the section start is the moment Windows first set
the device up: usually the first time that phone was plugged into this
computer. The time is local; it is converted with the image's timezone.
Rotated logs (``setupapi.dev.YYYYMMDD_hhmmss.log``) are read too.

A device instance can have several sections: later ones are driver updates
or reinstalls (e.g. ``UpdateDriverForPlugAndPlayDevices``). Only the
earliest section per instance, across all logs, is reported as the first
install; the others are reported as ``usb_driver_install``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import Context, Plugin, register
from ._windows import system_hive, windows_timezone
from .win_usb import APPLE_VID, MIXED_VENDORS, PHONE_VENDORS, is_ios_pid, phone_instance_index

_SECTION = re.compile(r"^>>>\s+\[Device Install \((?P<how>[^)]*)\)\s+-\s+(?P<inst>[^\]]+)\]")
_START = re.compile(r"^>>>\s+Section start (\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})")
_USB = re.compile(r"^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})([^\\]*)\\(.+)$", re.I)
_WPD = re.compile(r"^SWD\\WPDBUSENUM\\", re.I)


@register
class WindowsSetupapi(Plugin):
    id = "win_setupapi_phones"
    name = "First install of phones (setupapi.dev.log)"
    os = "windows"
    channel = Channel.USB
    description = __doc__ or ""
    paths = ["Windows/INF/setupapi.dev*.log"]
    references = [
        "https://learn.microsoft.com/en-us/windows-hardware/drivers/install/setupapi-text-logs",
        "https://www.sans.org/posters/windows-forensic-analysis/ (USB first connection)",
    ]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        logs = ctx.files("Windows/INF/setupapi.dev*.log")
        if not logs:
            return
        tz = None
        known: dict[str, tuple[str | None, str]] = {}
        with system_hive(ctx) as opened:
            if opened is not None:
                hive, _path, control_sets = opened
                tz = windows_timezone(hive, control_sets)
                known = phone_instance_index(hive, control_sets[0] if control_sets else "\\ControlSet001")
        sections: list[dict] = []
        for log in logs:
            with ctx.file_scope(log) as n:
                lines = ctx.read_text(log).splitlines()
                for i, line in enumerate(lines):
                    m = _SECTION.match(line)
                    if not m:
                        continue
                    inst = m.group("inst").strip()
                    u = _USB.match(inst)
                    if not u:
                        continue
                    vid, pid, suffix, instance = u.group(1).upper(), u.group(2).upper(), u.group(3), u.group(4)
                    # Classify with the device tree when the device is still in the registry;
                    # fall back to vendor/product ids when Windows has removed it.
                    device_class = "phone"
                    if inst.upper() in known:
                        phone_os, device_class = known[inst.upper()]
                        classified_by = "device registry (Enum\\USB)"
                    elif vid == APPLE_VID and is_ios_pid(pid):
                        phone_os, classified_by = "ios", "vendor/product id"
                    elif vid in PHONE_VENDORS or (vid in MIXED_VENDORS and "MTP" in suffix.upper()):
                        phone_os, classified_by = "android", "vendor id"
                    else:
                        continue
                    start = _START.match(lines[i + 1]) if i + 1 < len(lines) else None
                    if not start:
                        continue
                    y, mo, d, hh, mm, ss, ms = (int(x) for x in start.groups())
                    local = datetime(y, mo, d, hh, mm, ss, ms * 1000)
                    sections.append({
                        "ts": tz.to_utc(local) if tz else local.replace(tzinfo=timezone.utc),
                        "log": log, "line": i + 1, "inst": inst, "how": m.group("how"), "vid": vid, "pid": pid,
                        "suffix": suffix, "instance": instance, "phone_os": phone_os,
                        "device_class": device_class, "classified_by": classified_by,
                    })
                    n[0] += 1
        first: dict[str, dict] = {}
        for sec in sections:
            k = sec["inst"].upper()
            if k not in first or sec["ts"] < first[k]["ts"]:
                first[k] = sec
        for sec in sections:
            is_first = first[sec["inst"].upper()] is sec
            serial = sec["instance"] if "&" not in sec["instance"] and not sec["suffix"] else None
            ids = [ident(IdKind.USB_SERIAL, serial)]
            if sec["phone_os"] == "ios" and serial:
                ids.append(ident(IdKind.UDID, serial))
            what = "iPhone/iPad" if sec["phone_os"] == "ios" else f"Android {sec['device_class']}"
            ctx.emit(Observation(
                artifact=self.id, event="usb_driver_first_install" if is_first else "usb_driver_install",
                message=f"Windows {'first ' if is_first else ''}installed drivers for {what} {sec['inst']} "
                        f"({sec['how']})",
                channel=Channel.USB, timestamp=sec["ts"],
                timestamp_desc="Device install section start (local time converted with evidence timezone)"
                if tz else "Device install section start (local time, timezone unknown)",
                provenance=ctx.prov(sec["log"], f"line={sec['line']}"),
                device_ids=ids, phone_os=sec["phone_os"], device_class=sec["device_class"],
                data={"instance": sec["inst"], "trigger": sec["how"], "vid": sec["vid"], "pid": sec["pid"],
                      "classified_by": sec["classified_by"], "first_for_instance": is_first,
                      "timezone": tz.describe() if tz else None},
            ))
