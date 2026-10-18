"""Windows USB phone enumeration.

The synthetic fixture mirrors the structure observed in a real Windows SYSTEM
hive with a Samsung Galaxy S5 (SM-G900V) attached over MTP (Eric Zimmerman's
sample hives in the DFIR Artifact Museum). Set TETHER_REALDATA to a folder
containing EZ_W7RegistryHives/SYSTEM to also run against the real hive.
"""

import os
import shutil
from pathlib import Path

import pytest

from tests.fixtures.regf import DEVPROP_FILETIME, REG_MULTI_SZ, REG_SZ, HiveBuilder
from tether.engine import scan
from tether.model import IdKind
from tether.timeutil import from_filetime

USB = r"ControlSet001\Enum\USB"
PROPS = r"Properties\{83da6326-97a6-4088-9453-a1923f573b29}"
CID = "{02515846-e412-542e-84e4-4bfc2767263f}"
T_INSTALL, T_ARRIVE, T_REMOVE = 130_688_948_456_050_000, 130_688_948_467_037_060, 130_688_992_257_505_080


def _times(h, key, install, arrival, removal):
    for sub, ts in (("0064", install), ("0065", install), ("0066", arrival), ("0067", removal)):
        h.value(f"{key}\\{PROPS}\\{sub}", "", ts, DEVPROP_FILETIME)


def _hive(path):
    h = HiveBuilder()
    h.value("Select", "Current", 1)
    parent = f"{USB}\\VID_04E8&PID_6860\\ce0fa56b"
    h.value(parent, "DeviceDesc", "@oem26.inf,%usb\\vid_04e8&pid_6860.devicedesc%;SAMSUNG Mobile USB Composite Device")
    h.value(parent, "ContainerID", CID)
    h.value(parent, "Mfg", "@oem26.inf,%ssud%;SAMSUNG Electronics Co., Ltd.")
    h.value(parent, "CompatibleIDs", ["USB\\MS_COMP_MTP", "USB\\Class_06&SubClass_01&Prot_01"], REG_MULTI_SZ)
    _times(h, parent, T_INSTALL, T_ARRIVE - 1000, T_REMOVE - 5000)
    mtp = f"{USB}\\VID_04E8&PID_6860&MS_COMP_MTP&SAMSUNG_Android\\6&18a2405&1&0000"
    h.value(mtp, "DeviceDesc", "SM-G900V")
    h.value(mtp, "FriendlyName", "SAMSUNG-SM-G900V")
    h.value(mtp, "ContainerID", CID)
    h.value(mtp, "Service", "WUDFWpdMtp")
    h.value(mtp, "CompatibleIDs", ["USB\\MS_COMP_MTP"], REG_MULTI_SZ)
    _times(h, mtp, T_INSTALL + 9_670_000, T_ARRIVE, T_REMOVE)
    # The phone's SD card exposed as mass storage: SubClass_06 (SCSI) is not class 06 (PTP),
    # so this interface must not supply the device's name.
    msc = f"{USB}\\VID_04E8&PID_6860&MI_01\\6&18a2405&1&0002"
    h.value(msc, "DeviceDesc", "USB Mass Storage Device")
    h.value(msc, "CompatibleIDs", ["USB\\Class_08&SubClass_06&Prot_50", "USB\\Class_08"], REG_MULTI_SZ)
    h.value(msc, "ContainerID", CID)
    modem = f"{USB}\\VID_04E8&PID_6860&Modem\\6&18a2405&1&0001"
    h.value(modem, "DeviceDesc", "SAMSUNG Mobile USB Modem", REG_SZ)
    h.value(modem, "ContainerID", CID)
    # An iPhone: Apple VID, mobile-device PID, serial = UDID.
    ip = f"{USB}\\VID_05AC&PID_12A8\\00008110000A1B2C3D4E801E"
    h.value(ip, "DeviceDesc", "Apple Mobile Device USB Composite Device")
    h.value(ip, "ContainerID", "{11111111-2222-3333-4444-555555555555}")
    _times(h, ip, T_INSTALL, T_ARRIVE, T_REMOVE)
    # A (VMware) Bluetooth radio: USB class E0/01/01 is not RNDIS tethering. Must be ignored.
    bt = f"{USB}\\VID_0E0F&PID_0008\\000650268328"
    h.value(bt, "DeviceDesc", "Generic Bluetooth Adapter")
    h.value(bt, "Service", "BTHUSB")
    h.value(bt, "CompatibleIDs", ["USB\\Class_E0&SubClass_01&Prot_01", "USB\\Class_E0"], REG_MULTI_SZ)
    h.value(bt, "ContainerID", "{2cbad689-8dc0-543b-a46f-dad20d391c7b}")
    # An Apple keyboard and a Samsung SSD: must be ignored.
    h.value(f"{USB}\\VID_05AC&PID_024F\\5&abc&0&1", "DeviceDesc", "USB Input Device")
    h.value(f"{USB}\\VID_04E8&PID_61F5\\S5SVNF0R123456", "DeviceDesc", "USB Mass Storage Device")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(h.build())


