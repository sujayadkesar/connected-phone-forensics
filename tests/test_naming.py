"""Naming phones: model codes, USB makers, names in free text, and hotspot-only phones by network."""

from collections import Counter

from connected_phone_forensics import content, decode
from connected_phone_forensics.correlate import best_name


def test_model_codes_become_marketing_names():
    assert decode.android_model("CPH2585") == ("OnePlus", "OnePlus 12R")
    assert decode.android_model("SM-S901B") == ("Samsung", "Galaxy S22")
    assert decode.android_model("Pixel 7a") == ("Google", "Pixel 7a")  # a model that is its own name
    assert decode.android_model("I2220") == ("vivo", "iQOO 12")
    assert decode.android_model("NOT-A-MODEL") is None


def test_usb_vendor_ids_name_phone_makers_not_chip_makers():
    assert decode.usb_brand("2A70") == "OnePlus" and decode.usb_brand("2D95") == "vivo"
    assert decode.usb_brand("05AC") == "Apple"
    assert decode.usb_vendor("0E8D") == "MediaTek" and decode.usb_brand("0E8D") is None


def test_phones_named_in_free_text():
    cases = {
        "OnePlus 13s 1D8C": ("OnePlus", "OnePlus 13s"), "Galaxy S22 5G": ("Samsung", "Galaxy S22"),
        "Pixel 7a": ("Google", "Pixel 7a"), "Redmi 9i": ("Redmi", "Redmi 9i"),
        "iPhone 15 Pro": ("Apple", "iPhone 15 Pro"), "Sujay’s iPhone": ("Apple", None),
        "AndroidAP_1234": ("Android", None), "Jane's Galaxy": ("Samsung", None),
    }
    for text, want in cases.items():
        assert decode.phone_in_name(text) == want, text
    assert decode.phone_in_name("Office WiFi 2") is None and decode.phone_in_name("LOCALHOST") is None
    assert decode.brand_from_org("vivo Mobile Communication Co., Ltd.") == "vivo"
    assert decode.brand_from_org("Apple, Inc.") == "Apple"


def test_the_most_phone_like_name_wins():
    assert best_name(Counter({"Localhost": 3, "Sujay’s iPhone": 1}), "ios") == "Sujay’s iPhone"


def _hotspot(dev_id: str, ssid: str, bssid: str, first: str, last: str):
    device = {"id": dev_id, "label": ssid, "systems": ["PC"], "phone_os": None,
              "channels": [{"channel": "wifi_hotspot", "observations": 2}],
              "identifiers": [{"kind": "app_device_id", "value": f"wifi_ap_mac:{bssid}"}]}
    records = [
        {"device": dev_id, "event": "hotspot_first_connected", "datetime": first, "source_path": "SOFTWARE",
         "data": {"ssid": ssid, "profile_name": ssid}},
        {"device": dev_id, "event": "hotspot_last_connected", "datetime": last, "source_path": "SOFTWARE",
         "data": {"ssid": ssid, "profile_name": f"{ssid} 2"}},
    ]
    return device, records


def test_hotspot_only_phones_are_grouped_by_network():
    a, ra = _hotspot("DEV-002", "Galaxy S22 5G", "aae3810cfca2", "2025-11-09T11:29:03+00:00",
                     "2025-11-09T12:48:27+00:00")
    b, rb = _hotspot("DEV-003", "Galaxy S22 5G", "06df76316183", "2025-11-08T05:46:21+00:00",
                     "2025-11-08T05:46:21+00:00")
    c, rc = _hotspot("DEV-004", "Jane’s iPhone", "3c22fb9a104e", "2025-12-01T08:00:00+00:00",
                     "2025-12-02T08:00:00+00:00")
    phone = {"id": "DEV-001", "label": "Pixel 8", "channels": [{"channel": "usb"}, {"channel": "wifi_hotspot"}],
             "identifiers": [], "systems": ["PC"]}
    nets = content.hotspot_networks(ra + rb + rc, [a, b, c, phone])

    assert [n["ssid"] for n in nets] == ["Galaxy S22 5G", "Jane’s iPhone"]  # most access points first
    galaxy = nets[0]
    assert galaxy["devices"] == ["DEV-002", "DEV-003"] and galaxy["likely_phone"] == "Galaxy S22"
    assert [s["bssid"] for s in galaxy["sessions"]] == ["06:DF:76:31:61:83", "AA:E3:81:0C:FC:A2"]  # by time
    assert all(s["randomised"] and s["maker"] == "randomised (no maker)" for s in galaxy["sessions"])
    assert galaxy["first"].startswith("2025-11-08") and galaxy["last"].startswith("2025-11-09T12:48")
    iphone = nets[1]
    assert iphone["likely_phone"] == "iPhone" and iphone["os"] == "ios" and not iphone["randomised"]
    assert not content.is_hotspot_only(phone)  # also seen over USB: a phone, not a hotspot row
