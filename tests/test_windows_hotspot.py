import struct
from datetime import datetime, timezone

from tests.fixtures.regf import REG_BINARY, REG_MULTI_SZ, HiveBuilder
from tether.engine import scan
from tether.plugins.win_hotspot import _gateway_hardware, _hint_to_ssid, ssid_phone_signal

UTC = timezone.utc
NL = r"Microsoft\Windows NT\CurrentVersion\NetworkList"
MAC = bytes.fromhex("6ac3e1112233")


def _systemtime(dt):
    return struct.pack("<8H", dt.year, dt.month, (dt.weekday() + 1) % 7, dt.day, dt.hour, dt.minute, dt.second, 0)


def test_helpers():
    assert ssid_phone_signal("Jane’s iPhone") == (True, "ios")
    assert ssid_phone_signal("AndroidAP_4821") == (True, "android")
    assert ssid_phone_signal("NETGEAR-5G") == (False, None)
    raw = bytes([172, 20, 10, 1]) + struct.pack("<I", 6) + MAC
    assert _gateway_hardware(raw) == ("172.20.10.1", MAC.hex())
    # "Jane's iPhone" hex with nibbles swapped per byte
    hexed = "Jane's iPhone".encode().hex()
    swapped = "".join(hexed[i + 1] + hexed[i] for i in range(0, len(hexed), 2))
    assert _hint_to_ssid(swapped) == "Jane's iPhone"


def _build(root):
    cfg = root / "Windows" / "System32" / "config"
    cfg.mkdir(parents=True)
    sw = HiveBuilder()
    sw.value(f"{NL}\\Profiles\\{{AAAA0000-0000-0000-0000-000000000001}}", "ProfileName", "Jane's iPhone")
    sw.value(f"{NL}\\Profiles\\{{AAAA0000-0000-0000-0000-000000000001}}", "NameType", 71)
    sw.value(f"{NL}\\Profiles\\{{AAAA0000-0000-0000-0000-000000000001}}", "DateCreated",
             _systemtime(datetime(2024, 7, 4, 9, 15, 0)), REG_BINARY)
    sw.value(f"{NL}\\Profiles\\{{AAAA0000-0000-0000-0000-000000000001}}", "DateLastConnected",
             _systemtime(datetime(2024, 12, 1, 18, 0, 0)), REG_BINARY)
    sw.value(f"{NL}\\Signatures\\Unmanaged\\0101SIG", "ProfileGuid", "{AAAA0000-0000-0000-0000-000000000001}")
    sw.value(f"{NL}\\Signatures\\Unmanaged\\0101SIG", "DefaultGatewayMac", MAC, REG_BINARY)
    sw.value(f"{NL}\\Profiles\\{{BBBB0000-0000-0000-0000-000000000002}}", "ProfileName", "NETGEAR-5G")
    sw.value(f"{NL}\\Profiles\\{{BBBB0000-0000-0000-0000-000000000002}}", "DateCreated",
             _systemtime(datetime(2023, 1, 1)), REG_BINARY)
    (cfg / "SOFTWARE").write_bytes(sw.build())
    sy = HiveBuilder()
    sy.value("Select", "Current", 1)
    tzk = r"ControlSet001\Control\TimeZoneInformation"
    sy.value(tzk, "TimeZoneKeyName", "Eastern Standard Time")
    sy.value(tzk, "Bias", 300)
    sy.value(tzk, "DaylightBias", 0xFFFFFFC4)
    sy.value(tzk, "StandardBias", 0)
    sy.value(tzk, "DaylightStart", bytes.fromhex("00000300020002000000000000000000"), REG_BINARY)
    sy.value(tzk, "StandardStart", bytes.fromhex("00000b00010002000000000000000000"), REG_BINARY)
    iface = r"ControlSet001\Services\Tcpip\Parameters\Interfaces\{1234}"
    sy.value(iface, "DhcpIPAddress", "172.20.10.4")
    sy.value(iface, "DhcpServer", "172.20.10.1")
    sy.value(iface, "DhcpDefaultGateway", ["172.20.10.1"], REG_MULTI_SZ)
    sy.value(iface, "DhcpGatewayHardware", bytes([172, 20, 10, 1]) + struct.pack("<I", 6) + MAC, REG_BINARY)
    sy.value(iface, "LeaseObtainedTime", 1733076000)
    (cfg / "SYSTEM").write_bytes(sy.build())
    (root / "Users" / "jdoe").mkdir(parents=True)
    (root / "Users" / "jdoe" / "NTUSER.DAT").write_bytes(b"regf")


def test_iphone_hotspot_networklist_and_dhcp_merge(tmp_path):
    _build(tmp_path / "C")
    result = scan(tmp_path, plugin_ids=["win_phone_hotspots"])
    events = sorted(o.event for o in result.observations)
    assert events == ["hotspot_dhcp_lease", "hotspot_first_connected", "hotspot_last_connected"]
    first = next(o for o in result.observations if o.event == "hotspot_first_connected")
    assert first.timestamp == datetime(2024, 7, 4, 13, 15, tzinfo=UTC)  # EDT = UTC-4
    last = next(o for o in result.observations if o.event == "hotspot_last_connected")
    assert last.timestamp == datetime(2024, 12, 1, 23, 0, tzinfo=UTC)  # EST = UTC-5
    assert first.phone_os == "ios"
    devs = result.graph.devices
    assert len(devs) == 1 and devs[0].confirmed and devs[0].label == "Jane's iPhone"
    lease = next(o for o in result.observations if o.event == "hotspot_dhcp_lease")
    assert "iphone_hotspot_subnet" in lease.data["signals"]
    assert any("not reported" in w.message for w in result.warnings)  # NETGEAR skipped, but said so