def _case(root):
    _hive(root / "Windows" / "System32" / "config" / "SYSTEM")
    (root / "Users" / "jdoe").mkdir(parents=True)
    (root / "Users" / "jdoe" / "NTUSER.DAT").write_bytes(b"regf")


def test_usb_phones_grouped_by_container(tmp_path):
    _case(tmp_path / "C")
    result = scan(tmp_path, plugin_ids=["win_usb_phones"])
    devs = result.graph.devices
    assert len(devs) == 2
    galaxy = next(d for d in devs if d.phone_os == "android")
    assert galaxy.label == "SM-G900V"
    serial = next(i for i in galaxy.identifiers if i.kind == IdKind.USB_SERIAL)
    assert serial.value == "CE0FA56B" and galaxy.raw_values[serial] == {"ce0fa56b"}
    events = sorted(o.event for o in result.observations if galaxy.id == result.graph.assignment[result.observations.index(o)])
    assert events == ["usb_first_install", "usb_install", "usb_last_arrival", "usb_last_removal"]
    arrival = next(o for o in result.observations if o.event == "usb_last_arrival" and o.phone_os == "android")
    assert arrival.timestamp == from_filetime(T_ARRIVE)  # latest across interface nodes
    assert "MS_COMP_MTP" in arrival.provenance.locator
    iphone = next(d for d in devs if d.phone_os == "ios")
    assert any(i.kind == IdKind.UDID for i in iphone.identifiers)


def test_outputs_written(tmp_path):
    import json

    from tether.output import write_all

    _case(tmp_path / "ev" / "C")
    result = scan(tmp_path / "ev")
    written = {p.name for p in write_all(result, tmp_path / "out")}
    assert {"timeline.jsonl", "timeline.csv", "observations.jsonl", "devices.json",
            "coverage.csv", "manifest.json", "report.html"} <= written
    lines = (tmp_path / "out" / "timeline.jsonl").read_text().splitlines()
    rec = json.loads(lines[0])
    assert {"message", "datetime", "timestamp_desc"} <= set(rec)  # Timesketch required fields
    man = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert "Windows/System32/config/SYSTEM" in man["evidence_files_sha256"][result.systems[0].label]
    html = (tmp_path / "out" / "report.html").read_text()
    assert "SM-G900V" in html and "<script" in html and "http" not in html.split("<main>")[0]


REAL = os.environ.get("TETHER_REALDATA")


@pytest.mark.skipif(not REAL, reason="TETHER_REALDATA not set")
def test_real_ez_win7_hive(tmp_path):
    src = Path(REAL) / "EZ_W7RegistryHives"
    cfg = tmp_path / "C" / "Windows" / "System32" / "config"
    cfg.mkdir(parents=True)
    for name in ("SYSTEM", "system.LOG1", "system.LOG2"):
        if (src / name).exists():
            shutil.copy(src / name, cfg / name)
    result = scan(tmp_path, plugin_ids=["win_usb_phones"])
    labels = {d.label for d in result.graph.devices}
    assert "SM-G900V" in labels
