import struct
from datetime import datetime, timezone

from connected_phone_forensics.engine import scan
from connected_phone_forensics.plugins.win_hotspot import (
    _gateway_hardware,
    _hint_to_ssid,
    ssid_phone_signal,
)
from tests.fixtures.regf import REG_BINARY, REG_MULTI_SZ, HiveBuilder

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
    hexed = b"Jane's iPhone".hex()
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


def _profile(sw, guid, name, name_type, created, last=None, description=None, sig="0101SIG"):
    key = f"{NL}\\Profiles\\{{{guid}}}"
    sw.value(key, "ProfileName", name)
    if description:
        sw.value(key, "Description", description)
    sw.value(key, "NameType", name_type)
    sw.value(key, "DateCreated", _systemtime(created), REG_BINARY)
    if last:
        sw.value(key, "DateLastConnected", _systemtime(last), REG_BINARY)
    sw.value(f"{NL}\\Signatures\\Unmanaged\\{sig}", "ProfileGuid", f"{{{guid}}}")
    sw.value(f"{NL}\\Signatures\\Unmanaged\\{sig}", "DefaultGatewayMac", MAC, REG_BINARY)


def test_one_iphone_under_several_names_and_a_cable(tmp_path):
    # The same iPhone (one gateway address, the iPhone hotspot subnet) behind three Windows profiles:
    # its hotspot under the phone's name, again after the phone was renamed (Windows adds " 3" to a
    # name it has seen), and once by cable, which Windows calls "Local Area Connection".
    from connected_phone_forensics.casedata import CaseData

    _build(tmp_path / "C")
    cfg = tmp_path / "C" / "Windows" / "System32" / "config"
    sw = HiveBuilder()
    _profile(sw, "AAAA0000-0000-0000-0000-000000000001", "Jane's iPhone", 71, datetime(2025, 7, 11, 9, 0),
             datetime(2026, 10, 6, 17, 0))
    _profile(sw, "CCCC0000-0000-0000-0000-000000000003", "BLUE HOME DR5-5g 3", 71, datetime(2026, 10, 17, 18, 0),
             datetime(2026, 10, 17, 18, 30), description="BLUE HOME DR5-5g", sig="0103SIG")
    _profile(sw, "DDDD0000-0000-0000-0000-000000000004", "Local Area Connection", 1, datetime(2026, 5, 13, 9, 39),
             datetime(2026, 5, 13, 9, 39), sig="0104SIG")
    (cfg / "SOFTWARE").write_bytes(sw.build())

    result = scan(tmp_path, plugin_ids=["win_phone_hotspots"])
    cable = [o for o in result.observations if (o.data or {}).get("profile_name") == "Local Area Connection"]
    assert cable and all(o.channel.value == "usb_tethering" for o in cable)
    assert all("cable or Bluetooth" in o.message for o in cable)
    assert all(i.kind.value != "device_name" for o in cable for i in o.device_ids)  # Windows' name, not the phone's
    devs = result.graph.devices
    assert len(devs) == 1  # one gateway address: one phone
    names = {i.value for i in devs[0].identifiers if i.kind.value == "device_name"}
    assert names == {"Jane's iPhone", "BLUE HOME DR5-5g"}

    case = CaseData.from_result(result)
    [net] = case.hotspots
    assert net["access_points"] == 1 and net["tethered"] == 1
    assert net["names"] == ["Jane's iPhone", "BLUE HOME DR5-5g"]  # in the order they were first joined
    rows = {s["name"]: s for s in net["sessions"]}
    assert set(rows) == {"Jane's iPhone", "BLUE HOME DR5-5g", "Local Area Connection"}
    assert rows["Local Area Connection"]["link"].startswith("cable or Bluetooth")
    assert rows["BLUE HOME DR5-5g"]["first"].startswith("2026-10-17") and rows["BLUE HOME DR5-5g"]["profiles"] == \
        "BLUE HOME DR5-5g 3"
    assert rows["Jane's iPhone"]["dhcp"] == "phone 172.20.10.1 · computer 172.20.10.4"
    assert net["likely_phone"] == "iPhone" and case.phones == []  # known only through its hotspot
