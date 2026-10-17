import json

from tests.test_apple import UDID, _windows
from tether.cli import main
from tether.engine import scan
from tether.output import write_all
from tether.validate import validate


def test_validate_scores_found_missing_time_and_device(tmp_path):
    _windows(tmp_path / "ev" / "C")
    write_all(scan(tmp_path / "ev"), tmp_path / "out", html=False)
    truth = {
        "dataset": "synthetic",
        "devices": {"phone1": {"udid": UDID}, "ghost": {"serial": "NOPE"}},
        "expected": [
            {"id": "paired", "device": "phone1", "event": "pairing_certificate_issued",
             "time": "2024-03-15T12:01:00Z", "tolerance_seconds": 120},
            {"id": "backup", "device": "phone1", "channel": "backup", "contains": "encrypted"},
            {"id": "late", "event": "pairing_certificate_issued", "time": "2024-03-20T00:00:00Z"},
            {"id": "wrongdev", "device": "ghost", "event": "device_backup"},
            {"id": "absent", "event": "sms"},
        ],
    }
    tp = tmp_path / "truth.json"
    tp.write_text(json.dumps(truth))
    report = validate(tp, tmp_path / "out")
    status = {i["id"]: i["status"] for i in report["items"]}
    assert status == {"paired": "found", "backup": "found", "late": "time_off",
                      "wrongdev": "wrong_device", "absent": "missing"}
    assert report["summary"]["found"] == 2
    assert main(["validate", str(tp), str(tmp_path / "out")]) == 1
