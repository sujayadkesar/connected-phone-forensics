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
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import Context, Plugin, register
from ._windows import system_hive, windows_timezone
from .win_usb import APPLE_VID, MIXED_VENDORS, PHONE_VENDORS, is_ios_pid

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
        with system_hive(ctx) as opened:
            if opened is not None:
                tz = windows_timezone(opened[0], opened[2])
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
                    if vid == APPLE_VID and is_ios_pid(pid):
                        phone_os = "ios"
                    elif vid in PHONE_VENDORS or (vid in MIXED_VENDORS and "MTP" in suffix.upper()):
                        phone_os = "android"
                    else:
                        continue
                    start = _START.match(lines[i + 1]) if i + 1 < len(lines) else None
                    if not start:
                        continue
                    y, mo, d, hh, mm, ss, ms = (int(x) for x in start.groups())
                    local = datetime(y, mo, d, hh, mm, ss, ms * 1000)
                    ts = tz.to_utc(local) if tz else local.replace(tzinfo=timezone.utc)
                    serial = instance if "&" not in instance and not suffix else None
                    ids = [ident(IdKind.USB_SERIAL, serial)]
                    if phone_os == "ios" and serial:
                        ids.append(ident(IdKind.UDID, serial))
                    ctx.emit(Observation(
                        artifact=self.id, event="usb_driver_first_install",
                        message=f"Windows installed drivers for {'iPhone/iPad' if phone_os == 'ios' else 'Android phone'} "
                                f"{inst} ({m.group('how')})",
                        channel=Channel.USB, timestamp=ts,
                        timestamp_desc="Device install section start (local time converted with evidence timezone)"
                        if tz else "Device install section start (local time, timezone unknown)",
                        provenance=ctx.prov(log, f"line={i + 1}"),
                        device_ids=ids, phone_os=phone_os, device_class="phone",
                        data={"instance": inst, "trigger": m.group("how"), "vid": vid, "pid": pid,
                              "timezone": tz.describe() if tz else None},
                    ))
                    n[0] += 1
