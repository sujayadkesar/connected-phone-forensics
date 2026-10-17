"""Phones paired with KDE Connect (Linux, Windows, macOS).

KDE Connect keeps its state in Qt's generic config location:
``~/.config/kdeconnect`` (Linux), ``%LOCALAPPDATA%\\kdeconnect`` (Windows),
``~/Library/Preferences/kdeconnect`` (macOS). ``trusted_devices`` is an INI
file with one group per paired device id holding its name, type
(phone/tablet/...), protocol version and the device's own certificate;
``config`` holds this computer's name. Each paired device also gets a
``<device id>/`` folder with per-plugin settings.

The paired phone's certificate is generated on the phone. In the Android
app (kdeconnect-android ``SslHelper.kt``) it is issued with notBefore = the
phone's local date minus one year and notAfter = that date plus ten years,
so a certificate with exactly that shape reveals the day the app created its
identity on the phone. This is derived from the source code and labelled as
such.
"""

from __future__ import annotations

import configparser
from datetime import datetime, timedelta

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..readers.x509 import parse_pem_or_der
from ..timeutil import from_unix

_REFS = [
    "https://invent.kde.org/network/kdeconnect-kde/-/blob/master/core/kdeconnectconfig.cpp",
    "https://invent.kde.org/network/kdeconnect-android/-/blob/master/src/main/java/org/kde/kdeconnect/"
    "helpers/security/SslHelper.kt",
]
_CLASSES = {"phone": "phone", "tablet": "tablet", "desktop": "computer", "laptop": "computer", "tv": "other"}


def _qsettings_value(raw: str) -> str:
    """Undo QSettings INI quoting/escaping for string values."""
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        v = v[1:-1]
    return v.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def _identity_created(not_before: datetime | None, not_after: datetime | None) -> datetime | None:
    """Day the Android app generated its certificate, if the validity has the 1y-back/10y-ahead shape."""
    if not (not_before and not_after):
        return None
    try:
        created = not_before.replace(year=not_before.year + 1)
    except ValueError:  # 29 February
        created = not_before + timedelta(days=365)
    try:
        expected_after = created.replace(year=created.year + 10)
    except ValueError:
        expected_after = created + timedelta(days=3652)
    # Both bounds are local midnights converted to UTC; allow for a DST shift.
    if abs((not_after - expected_after).total_seconds()) <= 2 * 3600:
        return created
    return None


def _parse(ctx: Context, base_dirs) -> None:
    for _user, base in base_dirs:
        trusted = ctx.fs.child(base, "trusted_devices")
        cfg_path = ctx.fs.child(base, "config")
        host_name = None
        if cfg_path and cfg_path.is_file():
            with ctx.file_scope(cfg_path):
                cp = configparser.ConfigParser(interpolation=None, strict=False)
                cp.optionxform = str
                cp.read_string(ctx.read_text(cfg_path))
                if cp.has_option("General", "name"):
                    host_name = _qsettings_value(cp["General"]["name"])
                ctx.cover(cfg_path, "parsed", 0, note=f"this computer's KDE Connect name: {host_name}")
        if not trusted or not trusted.is_file():
            continue
        with ctx.file_scope(trusted) as n:
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            cp.optionxform = str
            try:
                cp.read_string(ctx.read_text(trusted))
            except configparser.Error as exc:
                ctx.cover(trusted, UNRECOGNISED, note=f"not an INI file: {exc}")
                continue
            for dev_id in cp.sections():
                sec = cp[dev_id]
                name = _qsettings_value(sec.get("name", ""))
                dtype = _qsettings_value(sec.get("type", "")).lower() or None
                cert = parse_pem_or_der(_qsettings_value(sec.get("certificate", "")).encode())
                dev_dir = ctx.fs.child(base, dev_id)
                plugins = sorted(p.name for p in dev_dir.iterdir() if p.is_dir()) if dev_dir and dev_dir.is_dir() else []
                ids = [ident(IdKind.APP_DEVICE_ID, f"kdeconnect:{dev_id}"), ident(IdKind.DEVICE_NAME, name)]
                data = {
                    "device_id": dev_id, "name": name, "type": dtype,
                    "protocol_version": _qsettings_value(sec.get("protocolVersion", "")) or None,
                    "this_computer_name": host_name, "plugin_folders": plugins,
                    "certificate_not_before": cert.not_before.isoformat() if cert and cert.not_before else None,
                    "certificate_not_after": cert.not_after.isoformat() if cert and cert.not_after else None,
                    "certificate_cn": cert.subject_cn if cert else None,
                }
                device_class = _CLASSES.get(dtype or "", "other" if dtype else None)
                phone_os = "android" if device_class in ("phone", "tablet") else None
                label = f"KDE Connect {dtype or 'device'} \"{name or dev_id}\""
                created = _identity_created(cert.not_before, cert.not_after) if cert else None
                if created:
                    ctx.emit(Observation(
                        artifact=ctx.plugin.id, event="kdeconnect_identity_created",
                        message=f"{label}: KDE Connect identity created on the device",
                        channel=Channel.COMPANION_APP, timestamp=created,
                        timestamp_desc="Device certificate generation day (notBefore + 1 year; "
                                       "per kdeconnect-android source; day precision, device-local midnight)",
                        provenance=ctx.prov(trusted, f"ini:[{dev_id}].certificate"),
                        device_ids=ids, data=data, phone_os=phone_os, device_class=device_class,
                    ))
                ctx.emit(Observation(
                    artifact=ctx.plugin.id, event="kdeconnect_paired",
                    message=f"{label} is paired (trusted) with this computer",
                    channel=Channel.COMPANION_APP,
                    timestamp=from_unix(dev_dir.stat().st_mtime) if dev_dir else None,
                    timestamp_desc="Device config folder modified (filesystem time)" if dev_dir else "",
                    time_source="filesystem",
                    provenance=ctx.prov(trusted, f"ini:[{dev_id}]"),
                    device_ids=ids, data=data, phone_os=phone_os, device_class=device_class,
                ))
                n[0] += 1


@register
class LinuxKdeConnect(Plugin):
    id = "linux_kdeconnect"
    name = "KDE Connect paired devices"
    os = "linux"
    channel = Channel.COMPANION_APP
    description = __doc__ or ""
    paths = ["home/*/.config/kdeconnect/trusted_devices", "home/*/.config/kdeconnect/config"]
    references = _REFS
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        _parse(ctx, ctx.user_dirs(".config/kdeconnect"))


@register
class WindowsKdeConnect(Plugin):
    id = "win_kdeconnect"
    name = "KDE Connect paired devices"
    os = "windows"
    channel = Channel.COMPANION_APP
    description = __doc__ or ""
    paths = ["Users/*/AppData/Local/kdeconnect/trusted_devices", "Users/*/AppData/Local/kdeconnect/config"]
    references = _REFS
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        _parse(ctx, ctx.user_dirs("AppData/Local/kdeconnect"))


@register
class MacKdeConnect(Plugin):
    id = "mac_kdeconnect"
    name = "KDE Connect paired devices"
    os = "macos"
    channel = Channel.COMPANION_APP
    description = __doc__ or ""
    paths = ["Users/*/Library/Preferences/kdeconnect/trusted_devices",
             "Users/*/Library/Preferences/kdeconnect/config"]
    references = _REFS
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        _parse(ctx, ctx.user_dirs("Library/Preferences/kdeconnect"))

