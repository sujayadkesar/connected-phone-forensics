"""Build a synthetic three-computer case and scan it: ``tether demo OUT``.

Persona "Alex Rivera" owns an iPhone 15 and a Pixel 8 and uses a Windows
laptop, a MacBook and a Linux desktop. The same iPhone leaves different
traces on each computer; Tether recognises it as one phone through its
UDID and Bluetooth address. Every byte is synthetic.
"""

from __future__ import annotations

import plistlib
import struct
from datetime import datetime, timezone
from pathlib import Path

from ._synth.certs import make_cert_pem
from ._synth.phonelink import build_phonelink_profile
from ._synth.regf import DEVPROP_FILETIME, REG_BINARY, REG_MULTI_SZ, REG_QWORD, HiveBuilder

UTC = timezone.utc
IPHONE_UDID = "00008120-001A2B3C4D5E801E"
IPHONE_BT = "a4:c3:37:5e:10:2b"
IPHONE_WIFI = "a4:c3:37:5e:10:2c"
IPHONE_NAME = "Alex’s iPhone"
PIXEL_SERIAL = "3A281FDJH00B7C"
PIXEL_BT = "f8:0f:f9:44:21:9a"
HOTSPOT_MAC = bytes.fromhex("6ac3e15e102b")


def _ft(dt: datetime) -> int:
    return int((dt - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 10_000_000)


def _systemtime_local(dt_local: datetime) -> bytes:
    return struct.pack("<8H", dt_local.year, dt_local.month, (dt_local.weekday() + 1) % 7, dt_local.day,
                       dt_local.hour, dt_local.minute, dt_local.second, 0)


def _pair_record(paired: datetime) -> dict:
    later = paired.replace(year=paired.year + 10)
    return {
        "DeviceCertificate": make_cert_pem(paired, later, cn="Device"),
        "HostCertificate": make_cert_pem(paired, later, cn="Host"),
        "RootCertificate": make_cert_pem(paired, later, cn="Root"),
        "HostID": "5C1A3E2B-7D44-4F0E-9A61-3B2D8E4F1C77",
        "SystemBUID": "0E9B2C41-6A7D-4E33-8F12-5D6C7B8A9E01",
        "WiFiMACAddress": IPHONE_WIFI,
        "EscrowBag": b"\x00" * 64,
        "HostPrivateKey": b"-----BEGIN RSA PRIVATE KEY-----",
    }


def _backup(folder: Path, last: datetime, encrypted: bool) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    naive = last.replace(tzinfo=None)
    with open(folder / "Info.plist", "wb") as f:
        plistlib.dump({
            "Device Name": IPHONE_NAME, "Display Name": IPHONE_NAME,
            "Unique Identifier": IPHONE_UDID.replace("-", ""), "Target Identifier": IPHONE_UDID.replace("-", ""),
            "Serial Number": "HG7XQ2LM9P", "IMEI": "353912110123456", "Phone Number": "+1 (415) 555-0142",
            "Product Type": "iPhone15,4", "Product Version": "18.1", "Build Version": "22B83",
            "Last Backup Date": naive, "iTunes Version": "12.13.4.4",
            "Installed Applications": ["net.whatsapp.WhatsApp", "ph.telegra.Telegraph", "org.whispersystems.signal"],
        }, f)
    with open(folder / "Manifest.plist", "wb") as f:
        plistlib.dump({"IsEncrypted": encrypted, "WasPasscodeSet": True,
                       "Lockdown": {"DeviceName": IPHONE_NAME, "UniqueDeviceID": IPHONE_UDID}}, f)
    with open(folder / "Status.plist", "wb") as f:
        plistlib.dump({"IsFullBackup": False, "SnapshotState": "finished", "Date": naive,
                       "UUID": "8D6E2F1A-0B3C-4D5E-9F70-1A2B3C4D5E6F", "Version": "3.3"}, f)
    (folder / "Manifest.db").write_bytes(b"\x8a\x91" * 64 if encrypted else b"SQLite format 3\x00")


def _windows(root: Path) -> None:
    cfg = root / "Windows" / "System32" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    home = root / "Users" / "alex"
    home.mkdir(parents=True, exist_ok=True)
    nt = HiveBuilder(root_name="CsiTool-CreateHive-{00000000-0000-0000-0000-000000000000}")
    dc = r"Software\Microsoft\Windows\CurrentVersion\TaskFlow\DeviceCache"
    for key, name, make, model, dtype in (("RzV4dGVyblBpeGVsOA==", "Pixel 8", "Google", "Pixel 8", 8),):
        nt.key(f"{dc}\\{key}", last_written=_ft(datetime(2025, 6, 14, 9, 5, tzinfo=UTC)))
        for vname, v in (("DeviceName", name), ("DeviceMake", make), ("DeviceModel", model)):
            nt.value(f"{dc}\\{key}", vname, v)
        nt.value(f"{dc}\\{key}", "DeviceType", dtype)
    nt.value(r"Software\Microsoft\Windows\CurrentVersion\Mobility", "CrossDeviceEnabled", 1)
    nt.value(r"Software\Microsoft\Windows\CurrentVersion\Mobility", "PhoneLinkEnabled", 1)
    (home / "NTUSER.DAT").write_bytes(nt.build())

    sy = HiveBuilder()
    sy.value("Select", "Current", 1)
    sy.value(r"ControlSet001\Control\ComputerName\ComputerName", "ComputerName", "ALEX-LAPTOP")
    tz = r"ControlSet001\Control\TimeZoneInformation"
    for name, value in (("TimeZoneKeyName", "Pacific Standard Time"), ("Bias", 480), ("StandardBias", 0),
                        ("DaylightBias", 0xFFFFFFC4)):
        sy.value(tz, name, value)
    sy.value(tz, "DaylightStart", bytes.fromhex("00000300020002000000000000000000"), REG_BINARY)
    sy.value(tz, "StandardStart", bytes.fromhex("00000b00010002000000000000000000"), REG_BINARY)
    usb = r"ControlSet001\Enum\USB"
    props = r"Properties\{83da6326-97a6-4088-9453-a1923f573b29}"

    def times(key, first, arrival, removal):
        for sub, ts in (("0064", first), ("0065", first), ("0066", arrival), ("0067", removal)):
            sy.value(f"{key}\\{props}\\{sub}", "", _ft(ts), DEVPROP_FILETIME)

    ip = f"{usb}\\VID_05AC&PID_12A8\\{IPHONE_UDID.replace('-', '')}"
    sy.value(ip, "DeviceDesc", "Apple Mobile Device USB Composite Device")
    sy.value(ip, "ContainerID", "{7f1c2d3e-4b5a-5c6d-8e9f-0a1b2c3d4e5f}")
    times(ip, datetime(2025, 3, 2, 17, 4, 11, tzinfo=UTC), datetime(2025, 9, 28, 21, 15, 3, tzinfo=UTC),
          datetime(2025, 9, 28, 22, 40, 51, tzinfo=UTC))
    px = f"{usb}\\VID_18D1&PID_4EE7\\{PIXEL_SERIAL}"
    sy.value(px, "DeviceDesc", "USB Composite Device")
    sy.value(px, "ContainerID", "{2a3b4c5d-6e7f-5081-9a2b-3c4d5e6f7081}")
    times(px, datetime(2025, 6, 14, 8, 30, 0, tzinfo=UTC), datetime(2025, 6, 14, 8, 30, 0, tzinfo=UTC),
          datetime(2025, 6, 14, 9, 2, 44, tzinfo=UTC))
    pxm = f"{usb}\\VID_18D1&PID_4EE7&MI_00\\7&1f2e3d4c&0&0000"
    sy.value(pxm, "DeviceDesc", "Pixel 8")
    sy.value(pxm, "FriendlyName", "Pixel 8")
    sy.value(pxm, "Service", "WUDFWpdMtp")
    sy.value(pxm, "CompatibleIDs", ["USB\\MS_COMP_MTP"], REG_MULTI_SZ)
    sy.value(pxm, "ContainerID", "{2a3b4c5d-6e7f-5081-9a2b-3c4d5e6f7081}")
    pxa = f"{usb}\\VID_18D1&PID_4EE7&MI_01\\7&1f2e3d4c&0&0001"
    sy.value(pxa, "DeviceDesc", "Android Composite ADB Interface")
    sy.value(pxa, "CompatibleIDs", ["USB\\Class_FF&SubClass_42&Prot_01"], REG_MULTI_SZ)
    sy.value(pxa, "ContainerID", "{2a3b4c5d-6e7f-5081-9a2b-3c4d5e6f7081}")

    bt = r"ControlSet001\Services\BTHPORT\Parameters"
    dev = f"{bt}\\Devices\\{IPHONE_BT.replace(':', '')}"
    sy.key(dev, last_written=_ft(datetime(2025, 10, 3, 18, 2, 9, tzinfo=UTC)))
    sy.value(dev, "Name", IPHONE_NAME.encode() + b"\x00", REG_BINARY)
    sy.value(dev, "COD", 0x7A020C)
    sy.value(dev, "LastSeen", _ft(datetime(2025, 10, 3, 18, 2, 9, tzinfo=UTC)), REG_QWORD)
    sy.value(dev, "LastConnected", _ft(datetime(2025, 10, 3, 17, 55, 40, tzinfo=UTC)), REG_QWORD)
    sy.value(f"{bt}\\Keys\\f4b7e2a10c33", IPHONE_BT.replace(":", ""), b"\x5a" * 16, REG_BINARY)
    hp = f"{bt}\\Devices\\38184c10aa21"
    sy.value(hp, "Name", b"WH-1000XM5\x00", REG_BINARY)
    sy.value(hp, "COD", 0x240404)

    iface = r"ControlSet001\Services\Tcpip\Parameters\Interfaces\{9c2d6f1e-3a4b-4c5d-8e7f-1a2b3c4d5e6f}"
    sy.value(iface, "DhcpIPAddress", "172.20.10.3")
    sy.value(iface, "DhcpServer", "172.20.10.1")
    sy.value(iface, "DhcpDefaultGateway", ["172.20.10.1"], REG_MULTI_SZ)
    sy.value(iface, "DhcpGatewayHardware", bytes([172, 20, 10, 1]) + struct.pack("<I", 6) + HOTSPOT_MAC,
             REG_BINARY)
    sy.value(iface, "LeaseObtainedTime", int(datetime(2025, 10, 9, 23, 12, 5, tzinfo=UTC).timestamp()))
    (cfg / "SYSTEM").write_bytes(sy.build())

    sw = HiveBuilder()
    cv = r"Microsoft\Windows NT\CurrentVersion"
    sw.value(cv, "ProductName", "Windows 10 Pro")
    sw.value(cv, "DisplayVersion", "24H2")
    sw.value(cv, "CurrentBuild", "26100")
    nl = r"Microsoft\Windows NT\CurrentVersion\NetworkList"
    prof = f"{nl}\\Profiles\\{{4E1F2A3B-5C6D-4E7F-8091-A2B3C4D5E6F7}}"
    sw.value(prof, "ProfileName", IPHONE_NAME)
    sw.value(prof, "NameType", 71)
    sw.value(prof, "DateCreated", _systemtime_local(datetime(2025, 4, 18, 14, 3, 0)), REG_BINARY)
    sw.value(prof, "DateLastConnected", _systemtime_local(datetime(2025, 10, 9, 16, 12, 0)), REG_BINARY)
    sig = f"{nl}\\Signatures\\Unmanaged\\010103000F0000F0080000000F0000F0A1B2C3D4"
    sw.value(sig, "ProfileGuid", "{4E1F2A3B-5C6D-4E7F-8091-A2B3C4D5E6F7}")
    sw.value(sig, "DefaultGatewayMac", HOTSPOT_MAC, REG_BINARY)
    home_prof = f"{nl}\\Profiles\\{{11111111-2222-3333-4444-555555555555}}"
    sw.value(home_prof, "ProfileName", "RiveraHome-5G")
    sw.value(home_prof, "DateCreated", _systemtime_local(datetime(2024, 11, 1, 9, 0, 0)), REG_BINARY)
    (cfg / "SOFTWARE").write_bytes(sw.build())

    build_phonelink_profile(root / "Users", "alex", "9a6e2c1f-4b3d-4e5a-8f70-2d1c3b4a5e6f",
                            phone_number="+14155550199",
                            contacts=[("Jordan Lee", "+1 415 555 0177"), ("Sam Ortiz", "+1 415 555 0123")],
                            base=datetime(2025, 10, 11, 19, 30, tzinfo=UTC))
    lock = root / "ProgramData" / "Apple" / "Lockdown"
    lock.mkdir(parents=True, exist_ok=True)
    with open(lock / f"{IPHONE_UDID}.plist", "wb") as f:
        plistlib.dump(_pair_record(datetime(2025, 3, 2, 17, 4, 30, tzinfo=UTC)), f)
    _backup(home / "Apple" / "MobileSync" / "Backup" / IPHONE_UDID.replace("-", ""),
            datetime(2025, 9, 28, 21, 40, 12, tzinfo=UTC), encrypted=True)
    inf = root / "Windows" / "INF"
    inf.mkdir(parents=True, exist_ok=True)
    (inf / "setupapi.dev.log").write_text(
        "[Device Install Log]\n     OS Version = 10.0.26100\n"
        f">>>  [Device Install (Hardware initiated) - USB\\VID_05AC&PID_12A8\\{IPHONE_UDID.replace('-', '')}]\n"
        ">>>  Section start 2025/03/02 09:04:09.215\n<<<  Section end 2025/03/02 09:04:20.003\n"
        f">>>  [Device Install (Hardware initiated) - USB\\VID_18D1&PID_4EE7\\{PIXEL_SERIAL}]\n"
        ">>>  Section start 2025/06/14 01:29:58.770\n<<<  Section end 2025/06/14 01:30:04.120\n",
        encoding="utf-8")


def _mac(root: Path) -> None:
    (root / "Library" / "Preferences").mkdir(parents=True, exist_ok=True)
    (root / "System" / "Library" / "CoreServices").mkdir(parents=True, exist_ok=True)
    with open(root / "System" / "Library" / "CoreServices" / "SystemVersion.plist", "wb") as f:
        plistlib.dump({"ProductVersion": "15.6", "ProductBuildVersion": "24G84"}, f)
    sc = root / "Library" / "Preferences" / "SystemConfiguration"
    sc.mkdir(parents=True, exist_ok=True)
    with open(sc / "preferences.plist", "wb") as f:
        plistlib.dump({"System": {"System": {"ComputerName": "Alex’s MacBook Air"}}}, f)
    lock = root / "private" / "var" / "db" / "lockdown"
    lock.mkdir(parents=True, exist_ok=True)
    with open(lock / f"{IPHONE_UDID}.plist", "wb") as f:
        plistlib.dump(_pair_record(datetime(2025, 5, 20, 19, 45, 2, tzinfo=UTC)), f)
    _backup(root / "Users" / "alex" / "Library" / "Application Support" / "MobileSync" / "Backup" / IPHONE_UDID,
            datetime(2025, 8, 11, 6, 15, 0, tzinfo=UTC), encrypted=False)
    _mac_sync(root)


def _sqlite(path: Path, script: str, rows: list[tuple[str, tuple]]) -> None:
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript(script)
    for sql, params in rows:
        c.execute(sql, params)
    c.commit()
    c.close()


def _cocoa(dt: datetime, ns: bool = False) -> float | int:
    s = (dt - datetime(2001, 1, 1, tzinfo=UTC)).total_seconds()
    return int(s * 1e9) if ns else s


def _mac_sync(root: Path) -> None:
    lib = root / "Users" / "alex" / "Library"
    site = "5E7D8C9B-3A2F-4C1D-9E8B-7A6F5D4C3B2A"
    _sqlite(lib / "Biome" / "sync" / "sync.db", """
        CREATE TABLE DevicePeer(device_identifier TEXT, name TEXT, platform INTEGER, model NUMERIC, me INTEGER,
            last_sync_date INTEGER, ids_device_identifier TEXT);
        CREATE TABLE AtomMergedLog(synced_at REAL, event_created_at REAL, stream TEXT, merge_result INTEGER,
            owning_site_identifier TEXT, relayed_by_site_identifier TEXT, session_id TEXT, message_id TEXT);
    """, [
        ("INSERT INTO DevicePeer VALUES ('0C1D2E3F-AAAA-4BBB-8CCC-DDDDEEEEFFFF', '', 4, '24G84', 1, NULL, NULL)", ()),
        ("INSERT INTO DevicePeer VALUES (?, ?, 2, '22B83', 0, ?, '6A5B4C3D-1111-2222-3333-444455556666')",
         (site, IPHONE_NAME, int(datetime(2025, 10, 12, 7, 41, tzinfo=UTC).timestamp()))),
        ("INSERT INTO AtomMergedLog VALUES (?, ?, 'App.InFocus', 3, ?, NULL, 's1', 'm1')",
         (_cocoa(datetime(2025, 9, 14, 20, 5, tzinfo=UTC)), _cocoa(datetime(2025, 9, 14, 20, 4, tzinfo=UTC)), site)),
        ("INSERT INTO AtomMergedLog VALUES (?, ?, 'App.InFocus', 3, ?, NULL, 's2', 'm2')",
         (_cocoa(datetime(2025, 10, 12, 7, 41, tzinfo=UTC)), _cocoa(datetime(2025, 10, 12, 7, 40, tzinfo=UTC)), site)),
    ])
    _sqlite(lib / "Messages" / "chat.db", """
        CREATE TABLE handle(ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
        CREATE TABLE message(ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB, handle_id INTEGER,
            service TEXT, account TEXT, date INTEGER, date_read INTEGER, date_delivered INTEGER, is_from_me INTEGER,
            destination_caller_id TEXT, service_center TEXT, cache_has_attachments INTEGER, date_edited INTEGER,
            date_retracted INTEGER);
    """, [
        ("INSERT INTO handle VALUES (1, '+14155550177', 'SMS')", ()),
        ("INSERT INTO message VALUES (1, 'A1', 'Meet at the dock at 9', NULL, 1, 'SMS', 'P:+14155550142', ?, 0, 0, 0,"
         " '+14155550142', NULL, 0, 0, 0)", (_cocoa(datetime(2025, 10, 10, 18, 22, tzinfo=UTC), ns=True),)),
        ("INSERT INTO message VALUES (2, 'A2', 'ok', NULL, 1, 'SMS', 'P:+14155550142', ?, 0, 0, 1,"
         " '+14155550142', NULL, 0, 0, 0)", (_cocoa(datetime(2025, 10, 10, 18, 25, tzinfo=UTC), ns=True),)),
    ])


def _linux(root: Path) -> None:
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "etc" / "os-release").write_text('PRETTY_NAME="Ubuntu 24.04.3 LTS"\n')
    (root / "etc" / "hostname").write_text("alex-desktop\n")
    (root / "home" / "alex").mkdir(parents=True, exist_ok=True)
    adapter = "00:1A:7D:DA:71:13"
    bt = root / "var" / "lib" / "bluetooth" / adapter
    for mac, name, cls, vendor in ((IPHONE_BT.upper(), IPHONE_NAME, "0x7a020c", 76),
                                   (PIXEL_BT.upper(), "Pixel 8", "0x5a020c", 224)):
        d = bt / mac
        d.mkdir(parents=True, exist_ok=True)
        (d / "info").write_text(
            f"[General]\nName={name}\nClass={cls}\nSupportedTechnologies=BR/EDR;LE;\nTrusted=true\n"
            f"Blocked=false\n\n[LinkKey]\nKey={'AB' * 16}\nType=4\nPINLength=0\n\n"
            f"[DeviceID]\nSource=1\nVendor={vendor}\nProduct=1\nVersion=1\n", encoding="utf-8")
    kc = root / "home" / "alex" / ".config" / "kdeconnect"
    dev_id = "b7c1e2d3_a4f5_4b6c_8d7e_9f0a1b2c3d4e"
    (kc / dev_id / "kdeconnect_sms").mkdir(parents=True, exist_ok=True)
    (kc / "config").write_text("[General]\nname=alex-desktop\n", encoding="utf-8")
    cert = make_cert_pem(datetime(2024, 6, 13, 22, 0, tzinfo=UTC), datetime(2035, 6, 13, 22, 0, tzinfo=UTC),
                         cn=dev_id).decode().replace("\n", "\\n")
    (kc / "trusted_devices").write_text(
        f"[{dev_id}]\nname=Pixel 8\ntype=phone\nprotocolVersion=8\ncertificate=\"{cert}\"\n", encoding="utf-8")


def build_demo_evidence(base: Path) -> Path:
    """Create the synthetic evidence tree; returns its root."""
    ev = Path(base) / "evidence"
    _windows(ev / "ALEX-LAPTOP" / "C")
    _mac(ev / "ALEX-MACBOOK")
    _linux(ev / "ALEX-DESKTOP")
    return ev
