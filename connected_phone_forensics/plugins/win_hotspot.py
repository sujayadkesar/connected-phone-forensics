"""Phone hotspots (tethering) a Windows computer connected to.

Windows keeps a NetworkList profile per network (SOFTWARE\\...\\NetworkList):
the network name (the SSID for Wi-Fi), when the profile was created (first
connection) and last connected, and through the matching signature the MAC
address of the default gateway. For a phone hotspot the SSID is usually the
phone's name ("Jane's iPhone") and the gateway is the phone itself.

Per-interface DHCP state (SYSTEM\\...\\Tcpip\\Parameters\\Interfaces) adds the
lease time and gateway: iPhone Personal Hotspot serves 172.20.10.0/28 with
gateway 172.20.10.1, a strong sign the computer was tethered to an iPhone.

Only networks with a phone signal (hotspot subnet, or a phone-like SSID) are
reported; the count of other networks is noted in coverage.

NetworkList dates are local-time SYSTEMTIMEs; they are converted to UTC with
the timezone recorded in the same image.
"""

from __future__ import annotations

import ipaddress
import re
import struct

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import Context, Plugin, register
from ..timeutil import from_systemtime, from_unix
from ._windows import (
    SOFTWARE_HIVE,
    SYSTEM_HIVE,
    key_locator,
    reg_dword,
    reg_text,
    software_hive,
    system_hive,
    windows_timezone,
)

NL = r"\Microsoft\Windows NT\CurrentVersion\NetworkList"
_NAME_TYPES = {6: "wired", 23: "mobile_broadband", 71: "wireless"}
_IPHONE_NET = ipaddress.ip_network("172.20.10.0/28")
_ANDROID_LEGACY_GW = ipaddress.ip_address("192.168.43.1")

_SSID_PATTERNS = [
    (re.compile(r"\biphone\b|’s iphone|'s iphone", re.I), "ios"),
    (re.compile(r"\bipad\b", re.I), "ios"),
    (re.compile(r"androidap|\bandroid\b", re.I), "android"),
    (re.compile(r"\b(galaxy|pixel|redmi|xiaomi|poco|oneplus|huawei|honor|moto(rola)?|oppo|vivo|"
                r"realme|nokia|nothing phone|fairphone|xperia|zenfone)\b", re.I), "android"),
    (re.compile(r"hotspot|tether", re.I), None),
]


def ssid_phone_signal(ssid: str | None) -> tuple[bool, str | None]:
    if not ssid:
        return False, None
    for rx, os_hint in _SSID_PATTERNS:
        if rx.search(ssid):
            return True, os_hint
    return False, None


def _mac(raw) -> str | None:
    if isinstance(raw, (bytes, bytearray)) and len(raw) >= 6:
        mac = bytes(raw[:6])
        return None if mac == b"\x00" * 6 else mac.hex()
    return None


def _gateway_hardware(raw) -> tuple[str | None, str | None]:
    """DhcpGatewayHardware: 4-byte IPv4, 4-byte length, then the MAC."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 14:
        return None, None
    ip = str(ipaddress.ip_address(bytes(raw[:4])))
    length = struct.unpack_from("<I", raw, 4)[0]
    mac = bytes(raw[8:8 + min(length, 6)])
    return ip, (mac.hex() if len(mac) == 6 else None)


def _hint_to_ssid(hint) -> str | None:
    """DhcpNetworkHint stores the SSID as hex with each byte's nibbles swapped."""
    text = reg_text(hint) if not isinstance(hint, str) else hint
    if not text or len(text) % 2:
        return None
    try:
        swapped = "".join(text[i + 1] + text[i] for i in range(0, len(text), 2))
        return bytes.fromhex(swapped).decode("utf-8", errors="replace")
    except ValueError:
        return None


