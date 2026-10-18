import plistlib
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

from connected_phone_forensics.engine import scan

UTC = timezone.utc


def cocoa(dt):
    return (dt - datetime(2001, 1, 1, tzinfo=UTC)).total_seconds()


def _base(tmp_path):
    root = tmp_path / "mac"
    (root / "Library" / "Preferences" / "SystemConfiguration").mkdir(parents=True)
    (root / "private" / "var" / "db" / "dhcpclient" / "leases").mkdir(parents=True)
    (root / "Users" / "jane" / "Library").mkdir(parents=True)
    return root


@pytest.mark.skipif(sys.platform == "win32", reason="DHCP lease names contain ':' which Windows cannot store")
def test_mac_hotspot_known_networks_and_lease(tmp_path):
    root = _base(tmp_path)
    with open(root / "Library" / "Preferences" / "com.apple.wifi.known-networks.plist", "wb") as f:
        plistlib.dump({
            "wifi.network.ssid.Jane’s iPhone": {"SSID": "Jane’s iPhone".encode(), "AddReason": "wifi menu",
                                                "AddedAt": datetime(2025, 5, 1, 9), "JoinedByUserAt": datetime(2025, 9, 1, 8),
                                                "BSSList": [{"BSSID": "6a:c3:e1:5e:10:2b"}]},
            "wifi.network.ssid.HomeNet": {"SSID": b"HomeNet", "AddedAt": datetime(2024, 1, 1)},
        }, f, fmt=plistlib.FMT_BINARY)
    with open(root / "private" / "var" / "db" / "dhcpclient" / "leases" / "en0-1,6a:c3:e1:5e:10:2b", "wb") as f:
        plistlib.dump({"RouterIPAddress": "172.20.10.1", "RouterHardwareAddress": bytes.fromhex("6ac3e15e102b"),
                       "IPAddress": "172.20.10.2", "LeaseStartDate": datetime(2025, 9, 1, 8, 1), "LeaseLength": 85536,
                       "SSID": "Jane’s iPhone"}, f)
    result = scan(tmp_path, plugin_ids=["mac_phone_hotspots"])
    events = sorted(o.event for o in result.observations)
    assert events == ["hotspot_added", "hotspot_dhcp_lease", "hotspot_joined_by_user"]
    assert len([d for d in result.graph.devices if d.confirmed]) == 1  # BSSID ties network and lease


def test_mac_notifications_flags_iphone_apps(tmp_path):
    root = _base(tmp_path)
    app = root / "Applications" / "Slack.app" / "Contents"
    app.mkdir(parents=True)
    with open(app / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.tinyspeck.slackmacgap"}, f)
    db = root / "Users" / "jane" / "Library" / "Group Containers" / "group.com.apple.usernoted" / "db2" / "db"
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE app(app_id INTEGER PRIMARY KEY, identifier VARCHAR, badge INTEGER NULL);"
                    "CREATE TABLE record(rec_id INTEGER PRIMARY KEY, app_id INTEGER, uuid BLOB, data BLOB, "
                    "request_date REAL, request_last_date REAL, delivered_date REAL, presented Bool, style INTEGER, "
                    "snooze_fire_date REAL);")
    c.execute("INSERT INTO app VALUES (1, 'com.tinyspeck.slackmacgap', 0)")
    c.execute("INSERT INTO app VALUES (2, 'net.whatsapp.WhatsApp', 0)")
    for app_id, title, body in ((1, "#general", "deploy done"), (2, "Jordan", "where are you?")):
        payload = plistlib.dumps({"app": "x", "req": {"titl": title, "body": body}}, fmt=plistlib.FMT_BINARY)
        c.execute("INSERT INTO record(app_id, data, delivered_date) VALUES (?,?,?)",
                  (app_id, payload, cocoa(datetime(2025, 9, 2, 10, tzinfo=UTC))))
    c.commit()
    c.close()
    result = scan(tmp_path, plugin_ids=["mac_notifications"])
    ev = {o.data["app"]: o for o in result.observations}
    assert ev["net.whatsapp.WhatsApp"].data["probably_from_iphone"] is True
    assert ev["net.whatsapp.WhatsApp"].event == "notification_iphone"
    assert ev["com.tinyspeck.slackmacgap"].data["probably_from_iphone"] is False


