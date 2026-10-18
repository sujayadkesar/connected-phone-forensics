from connected_phone_forensics._synth.regf import HiveBuilder
from connected_phone_forensics.engine import scan
from connected_phone_forensics.model import IdKind

DC = r"Software\Microsoft\Windows\CurrentVersion\TaskFlow\DeviceCache"


def test_taskflow_devicecache_and_settings(tmp_path):
    root = tmp_path / "C"
    (root / "Windows" / "System32" / "config").mkdir(parents=True)
    home = root / "Users" / "sam"
    home.mkdir(parents=True)
    h = HiveBuilder()
    for key, name, model, dtype in (("aVBob25lMTU=", "Sam's iPhone", "iPhone15,4", 6),
                                    ("R2FsYXh5UzI0", "Galaxy S24", "SM-S921B", 8)):
        for vname, v in (("DeviceName", name), ("DeviceMake", "x"), ("DeviceModel", model)):
            h.value(f"{DC}\\{key}", vname, v)
        h.value(f"{DC}\\{key}", "DeviceType", dtype)
    mob = r"Software\Microsoft\Windows\CurrentVersion\Mobility"
    h.value(f"{mob}\\SmsSyncCapable", "OptedIn", 1)  # 2022-era layout seen on real hives
    (home / "NTUSER.DAT").write_bytes(h.build())
    (root / "Users" / "stub").mkdir()
    (root / "Users" / "stub" / "NTUSER.DAT").write_bytes(b"regf")
    result = scan(tmp_path, plugin_ids=["win_crossdevice"])
    devs = [o for o in result.observations if o.event == "ms_account_device"]
    assert sorted((o.data["name"], o.phone_os) for o in devs) == [("Galaxy S24", "android"), ("Sam's iPhone", "ios")]
    assert any(i.kind == IdKind.APP_DEVICE_ID and i.value == "cdp_platform_device:aVBob25lMTU=" for i in devs[0].device_ids + devs[1].device_ids)
    setting = next(o for o in result.observations if o.event == "crossdevice_settings")
    assert setting.data["settings"] == {"SmsSyncCapable.OptedIn": 1}
    cov = {c.source_path: c.status for c in result.coverage}
    assert cov["Users/stub/NTUSER.DAT"] == "unrecognised"
