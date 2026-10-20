"""Identifier decoding: what each value tells, derived from the value and bundled public tables."""

from connected_phone_forensics.decode import (
    decode,
    device_family,
    display_value,
    ios_from_build,
    luhn_ok,
    mac_vendor,
)


def test_post_2018_udid_names_the_chip_and_ecid():
    d = decode("udid", "00008140-0001541E3E46801C")
    assert d.summary.startswith("Apple A18 / A18 Pro chip: iPhone 16")
    assert ("Chip ID", "0x8140 · A18 / A18 Pro (T8140)") in d.fields
    assert ("ECID", "0x0001541E3E46801C (exclusive chip ID, unique per device)") in d.fields
    assert device_family("udid", "000081400001541E3E46801C") == "iPhone 16 family"


def test_legacy_udid_is_identified_as_pre_2018():
    d = decode("udid", "d31f3d97a44e1db55dd319e2cb0a8d33362ba2eb")
    assert "before the iPhone XS" in d.summary
    assert device_family("udid", "D31F3D97A44E1DB55DD319E2CB0A8D33362BA2EB") == "iPhone (2007–2018 model)"


def test_unknown_chip_id_is_not_guessed():
    d = decode("udid", "00009999-0001541E3E46801C")
    assert "not in this tool's chip table" in d.summary


def test_iphone_usb_serial_is_its_udid():
    d = decode("usb_serial", "00008120001A2B3C4D5E801E", "ios")
    assert d.summary.startswith("The iPhone's UDID") and "A16 Bionic" in d.summary
    assert display_value("usb_serial", "00008120001A2B3C4D5E801E") == "00008120-001A2B3C4D5E801E"


def test_android_usb_serial_warns_about_reused_values():
    d = decode("usb_serial", "E28E9340", "android")
    assert "adb serial" in d.summary and "0123456789ABCDEF" in d.note


def test_imei_check_digit_and_reporting_body():
    good = decode("imei", "490154203237518")
    assert good.valid is True and ("Check digit", "8 (valid)") in good.fields
    assert ("TAC (model code)", "49015420 · reporting body BNetzA (Germany)") in good.fields
    bad = decode("imei", "490154203237519")
    assert bad.valid is False and "check digit fails" in bad.summary
    assert luhn_ok("353912110123451")  # the demo case's IMEI


def test_mac_vendor_and_randomised_addresses():
    hw = decode("bt_mac", "a4:c3:37:5e:10:2b")
    assert hw.summary == "Hardware address from Apple, Inc."
    private = decode("wifi_mac", "6a:c3:e1:5e:10:2b")
    assert private.summary.startswith("Randomised (private) address")
    assert mac_vendor("A4-C3-37-00-00-00") == "Apple, Inc."
    assert display_value("bt_mac", "a4:c3:37:5e:10:2b") == "A4:C3:37:5E:10:2B"


def test_phone_numbers_and_sims_name_their_country():
    assert "(India)" in decode("phone_number", "+919876543210").summary
    assert "country is not recorded" in decode("phone_number", "09876543210").summary
    sim = decode("iccid", "8991101200003204510")
    assert ("Country", "+91 · India") in sim.fields


def test_apple_serials_models_and_builds():
    assert "randomised serial" in decode("serial", "HG7XQ2LM9P", "ios").summary
    old = decode("serial", "F2LXK0ABHG7F", "ios")
    assert old.summary == "Apple 12-character serial: made in week 42 of 2018" and old.note
    assert decode("model", "iPhone15,2").summary == "Apple iPhone 14 Pro"
    assert ios_from_build("22B83") == "iOS 18" and ios_from_build("23A341") == "iOS 26"


def test_app_ids_say_which_app_assigned_them():
    d = decode("app_device_id", "phonelink:f77b925a-6e5f-43e4-9baa-097c0b5d0711")
    assert d.kind_label == "Phone Link device ID"
    assert ("Value", "f77b925a-6e5f-43e4-9baa-097c0b5d0711") in d.fields
    hotspot = decode("app_device_id", "wifi_ap_mac:6a:c3:e1:5e:10:2b")
    assert hotspot.kind_label == "Hotspot BSSID" and any("randomised" in v for _k, v in hotspot.fields)
