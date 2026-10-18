from datetime import datetime, timedelta, timezone

from connected_phone_forensics.engine import scan
from connected_phone_forensics.plugins.kdeconnect import _identity_created
from tests.fixtures.certs import make_cert_pem

UTC = timezone.utc


def _trusted(cert_pem: bytes) -> str:
    escaped = cert_pem.decode().replace("\n", "\\n")
    return (
        "[a1b2c3d4_e5f6_4a1b_9c3d_e5f6a7b8c9d0]\n"
        "name=Pixel 8\n"
        "type=phone\n"
        "protocolVersion=8\n"
        f'certificate="{escaped}"\n'
    )


def test_identity_created_android_132():
    # Phone in UTC+2 created its identity on 2025-03-15 (local midnight = 22:00Z the day before).
    nb = datetime(2024, 3, 14, 22, 0, tzinfo=UTC)
    got = _identity_created(nb, datetime(2035, 3, 14, 22, 0, tzinfo=UTC), "01")
    assert got["created"] == datetime(2025, 3, 14, 22, 0, tzinfo=UTC)
    assert "1.32" in got["generator"] and got["precision"] == "day" and got["phone_os"] == "android"
    assert got["local_day"] == "2025-03-15" and got["utc_offset_assumed"] == "+02:00"
    assert _identity_created(nb, datetime(2030, 1, 1, tzinfo=UTC)) is None  # unknown generator


def test_identity_created_android_115_to_131():
    # notAfter = notBefore + 10 years; India (UTC+05:30) local midnight is 18:30Z.
    nb = datetime(2021, 6, 9, 18, 30, tzinfo=UTC)
    got = _identity_created(nb, datetime(2031, 6, 9, 18, 30, tzinfo=UTC), "01")
    assert "1.15-1.31" in got["generator"] and got["local_day"] == "2022-06-10"
    assert got["created"] == datetime(2022, 6, 9, 18, 30, tzinfo=UTC)


def test_identity_created_android_calendar_era():
    nb = datetime(2018, 5, 2, 13, 7, 41, tzinfo=UTC)
    got = _identity_created(nb, datetime(2028, 5, 2, 13, 7, 41, tzinfo=UTC), "01")
    assert "1.0-1.14" in got["generator"] and got["precision"] == "second"
    assert got["created"] == datetime(2019, 5, 2, 13, 7, 41, tzinfo=UTC)


def test_identity_created_leap_day_is_flagged():
    # Created 2028-02-29 (or 02-28) in UTC: LocalDate.minusYears(1) gives 2027-02-28 either way.
    got = _identity_created(datetime(2027, 2, 28, tzinfo=UTC), datetime(2038, 2, 28, tzinfo=UTC), "01")
    assert got["local_day"] == "2028-02-28" and "29 February" in got["note"]
    assert _identity_created(datetime(2024, 6, 1, tzinfo=UTC), datetime(2035, 6, 1, tzinfo=UTC))["note"] is None


def test_identity_created_ios_and_desktop():
    now = datetime(2025, 9, 1, 8, 15, 27, tzinfo=UTC)
    nb, na = now - timedelta(days=365), now + timedelta(days=3650)
    ios = _identity_created(nb, na, "0a")
    assert ios["created"] == now and ios["phone_os"] == "ios" and "kdeconnect-ios" in ios["generator"]
    desktop = _identity_created(nb, na, "5f3c9a0b1d2e3f405162738495a6b7c8d9e0f1a2")
    assert desktop["created"] == now and desktop["phone_os"] is None and "desktop" in desktop["generator"]


def test_linux_kdeconnect(tmp_path):
    root = tmp_path / "fedora"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text('PRETTY_NAME="Fedora Linux 41"\n')
    kc = root / "home" / "sam" / ".config" / "kdeconnect"
    (kc / "a1b2c3d4_e5f6_4a1b_9c3d_e5f6a7b8c9d0" / "kdeconnect_sms").mkdir(parents=True)
    (kc / "config").write_text("[General]\nname=sam-laptop\n")
    cert = make_cert_pem(datetime(2024, 3, 14, 22, 0), datetime(2035, 3, 14, 22, 0),
                         cn="a1b2c3d4_e5f6_4a1b_9c3d_e5f6a7b8c9d0")
    (kc / "trusted_devices").write_text(_trusted(cert))
    result = scan(tmp_path, plugin_ids=["linux_kdeconnect"])
    events = {o.event: o for o in result.observations}
    assert set(events) == {"kdeconnect_identity_created", "kdeconnect_paired"}
    paired = events["kdeconnect_paired"]
    assert paired.data["plugin_folders"] == ["kdeconnect_sms"]
    assert paired.data["this_computer_name"] == "sam-laptop"
    assert paired.user == "sam" and paired.device_class == "phone" and paired.phone_os == "android"
    assert "1.32" in paired.data["identity_generator"]
    assert events["kdeconnect_identity_created"].timestamp == datetime(2025, 3, 14, 22, 0, tzinfo=UTC)
    dev = result.graph.devices[0]
    assert dev.label == "Pixel 8" and dev.confirmed
