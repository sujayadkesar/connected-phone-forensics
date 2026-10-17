from datetime import datetime, timezone

from tests.test_windows_hotspot import _build
from tether.engine import scan

LOG = """[Device Install Log]
     OS Version = 10.0.22631
>>>  [Device Install (Hardware initiated) - USB\\VID_05AC&PID_12A8\\00008110000A1B2C3D4E801E]
>>>  Section start 2024/03/15 08:00:05.123
     ump: Creating Install Process: DrvInst.exe 08:00:05.140
<<<  Section end 2024/03/15 08:00:09.010
>>>  [Device Install (Hardware initiated) - USB\\VID_05AC&PID_12A8&MI_00\\7&2b3c&0&0000]
>>>  Section start 2024/03/15 08:00:06.000
>>>  [Device Install (Hardware initiated) - USB\\VID_046D&PID_C52B\\5&1234&0&2]
>>>  Section start 2024/03/15 09:00:00.000
>>>  [Device Install (Hardware initiated) - USB\\VID_18D1&PID_4EE1&MTP\\7&abc&0&0000]
>>>  Section start 2024/12/01 18:30:00.000
"""


def test_setupapi_first_install_times_in_utc(tmp_path):
    root = tmp_path / "C"
    _build(root)  # SYSTEM hive with Eastern time zone
    inf = root / "Windows" / "INF"
    inf.mkdir(parents=True)
    (inf / "setupapi.dev.log").write_text(LOG)
    result = scan(tmp_path, plugin_ids=["win_setupapi_phones"])
    obs = [o for o in result.observations if o.event == "usb_driver_first_install"]
    assert len(obs) == 3  # iPhone parent + interface, Google MTP; the Logitech receiver is ignored
    iphone = next(o for o in obs if o.data["instance"].endswith("801E"))
    assert iphone.timestamp == datetime(2024, 3, 15, 12, 0, 5, 123000, tzinfo=timezone.utc)  # EDT
    assert {i.kind.value for i in iphone.device_ids} == {"usb_serial", "udid"}
    assert iphone.provenance.locator == "line=3"
    pixel = next(o for o in obs if "18D1" in o.data["instance"])
    assert pixel.timestamp == datetime(2024, 12, 1, 23, 30, tzinfo=timezone.utc)  # EST
    assert pixel.device_ids == []  # interface instance id is Windows-generated, not a serial