@register
class WindowsHotspots(Plugin):
    id = "win_phone_hotspots"
    name = "Phone hotspots joined (NetworkList, DHCP)"
    os = "windows"
    channel = Channel.WIFI_HOTSPOT
    description = __doc__ or ""
    paths = [SOFTWARE_HIVE, SYSTEM_HIVE]
    references = [
        "https://github.com/keydet89/RegRipper3.0/blob/master/plugins/networklist.pl",
        "https://support.apple.com/en-us/105111 (Personal Hotspot)",
        "https://www.sans.org/posters/windows-forensic-analysis/ (Network History)",
    ]
    validation = Validation.PUBLIC_SAMPLE

    def run(self, ctx: Context) -> None:
        tz = None
        dhcp: list[dict] = []
        with system_hive(ctx) as opened:
            if opened is not None:
                hive, path, control_sets = opened
                tz = windows_timezone(hive, control_sets)
                with ctx.file_scope(path) as n:
                    dhcp = self._dhcp(ctx, hive, path, control_sets)
                    n[0] += self._emit_dhcp(ctx, dhcp)
        with software_hive(ctx) as opened:
            if opened is None:
                return
            hive, path = opened
            with ctx.file_scope(path) as n:
                n[0] += self._networklist(ctx, hive, path, tz, dhcp)

    # -- SYSTEM: DHCP leases ---------------------------------------------------

    def _dhcp(self, ctx, hive, path, control_sets) -> list[dict]:
        out = []
        cs = control_sets[0] if control_sets else "\\ControlSet001"
        for iface in hive.subkeys(f"{cs}\\Services\\Tcpip\\Parameters\\Interfaces"):
            for key in [iface, *hive.subkeys(iface.path)]:
                gw_list = key.get("DhcpDefaultGateway")
                gws = gw_list if isinstance(gw_list, list) else [gw_list] if gw_list else []
                gw_ip, gw_mac = _gateway_hardware(key.get("DhcpGatewayHardware"))
                ips = [g for g in [*gws, gw_ip, reg_text(key.get("DhcpServer"))] if g]
                signals, os_hint = [], None
                for ip in ips:
                    try:
                        addr = ipaddress.ip_address(str(ip).strip())
                    except ValueError:
                        continue
                    if addr in _IPHONE_NET:
                        signals.append("iphone_hotspot_subnet")
                        os_hint = "ios"
                    elif addr == _ANDROID_LEGACY_GW:
                        signals.append("android_legacy_hotspot_gateway")
                        os_hint = os_hint or "android"
                ssid = _hint_to_ssid(key.get("DhcpNetworkHint"))
                has_ssid_signal, ssid_os = ssid_phone_signal(ssid)
                if has_ssid_signal:
                    signals.append("ssid_name")
                    os_hint = os_hint or ssid_os
                if not signals:
                    continue
                out.append({
                    "key": key, "ssid": ssid, "gateway_ip": gw_ip or (gws[0] if gws else None),
                    "gateway_mac": gw_mac, "dhcp_ip": reg_text(key.get("DhcpIPAddress")),
                    "dhcp_server": reg_text(key.get("DhcpServer")),
                    "lease_obtained": from_unix(reg_dword(key.get("LeaseObtainedTime"))),
                    "signals": sorted(set(signals)), "phone_os": os_hint, "path": path,
                })
        return out

    def _emit_dhcp(self, ctx, leases) -> int:
        n = 0
        for d in leases:
            key = d["key"]
            ids = [ident(IdKind.APP_DEVICE_ID, f"wifi_ap_mac:{d['gateway_mac']}") if d["gateway_mac"] else None,
                   ident(IdKind.DEVICE_NAME, d["ssid"]) if d["ssid"] else None]
            data = {k: v for k, v in d.items() if k not in ("key", "path", "lease_obtained")}
            label = d["ssid"] or f"hotspot gateway {d['gateway_ip']}"
            ctx.emit(Observation(
                artifact=self.id, event="hotspot_dhcp_lease",
                message=f"DHCP lease from phone hotspot {label} (gateway {d['gateway_ip']}; "
                        f"{', '.join(d['signals'])})",
                channel=Channel.WIFI_HOTSPOT, timestamp=d["lease_obtained"],
                timestamp_desc="DHCP lease obtained",
                provenance=ctx.prov(d["path"], key_locator(ctx.fs.rel(d["path"]), key, "LeaseObtainedTime")),
                device_ids=ids, data=data, phone_os=d["phone_os"], device_class="phone",
            ))
            n += 1
        return n

    # -- SOFTWARE: NetworkList ----------------------------------------------------

    def _networklist(self, ctx, hive, path, tz, leases) -> int:
        gateways: dict[str, str] = {}
        for kind in ("Unmanaged", "Managed"):
            for sig in hive.subkeys(f"{NL}\\Signatures\\{kind}"):
                guid = (reg_text(sig.get("ProfileGuid")) or "").upper()
                mac = _mac(sig.get("DefaultGatewayMac"))
                if guid and mac:
                    gateways[guid] = mac
        lease_by_mac = {d["gateway_mac"]: d for d in leases if d["gateway_mac"]}
        hive_rel = ctx.fs.rel(path)
        emitted, skipped = 0, 0
        for prof in hive.subkeys(f"{NL}\\Profiles"):
            name = reg_text(prof.get("ProfileName"))
            name_type = reg_dword(prof.get("NameType"))
            # Windows names a second profile for the same SSID "<SSID> 2"; the Description keeps the
            # network's own name, which is what the phone broadcast.
            description = reg_text(prof.get("Description"))
            ssid = description if name_type == 71 and description else name
            mac = gateways.get(prof.name.upper())
            has_sig, os_hint = ssid_phone_signal(ssid)
            lease = lease_by_mac.get(mac) if mac else None
            signals = (["ssid_name"] if has_sig else []) + (lease["signals"] if lease else [])
            if not signals:
                skipped += 1
                continue
            os_hint = os_hint or (lease["phone_os"] if lease else None)
            ids = [ident(IdKind.DEVICE_NAME, ssid),
                   ident(IdKind.APP_DEVICE_ID, f"wifi_ap_mac:{mac}") if mac else None]
            data = {
                "profile_name": name, "description": description, "ssid": ssid,
                "profile_guid": prof.name, "name_type": _NAME_TYPES.get(name_type, name_type),
                "gateway_mac": mac, "signals": sorted(set(signals)),
                "timezone": tz.describe() if tz else "unknown: local time shown as UTC",
            }
            for value, event, desc in (("DateCreated", "hotspot_first_connected", "Profile created (first connection)"),
                                       ("DateLastConnected", "hotspot_last_connected", "Last connected")):
                local = from_systemtime(prof.get(value))
                if local is None:
                    continue
                ts = tz.to_utc(local) if tz else local
                ctx.emit(Observation(
                    artifact=self.id, event=event,
                    message=f"Connected to phone hotspot \"{ssid}\": {desc}"
                            + (f" (Windows profile \"{name}\")" if name != ssid else ""),
                    channel=Channel.WIFI_HOTSPOT, timestamp=ts,
                    timestamp_desc=f"{desc} (local time converted with evidence timezone)" if tz
                    else f"{desc} (local time, timezone unknown)",
                    provenance=ctx.prov(path, key_locator(hive_rel, prof, value)),
                    device_ids=ids, data=data, phone_os=os_hint, device_class="phone",
                ))
                emitted += 1
        if skipped:
            ctx.warn(f"{skipped} network profile(s) without phone-hotspot signals were not reported", path)
        return emitted
