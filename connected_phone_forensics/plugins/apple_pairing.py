"""iPhone/iPad pairing (lockdown) records on Windows and macOS.

When an iOS device is connected over USB and the user taps "Trust", the
computer stores a pair record named after the device UDID. The record holds
the certificates of the trust relationship, the host id, the device's
hardware Wi-Fi MAC address and, usually, an escrow keybag.

Why it matters: the record proves a specific handset was trusted by this
computer, yields the UDID and real Wi-Fi MAC, and its certificates carry
validity dates written by the host when the pairing was made. With the
escrow keybag, a pair record has also been used to perform logical
acquisitions of the device while it is locked but after first unlock.
"""

from __future__ import annotations

from pathlib import Path

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import UNRECOGNISED, Context, Plugin, register
from ..readers.x509 import parse_pem_or_der
from ..timeutil import from_unix

_REFS = [
    "https://github.com/libimobiledevice/libimobiledevice (userpref / pair record handling)",
    "https://github.com/libimobiledevice/usbmuxd (pair record storage: /var/db/lockdown on macOS, "
    "%ProgramData%\\Apple\\Lockdown on Windows)",
    "https://github.com/doronz88/pymobiledevice3 (pair record format)",
]

_CERT_KEYS = ("DeviceCertificate", "HostCertificate", "RootCertificate")


def _parse_pair_records(ctx: Context, files: list[Path]) -> None:
    for path in files:
        stem = path.stem
        if stem.lower() == "systemconfiguration":
            with ctx.file_scope(path) as n:
                cfg = ctx.plist(path)
                buid = cfg.get("SystemBUID") if isinstance(cfg, dict) else None
                if buid:
                    ctx.emit(Observation(
                        artifact=ctx.plugin.id, event="host_system_buid",
                        message=f"This computer's lockdown SystemBUID is {buid}",
                        channel=Channel.PAIRING, provenance=ctx.prov(path, "plist:$.SystemBUID"),
                        data={"system_buid": buid},
                    ))
                    n[0] += 1
            continue
        with ctx.file_scope(path) as n:
            rec = ctx.plist(path)
            if not isinstance(rec, dict) or not any(k in rec for k in ("HostID", *_CERT_KEYS)):
                ctx.cover(path, UNRECOGNISED, note="plist is not a pair record")
                continue
            ids = [ident(IdKind.UDID, stem), ident(IdKind.WIFI_MAC, rec.get("WiFiMACAddress"))]
            certs = {}
            for key in _CERT_KEYS:
                info = parse_pem_or_der(rec.get(key) or b"")
                if info:
                    certs[key] = info
            escrow = rec.get("EscrowBag")
            data = {
                "udid": stem,
                "wifi_mac": rec.get("WiFiMACAddress"),
                "host_id": rec.get("HostID"),
                "system_buid": rec.get("SystemBUID"),
                "has_escrow_bag": bool(escrow),
                "escrow_bag_bytes": len(escrow) if isinstance(escrow, (bytes, bytearray)) else 0,
                "has_host_private_key": bool(rec.get("HostPrivateKey")),
                "certificates": {
                    k: {"not_before": c.not_before.isoformat() if c.not_before else None,
                        "not_after": c.not_after.isoformat() if c.not_after else None,
                        "serial": c.serial, "subject_cn": c.subject_cn}
                    for k, c in certs.items()
                },
                "extra_keys": sorted(k for k in rec if k not in {
                    "WiFiMACAddress", "HostID", "SystemBUID", "EscrowBag", "HostPrivateKey",
                    "RootPrivateKey", *_CERT_KEYS}),
            }
            label = f"iOS device {stem}"
            emitted = 0
            dev_cert = certs.get("DeviceCertificate")
            if dev_cert and dev_cert.not_before:
                ctx.emit(Observation(
                    artifact=ctx.plugin.id, event="pairing_certificate_issued",
                    message=f"{label} trusted this computer (pair record certificate issued)",
                    channel=Channel.PAIRING, timestamp=dev_cert.not_before,
                    timestamp_desc="Pair record certificate notBefore (written by the host at pairing)",
                    provenance=ctx.prov(path, "plist:$.DeviceCertificate.validity.notBefore"),
                    device_ids=ids, data=data, phone_os="ios",
                ))
                emitted += 1
            mtime = from_unix(path.stat().st_mtime)
            ctx.emit(Observation(
                artifact=ctx.plugin.id, event="pair_record_modified",
                message=f"{label} pair record last written"
                        + (" (has escrow keybag)" if escrow else ""),
                channel=Channel.PAIRING, timestamp=mtime,
                timestamp_desc="Pair record file modified (filesystem time)",
                time_source="filesystem",
                provenance=ctx.prov(path, "file:mtime"), device_ids=ids, data=data, phone_os="ios",
            ))
            n[0] += emitted + 1


@register
class WindowsApplePairing(Plugin):
    id = "win_apple_pairing"
    name = "iOS pairing (lockdown) records"
    os = "windows"
    channel = Channel.PAIRING
    description = __doc__ or ""
    paths = ["ProgramData/Apple/Lockdown/*.plist"]
    references = _REFS
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        _parse_pair_records(ctx, ctx.files("ProgramData/Apple/Lockdown/*.plist"))


@register
class MacApplePairing(Plugin):
    id = "mac_apple_pairing"
    name = "iOS pairing (lockdown) records"
    os = "macos"
    channel = Channel.PAIRING
    description = __doc__ or ""
    paths = ["private/var/db/lockdown/*.plist"]
    references = _REFS
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        _parse_pair_records(ctx, ctx.files("private/var/db/lockdown/*.plist"))
