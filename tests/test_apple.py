"""Apple pairing records and backups, on synthetic Windows and macOS layouts.

Fixtures follow the publicly documented formats (libimobiledevice pair
records; iTunes backup Info/Manifest/Status plists). They are synthetic:
these parsers are at validation level "synthetic" until checked against
real data.
"""

import plistlib
from datetime import datetime, timezone

from tests.fixtures.certs import make_cert_pem
from tether.engine import scan
from tether.model import IdKind

UTC = timezone.utc
UDID = "00008110-000A1B2C3D4E801E"
PAIRED = datetime(2024, 3, 15, 12, 0, 0, tzinfo=UTC)


def _pair_record():
    return {
        "DeviceCertificate": make_cert_pem(PAIRED, datetime(2034, 3, 15, tzinfo=UTC)),
        "HostCertificate": make_cert_pem(PAIRED, datetime(2034, 3, 15, tzinfo=UTC), cn="Host"),
        "RootCertificate": make_cert_pem(PAIRED, datetime(2034, 3, 15, tzinfo=UTC), cn="Root"),
        "HostID": "6E5A3B0C-1111-2222-3333-444455556666",
        "SystemBUID": "A1B2C3D4-AAAA-BBBB-CCCC-DDDDEEEEFFFF",
        "WiFiMACAddress": "a4:c3:37:11:22:33",
        "EscrowBag": b"\x00" * 64,
        "HostPrivateKey": b"-----BEGIN RSA PRIVATE KEY-----",
    }


def _backup(folder, last_backup):
    folder.mkdir(parents=True)
    with open(folder / "Info.plist", "wb") as f:
        plistlib.dump({
            "Device Name": "Jane's iPhone", "Display Name": "Jane's iPhone",
            "Unique Identifier": UDID.replace("-", ""), "Target Identifier": UDID.replace("-", ""),
            "Serial Number": "F17ABCDEF123", "IMEI": "356789012345678", "Phone Number": "+1 (555) 010-0199",
            "Product Type": "iPhone14,7", "Product Version": "17.4.1", "Build Version": "21E236",
            "Last Backup Date": last_backup.replace(tzinfo=None),
            "Installed Applications": ["com.whatsapp.WhatsApp", "org.telegram.Telegram"],
            "iTunes Version": "12.13.1.3",
        }, f)
    with open(folder / "Manifest.plist", "wb") as f:
        plistlib.dump({"IsEncrypted": True, "WasPasscodeSet": True,
                       "Lockdown": {"DeviceName": "Jane's iPhone", "UniqueDeviceID": UDID,
                                    "SerialNumber": "F17ABCDEF123", "ProductType": "iPhone14,7"}}, f)
    with open(folder / "Status.plist", "wb") as f:
        plistlib.dump({"IsFullBackup": False, "SnapshotState": "finished",
                       "Date": last_backup.replace(tzinfo=None), "UUID": "BACKUP-UUID", "Version": "3.3"}, f)
    (folder / "Manifest.db").write_bytes(b"\x8a\x91" * 64)  # encrypted Manifest.db is not SQLite


def _windows(root):
    (root / "Windows" / "System32" / "config").mkdir(parents=True)
    home = root / "Users" / "jdoe"
    home.mkdir(parents=True)
    (home / "NTUSER.DAT").write_bytes(b"regf")
    lock = root / "ProgramData" / "Apple" / "Lockdown"
    lock.mkdir(parents=True)
    with open(lock / f"{UDID}.plist", "wb") as f:
        plistlib.dump(_pair_record(), f)
    with open(lock / "SystemConfiguration.plist", "wb") as f:
        plistlib.dump({"SystemBUID": "A1B2C3D4-AAAA-BBBB-CCCC-DDDDEEEEFFFF"}, f)
    _backup(home / "Apple" / "MobileSync" / "Backup" / UDID.replace("-", ""),
            datetime(2024, 9, 1, 8, 30, tzinfo=UTC))


def test_windows_pairing_and_backup_merge_into_one_device(tmp_path):
    _windows(tmp_path / "C")
    result = scan(tmp_path, plugin_ids=["win_apple_pairing", "win_apple_backups"])
    events = sorted(o.event for o in result.observations)
    assert events.count("pairing_certificate_issued") == 1
    assert "host_system_buid" in events
    assert events.count("device_backup") == 2  # Info.plist and Status.plist dates (Manifest has none)
    devices = [d for d in result.graph.devices if d.confirmed]
    assert len(devices) == 1
    dev = devices[0]
    kinds = {i.kind for i in dev.identifiers}
    assert {IdKind.UDID, IdKind.WIFI_MAC, IdKind.SERIAL, IdKind.IMEI, IdKind.PHONE_NUMBER} <= kinds
    assert dev.label == "Jane's iPhone"
    assert set(dev.channels) == {"pairing", "backup"}
    assert dev.users == {"jdoe"}
    # The pair record's filesystem mtime is shown but must not drive first/last seen.
    assert dev.first_seen == PAIRED
    assert dev.last_seen == datetime(2024, 9, 1, 8, 30, tzinfo=UTC)
    paired = next(o for o in result.observations if o.event == "pairing_certificate_issued")
    assert paired.timestamp == PAIRED
    assert paired.data["has_escrow_bag"] is True
    backup = next(o for o in result.observations if o.event == "device_backup")
    assert backup.data["is_encrypted"] is True and backup.data["manifest_db"] == "encrypted_or_unknown"
    assert backup.provenance.sha256 and len(backup.provenance.sha256) == 64


def test_macos_paths(tmp_path):
    root = tmp_path / "mac"
    (root / "Library" / "Preferences").mkdir(parents=True)
    lock = root / "private" / "var" / "db" / "lockdown"
    lock.mkdir(parents=True)
    with open(lock / f"{UDID}.plist", "wb") as f:
        plistlib.dump(_pair_record(), f)
    home = root / "Users" / "jane"
    _backup(home / "Library" / "Application Support" / "MobileSync" / "Backup" / UDID,
            datetime(2025, 1, 2, tzinfo=UTC))
    result = scan(tmp_path, plugin_ids=["mac_apple_pairing", "mac_apple_backups"])
    assert result.systems[0].os == "macos"
    assert len([d for d in result.graph.devices if d.confirmed]) == 1
    assert {o.artifact for o in result.observations} == {"mac_apple_pairing", "mac_apple_backups"}


def test_non_pair_plist_is_reported_unrecognised(tmp_path):
    root = tmp_path / "C"
    _windows(root)
    with open(root / "ProgramData" / "Apple" / "Lockdown" / "weird.plist", "wb") as f:
        plistlib.dump({"Something": 1}, f)
    result = scan(tmp_path, plugin_ids=["win_apple_pairing"])
    statuses = {c.source_path.split("/")[-1]: c.status for c in result.coverage}
    assert statuses["weird.plist"] == "unrecognised"
