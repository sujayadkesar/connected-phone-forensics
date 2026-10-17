from datetime import datetime, timezone

from tests.fixtures.certs import make_cert_pem
from tether.engine import scan
from tether.plugins.kdeconnect import _identity_created

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


def test_identity_created_shape():
    # Phone in UTC+2 created its identity on 2025-03-15 (local midnight = 22:00Z the day before).
    nb = datetime(2024, 3, 14, 22, 0, tzinfo=UTC)
    na = datetime(2035, 3, 14, 22, 0, tzinfo=UTC)
    assert _identity_created(nb, na) == datetime(2025, 3, 14, 22, 0, tzinfo=UTC)
    assert _identity_created(nb, datetime(2030, 1, 1, tzinfo=UTC)) is None  # other generator


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
    assert paired.user == "sam" and paired.device_class == "phone"
    assert events["kdeconnect_identity_created"].timestamp == datetime(2025, 3, 14, 22, 0, tzinfo=UTC)
    dev = result.graph.devices[0]
    assert dev.label == "Pixel 8" and dev.confirmed
