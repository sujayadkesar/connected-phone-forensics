"""Phones known to Windows through the Microsoft account and cross-device settings (NTUSER.DAT).

* ``Software\\Microsoft\\Windows\\CurrentVersion\\TaskFlow\\DeviceCache\\<id>``:
  devices signed into the same Microsoft account that sync activities with
  this PC (Connected Devices Platform). Each key holds DeviceName,
  DeviceMake, DeviceModel and DeviceType (6 = iPhone, 8 = Android); the key
  name is the platform device id also used in ActivitiesCache.db.
* ``Software\\Microsoft\\Windows\\CurrentVersion\\Mobility``: cross-device settings.
  Windows 11 "Mobile devices" builds use values such as CrossDeviceEnabled and
  PhoneLinkEnabled; 2022 builds (verified on DFIR Artifact Museum hives) use
  subkeys like ``SmsSyncCapable``/``PhotoSyncCapable`` with an ``OptedIn``
  flag. These flags exist even when no phone was ever linked; they are
  reported as settings, not as evidence of a link.
* ``Software\\Microsoft\\Windows\\CurrentVersion\\Start\\Companions\\
  Microsoft.YourPhone_8wekyb3d8bbwe``: IsEnabled ("Show mobile device in Start").

Key last-written times approximate when these were last changed.
"""

from __future__ import annotations

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ._windows import key_locator, reg_dword, reg_text

_TYPES = {6: ("iPhone", "ios"), 8: ("Android", "android")}
_SETTINGS = [
    ("\\Software\\Microsoft\\Windows\\CurrentVersion\\Mobility", ("CrossDeviceEnabled", "PhoneLinkEnabled", "OptedIn")),
    ("\\Software\\Microsoft\\Windows\\CurrentVersion\\Start\\Companions\\Microsoft.YourPhone_8wekyb3d8bbwe", ("IsEnabled",)),
    ("\\Software\\Microsoft\\Windows\\CurrentVersion\\Notifications\\Settings\\Microsoft.YourPhone_8wekyb3d8bbwe!App",
     ("Enabled", "ShowInActionCenter")),
]


@register
class WindowsCrossDevice(Plugin):
    id = "win_crossdevice"
    name = "Microsoft-account phones (TaskFlow DeviceCache) and cross-device settings"
    os = "windows"
    channel = Channel.ACCOUNT_SYNC
    description = __doc__ or ""
    paths = ["Users/*/NTUSER.DAT"]
    references = [
        "https://github.com/kacos2000/WindowsTimeline",
        "https://cellebrite.com/en/exploring-the-windows-activity-timeline/ (DeviceCache, DeviceType)",
        "https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-connectivity "
        "(AllowPhonePCLinking / EnableMmx)",
    ]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for user, path in ctx.user_files("NTUSER.DAT"):
            if path.stat().st_size < 4096:
                ctx.cover(path, UNRECOGNISED, note="too small to be a registry hive")
                continue
            with ctx.file_scope(path) as n, ctx.hive(path) as hive:
                rel = ctx.fs.rel(path)
                for key in hive.subkeys("\\Software\\Microsoft\\Windows\\CurrentVersion\\TaskFlow\\DeviceCache"):
                    dtype = reg_dword(key.get("DeviceType"))
                    kind, phone_os = _TYPES.get(dtype, (f"type {dtype}", None))
                    name, make, model = (reg_text(key.get(v)) for v in ("DeviceName", "DeviceMake", "DeviceModel"))
                    ids = [ident(IdKind.APP_DEVICE_ID, f"cdp_platform_device:{key.name}"),
                           ident(IdKind.DEVICE_NAME, name), ident(IdKind.MODEL, model)]
                    ctx.emit(Observation(
                        artifact=self.id, event="ms_account_device",
                        message=f"{kind} {name or model or key.name} ({make or '?'} {model or ''}) shares this "
                                "user's Microsoft account (Connected Devices Platform)",
                        channel=Channel.ACCOUNT_SYNC, timestamp=key.last_written,
                        timestamp_desc="DeviceCache key last written",
                        provenance=ctx.prov(path, key_locator(rel, key)), device_ids=ids,
                        data={"platform_device_id": key.name, "device_type": dtype, "name": name, "make": make,
                              "model": model},
                        phone_os=phone_os, device_class="phone" if phone_os else None, user=user,
                    ))
                    n[0] += 1
                for key_path, values in _SETTINGS:
                    key = hive.key(key_path)
                    if key is None:
                        continue
                    settings = {v: key.get(v) for v in values if key.get(v) is not None}
                    for sub in hive.subkeys(key_path):  # 2022-era per-capability subkeys
                        for vname, val in sub.values.items():
                            settings[f"{sub.name}.{vname}"] = val.value
                    if not settings:
                        continue
                    ctx.emit(Observation(
                        artifact=self.id, event="crossdevice_settings",
                        message=f"Cross-device setting {key_path.rsplit(chr(92), 1)[-1]}: "
                                + ", ".join(f"{k}={v}" for k, v in settings.items()),
                        channel=Channel.PHONE_LINK, timestamp=key.last_written,
                        timestamp_desc="Settings key last written",
                        provenance=ctx.prov(path, key_locator(rel, key)),
                        data={"key": key_path, "settings": settings,
                              "interpretation": "settings/capability flags; not by themselves evidence of a linked phone"},
                        user=user,
                    ))
                    n[0] += 1
