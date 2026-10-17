"""iPhone Personal Hotspot / Instant Hotspot joined by a Mac.

* ``/Library/Preferences/com.apple.wifi.known-networks.plist`` (newer macOS):
  one entry per known network with the SSID, AddedAt / JoinedByUserAt /
  JoinedBySystemAt / LastDiscoveredAt / UpdatedAt and the BSSIDs seen.
* ``/Library/Preferences/SystemConfiguration/com.apple.airport.preferences.plist``
  (older macOS): ``KnownNetworks`` entries, with a ``PersonalHotspot`` flag.
* ``/private/var/db/dhcpclient/leases/*.plist``: the last DHCP lease per
  interface and network; for iPhone Personal Hotspot the router is
  172.20.10.1 and ``RouterHardwareAddress`` is the iPhone's hotspot MAC.

A hotspot's SSID is by default the iPhone's name. Only networks with a phone
signal (hotspot flag, iPhone hotspot subnet, phone-like SSID) are reported.
Known networks can also arrive through iCloud Keychain from other devices.
"""

from __future__ import annotations

import ipaddress

from ..model import Channel, IdKind, Observation, Validation, ident
from ..plugin import Context, Plugin, register
from ..timeutil import from_datetime
from .win_hotspot import ssid_phone_signal

KNOWN = "Library/Preferences/com.apple.wifi.known-networks.plist"
AIRPORT = "Library/Preferences/SystemConfiguration/com.apple.airport.preferences.plist"
LEASES = "private/var/db/dhcpclient/leases/*"  # e.g. "en0-1,c8:2a:14:12:34:56" (plist, no extension)
_IPHONE_NET = ipaddress.ip_network("172.20.10.0/28")


def _ssid(value) -> str | None:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value) if value else None


def _mac(value) -> str | None:
    if isinstance(value, (bytes, bytearray)) and len(value) == 6:
        return bytes(value).hex()
    if isinstance(value, str) and value.count(":") == 5:
        return "".join(p.zfill(2) for p in value.split(":")).lower()
    return None


@register
class MacHotspot(Plugin):
    id = "mac_phone_hotspots"
    name = "Phone hotspots joined (known networks, DHCP leases)"
    os = "macos"
    channel = Channel.WIFI_HOTSPOT
    description = __doc__ or ""
    paths = [KNOWN, AIRPORT, LEASES]
    references = ["https://github.com/ydkhatri/mac_apt/blob/master/plugins/airport_preferences.py",
                  "https://support.apple.com/en-us/105111 (Personal Hotspot)"]
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for path in ctx.files(KNOWN):
            with ctx.file_scope(path) as n:
                data = ctx.plist(path)
                if isinstance(data, dict):
                    for key, net in data.items():
                        if isinstance(net, dict):
                            n[0] += self._network(ctx, path, f"plist:$['{key}']", net,
                                                  _ssid(net.get("SSID")) or key.removeprefix("wifi.network.ssid."))
        for path in ctx.files(AIRPORT):
            with ctx.file_scope(path) as n:
                data = ctx.plist(path)
                known = data.get("KnownNetworks", {}) if isinstance(data, dict) else {}
                for key, net in (known.items() if isinstance(known, dict) else []):
                    if isinstance(net, dict):
                        n[0] += self._network(ctx, path, f"plist:$.KnownNetworks['{key}']", net,
                                              _ssid(net.get("SSIDString") or net.get("SSID")))
        for path in ctx.files(LEASES):
            head = ctx.read_bytes(path)[:200]
            if not head.startswith(b"bplist") and b"<plist" not in head:
                continue  # not a lease plist
            with ctx.file_scope(path) as n:
                lease = ctx.plist(path)
                if not isinstance(lease, dict):
                    continue
                router = lease.get("RouterIPAddress")
                try:
                    in_iphone_net = router is not None and ipaddress.ip_address(str(router)) in _IPHONE_NET
                except ValueError:
                    in_iphone_net = False
                ssid = _ssid(lease.get("SSID"))
                ssid_sig, os_hint = ssid_phone_signal(ssid)
                if not (in_iphone_net or ssid_sig):
                    continue
                mac = _mac(lease.get("RouterHardwareAddress"))
                ids = [ident(IdKind.APP_DEVICE_ID, f"wifi_ap_mac:{mac}") if mac else None,
                       ident(IdKind.DEVICE_NAME, ssid) if ssid else None]
                ctx.emit(Observation(
                    artifact=self.id, event="hotspot_dhcp_lease",
                    message=f"DHCP lease from phone hotspot {ssid or router} (router {router})",
                    channel=Channel.WIFI_HOTSPOT, timestamp=from_datetime(lease.get("LeaseStartDate")),
                    timestamp_desc="DHCP lease start", provenance=ctx.prov(path, "plist:$.LeaseStartDate"),
                    device_ids=ids, phone_os="ios" if in_iphone_net else os_hint, device_class="phone",
                    data={"ssid": ssid, "router_ip": router, "router_mac": mac, "ip": lease.get("IPAddress"),
                          "lease_length": lease.get("LeaseLength"),
                          "signals": (["iphone_hotspot_subnet"] if in_iphone_net else []) + (["ssid_name"] if ssid_sig else [])},
                ))
                n[0] += 1

    def _network(self, ctx, path, locator, net, ssid) -> int:
        osspec = net.get("__OSSpecific__") if isinstance(net.get("__OSSpecific__"), dict) else {}
        hotspot_flag = bool(net.get("PersonalHotspot") or osspec.get("PersonalHotspot"))
        ssid_sig, os_hint = ssid_phone_signal(ssid)
        if not (hotspot_flag or ssid_sig):
            return 0
        bssids = [_mac(b.get("BSSID")) for b in (net.get("BSSList") or []) if isinstance(b, dict)]
        bssids = [b for b in bssids if b]
        ids = [ident(IdKind.DEVICE_NAME, ssid)] + [ident(IdKind.APP_DEVICE_ID, f"wifi_ap_mac:{b}") for b in bssids]
        data = {"ssid": ssid, "personal_hotspot_flag": hotspot_flag, "add_reason": net.get("AddReason"),
                "bssids": bssids, "signals": (["personal_hotspot_flag"] if hotspot_flag else []) +
                (["ssid_name"] if ssid_sig else [])}
        n = 0
        for key, event, desc in (("AddedAt", "hotspot_added", "Network added"),
                                 ("JoinedByUserAt", "hotspot_joined_by_user", "Joined by user"),
                                 ("JoinedBySystemAt", "hotspot_joined_by_system", "Joined automatically"),
                                 ("LastConnected", "hotspot_last_connected", "Last connected"),
                                 ("LastDiscoveredAt", "hotspot_last_discovered", "Last discovered")):
            ts = from_datetime(net.get(key))
            if ts is None:
                continue
            ctx.emit(Observation(
                artifact=self.id, event=event, message=f"Phone hotspot \"{ssid}\": {desc}",
                channel=Channel.WIFI_HOTSPOT, timestamp=ts, timestamp_desc=desc,
                provenance=ctx.prov(path, f"{locator}.{key}"), device_ids=ids, data=data,
                phone_os="ios" if hotspot_flag else os_hint, device_class="phone",
            ))
            n += 1
        return n