def test_knowledgec_sync_peer(tmp_path):
    root = _base(tmp_path)
    db = root / "Users" / "jane" / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db"
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE ZSYNCPEER(Z_PK INTEGER PRIMARY KEY, ZDEVICEID VARCHAR, ZMODEL VARCHAR, ZCLOUDID VARCHAR,
            ZVERSION VARCHAR, ZLASTSEENDATE TIMESTAMP, ZME INTEGER, ZNAME VARCHAR);
        CREATE TABLE ZSOURCE(Z_PK INTEGER PRIMARY KEY, ZDEVICEID VARCHAR, ZBUNDLEID VARCHAR);
        CREATE TABLE ZOBJECT(Z_PK INTEGER PRIMARY KEY, ZSTREAMNAME VARCHAR, ZVALUESTRING VARCHAR, ZSTARTDATE TIMESTAMP,
            ZENDDATE TIMESTAMP, ZSOURCE INTEGER);
    """)
    c.execute("INSERT INTO ZSYNCPEER VALUES (1, 'A1B2C3D4-E5F6-4A1B-9C3D-E5F6A7B8C9D0', 'iPhone15,2', 'cloud1', '1', ?, 0, NULL)",
              (cocoa(datetime(2024, 3, 1, tzinfo=UTC)),))
    c.execute("INSERT INTO ZSOURCE VALUES (1, 'A1B2C3D4-E5F6-4A1B-9C3D-E5F6A7B8C9D0', 'com.apple.mobilesafari')")
    for d in (1, 2, 3):
        c.execute("INSERT INTO ZOBJECT VALUES (?, '/app/inFocus', 'com.apple.mobilesafari', ?, ?, 1)",
                  (d, cocoa(datetime(2024, 2, d, tzinfo=UTC)), cocoa(datetime(2024, 2, d, 1, tzinfo=UTC))))
    c.commit()
    c.close()
    result = scan(tmp_path, plugin_ids=["mac_knowledgec_peers"])
    obs = result.observations[0]
    assert obs.data["model"] == "iPhone15,2" and obs.data["streams"]["/app/inFocus"]["events"] == 3
    assert obs.device_class == "phone" and result.graph.devices[0].label == "iPhone15,2"


def test_iphone_mirroring_prefs_and_placeholders(tmp_path):
    root = _base(tmp_path)
    prefs = root / "Users" / "jane" / "Library" / "Containers" / "com.apple.ScreenContinuity" / "Data" / "Library" / "Preferences"
    prefs.mkdir(parents=True)
    with open(prefs / "com.apple.ScreenContinuity.plist", "wb") as f:
        plistlib.dump({"onenessPairedDeviceID": "7E3F2A1B-9C8D-4E5F-A6B7-C8D9E0F1A2B3"}, f)
    stub = (root / "Users" / "jane" / "Library" / "Daemon Containers" / "0A1B2C3D-0000-4000-8000-000000000000" / "Data" /
            "Library" / "Caches" / "generated_placeholders.noindex" / "Signal.app")
    stub.mkdir(parents=True)
    with open(stub / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "org.whispersystems.signal", "CFBundleDisplayName": "Signal",
                       "CFBundleShortVersionString": "7.40", "CFBundleSupportedPlatforms": ["iPhoneOS"]}, f)
    result = scan(tmp_path, plugin_ids=["mac_iphone_mirroring"])
    ev = {o.event: o for o in result.observations}
    assert ev["iphone_mirroring_configured"].data["onenessPairedDeviceID"].startswith("7E3F")
    assert ev["iphone_app_inventory"].data["apps"][0]["name"] == "Signal"
