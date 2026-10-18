from datetime import timezone

from connected_phone_forensics.engine import scan
from connected_phone_forensics.plugins.win_bluetooth import decode_cod
from connected_phone_forensics.timeutil import from_filetime
from tests.fixtures.regf import REG_BINARY, REG_QWORD, HiveBuilder

UTC = timezone.utc
DEV = r"ControlSet001\Services\BTHPORT\Parameters\Devices"
KEYS = r"ControlSet001\Services\BTHPORT\Parameters\Keys"


def test_decode_cod():
    assert decode_cod(0x7A020C) == {"cod": "0x7a020c", "major_class": "phone", "minor_class": "smartphone"}
    assert decode_cod(0x240404)["major_class"] == "audio_video"
    assert decode_cod(None) == {}


def _system_hive(path):
    h = HiveBuilder()
    h.value("Select", "Current", 1)
    h.key(f"{DEV}\\a4c337112233", last_written=133_500_000_000_000_000)
    h.value(f"{DEV}\\a4c337112233", "Name", b"Jane's iPhone\x00", REG_BINARY)
    h.value(f"{DEV}\\a4c337112233", "COD", 0x7A020C)
    h.value(f"{DEV}\\a4c337112233", "LastSeen", 133_600_000_000_000_000, REG_QWORD)
    h.value(f"{DEV}\\a4c337112233", "LastConnected", 133_590_000_000_000_000, REG_QWORD)
    h.value(f"{DEV}\\001122334455", "Name", b"WH-1000XM4\x00", REG_BINARY)
    h.value(f"{DEV}\\001122334455", "COD", 0x240404)
    h.value(f"{KEYS}\\f8e4e3aabbcc", "a4c337112233", b"\x11" * 16, REG_BINARY)
    enum = r"ControlSet001\Enum"
    h.value(f"{enum}\\BTHENUM\\{{0000111f-0000-1000-8000-00805f9b34fb}}_VID&0001004c_PID&0000\\"
            "8&2a3b4c5d&0&A4C337112233_C00000000", "FriendlyName", "Jane's iPhone Hands-Free AG")
    h.value(f"{enum}\\BTHLEDevice\\{{7905f431-b5ce-4e99-a40f-4b1e122d00d0}}_Dev_VID&01004c_PID&0000_REV&0000_"
            "a4c337112233\\9&1a2b3c&0&0001", "FriendlyName", "Jane's iPhone")
    # A headset's audio sink is not a phone service.
    h.value(f"{enum}\\BTHENUM\\{{0000110b-0000-1000-8000-00805f9b34fb}}_VID&000105d6_PID&0001\\"
            "8&9&0&001122334455_C00000000", "FriendlyName", "WH-1000XM4 Stereo")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(h.build())


def test_bthport_phone_and_headset(tmp_path):
    root = tmp_path / "C"
    _system_hive(root / "Windows" / "System32" / "config" / "SYSTEM")
    (root / "Users" / "jdoe").mkdir(parents=True)
    (root / "Users" / "jdoe" / "NTUSER.DAT").write_bytes(b"regf")
    result = scan(tmp_path, plugin_ids=["win_bluetooth"])
    phone_obs = [o for o in result.observations if o.data.get("address") == "a4c337112233"]
    assert {o.event for o in phone_obs} == {"bt_last_seen", "bt_last_connected", "bt_key_last_written"}
    seen = next(o for o in phone_obs if o.event == "bt_last_seen")
    assert seen.timestamp == from_filetime(133_600_000_000_000_000)
    assert seen.data["paired"] is True and seen.data["adapter"] == "f8e4e3aabbcc"
    assert seen.data["minor_class"] == "smartphone"
    assert "BTHPORT" in seen.provenance.locator and seen.provenance.locator.endswith("LastSeen")
    devs = result.graph.devices
    assert devs[0].label == "Jane's iPhone" and devs[0].device_class == "phone"
    assert devs[1].label == "WH-1000XM4" and devs[1].device_class == "audio"
    # The headset has no LastSeen/LastConnected values; only its key's last-written time remains.
    headset = [o for o in result.observations if o.data.get("address") == "001122334455"]
    assert [o.event for o in headset] == ["bt_key_last_written"]


def test_phone_services_from_bthenum_and_ble(tmp_path):
    root = tmp_path / "C"
    _system_hive(root / "Windows" / "System32" / "config" / "SYSTEM")
    (root / "Users" / "jdoe").mkdir(parents=True)
    (root / "Users" / "jdoe" / "NTUSER.DAT").write_bytes(b"regf")
    result = scan(tmp_path, plugin_ids=["win_bluetooth"])
    svc = [o for o in result.observations if o.event == "bt_phone_service"]
    assert sorted(o.data["service"] for o in svc) == [
        "Apple Notification Center Service (iPhone notifications)", "Hands-Free Audio Gateway (phone calls)"]
    assert all(o.data.get("uuid") != "0000110b-0000-1000-8000-00805f9b34fb" for o in svc)
    iphone = next(d for d in result.graph.devices if d.label == "Jane's iPhone")
    assert iphone.phone_os == "ios" and iphone.events["bt_phone_service"] == 2
