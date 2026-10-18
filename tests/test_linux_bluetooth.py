import sys

import pytest

from connected_phone_forensics.engine import scan

INFO = """[General]
Name=Jane's iPhone
Class=0x7a020c
SupportedTechnologies=BR/EDR;LE;
Trusted=true
Blocked=false
Services=0000110a-0000-1000-8000-00805f9b34fb;0000111f-0000-1000-8000-00805f9b34fb;

[LinkKey]
Key=0123456789ABCDEF0123456789ABCDEF
Type=4
PINLength=0

[DeviceID]
Source=1
Vendor=76
Product=8194
Version=5
"""


@pytest.mark.skipif(sys.platform == "win32", reason="BlueZ paths contain ':' which Windows cannot store")
def test_bluez_paired_and_cached(tmp_path):
    root = tmp_path / "ubuntu"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text('PRETTY_NAME="Ubuntu 24.04 LTS"\n')
    (root / "home" / "jane").mkdir(parents=True)
    dev = root / "var" / "lib" / "bluetooth" / "00:1A:7D:DA:71:13" / "A4:C3:37:11:22:33"
    dev.mkdir(parents=True)
    (dev / "info").write_text(INFO)
    cache = root / "var" / "lib" / "bluetooth" / "00:1A:7D:DA:71:13" / "cache"
    cache.mkdir()
    (cache / "A4:C3:37:11:22:33").write_text("[General]\nName=Jane's iPhone\n")  # paired: skipped
    (cache / "11:22:33:44:55:66").write_text("[General]\nName=Pixel 8\nClass=0x5a020c\n")
    result = scan(tmp_path, plugin_ids=["linux_bluetooth"])
    assert result.systems[0].details["pretty_name"] == "Ubuntu 24.04 LTS"
    obs = {o.data["address"]: o for o in result.observations}
    assert set(obs) == {"A4:C3:37:11:22:33", "11:22:33:44:55:66"}
    iphone = obs["A4:C3:37:11:22:33"]
    assert iphone.event == "bt_pairing_record" and iphone.phone_os == "ios"
    assert iphone.data["has_link_key"] and iphone.data["vendor"] == "Apple"
    assert iphone.time_source == "filesystem"
    assert obs["11:22:33:44:55:66"].event == "bt_cached_device"
    assert {d.label for d in result.graph.devices} == {"Jane's iPhone", "Pixel 8"}
