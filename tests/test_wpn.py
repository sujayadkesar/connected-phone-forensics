import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from connected_phone_forensics._synth.phonelink import ft
from connected_phone_forensics.engine import scan
from connected_phone_forensics.plugins.win_wpn import toast_text

UTC = timezone.utc
TOAST = ('<toast launch="phonelink://message?thread=10"><visual><binding template="ToastGeneric">'
         '<text>Jordan Lee</text><text>Running 10 min late</text></binding></visual></toast>')


def test_toast_text():
    assert toast_text(TOAST) == (["Jordan Lee", "Running 10 min late"], "phonelink://message?thread=10")
    assert toast_text(b"not xml <b>hi</b>")[0] == ["not xml  hi"]


def _wpn(path: Path):
    path.parent.mkdir(parents=True)
    tmp = path.parent / "build.db"
    c = sqlite3.connect(tmp, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE NotificationHandler(RecordId INTEGER PRIMARY KEY, PrimaryId TEXT, WNSId TEXT, "
              "HandlerType TEXT, WNFEventName INT, SystemDataPropertySet BLOB, CreatedTime DATETIME, "
              "ModifiedTime DATETIME, ParentId INT, ContainerSid TEXT)")
    c.execute("CREATE TABLE Notification(\"Order\" INTEGER PRIMARY KEY, Id INT, HandlerId INT, ActivityId TEXT, "
              "Type TEXT, Payload BLOB, Tag TEXT, \"Group\" TEXT, ExpiryTime INT, ArrivalTime INT, DataVersion INT, "
              "PayloadType TEXT, BootId INT, ExpiresOnReboot BOOLEAN)")
    c.execute("INSERT INTO NotificationHandler VALUES (122, 'Microsoft.YourPhone_8wekyb3d8bbwe!App', NULL, "
              "'app:immersive', NULL, NULL, '2025-06-01 10:00:00', '2025-06-14 18:00:00', NULL, NULL)")
    c.execute("INSERT INTO NotificationHandler VALUES (5, 'Microsoft.WindowsStore_8wekyb3d8bbwe!App', NULL, "
              "'app:immersive', NULL, NULL, '2024-01-01 00:00:00', NULL, NULL, NULL)")
    arrive = ft(datetime(2025, 6, 14, 18, 5, tzinfo=UTC))
    c.execute("INSERT INTO Notification VALUES (1, 1, 122, NULL, 'toast', ?, 't1', 'g', ?, ?, 1, 'Xml', 0, 0)",
              (TOAST.encode(), arrive + 10**12, arrive))
    c.execute("INSERT INTO Notification VALUES (2, 2, 122, NULL, 'toast', ?, 't2', 'g', ?, ?, 1, 'Xml', 0, 0)",
              (TOAST.replace("Running 10 min late", "Code 482913").encode(), arrive, arrive + 600))
    c.execute("INSERT INTO Notification VALUES (3, 3, 5, NULL, 'toast', ?, 't3', 'g', 0, ?, 1, 'Xml', 0, 0)",
              (b"<toast><visual><binding><text>Store update</text></binding></visual></toast>", arrive))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("DELETE FROM Notification WHERE \"Order\"=2")  # expired
    for s in ("", "-wal"):
        shutil.copy(str(tmp) + s, str(path) + s)
    c.close()


def test_wpn_phonelink_toasts_and_recovery(tmp_path):
    root = tmp_path / "C"
    (root / "Windows" / "System32" / "config").mkdir(parents=True)
    (root / "Users" / "alex").mkdir(parents=True)
    (root / "Users" / "alex" / "NTUSER.DAT").write_bytes(b"regf")
    _wpn(root / "Users" / "alex" / "AppData" / "Local" / "Microsoft" / "Windows" / "Notifications" / "wpndatabase.db")
    result = scan(tmp_path, plugin_ids=["win_wpn_phonelink"])
    ev = {o.event: o for o in result.observations}
    assert set(ev) == {"phonelink_toast_handler_registered", "phonelink_toast", "phonelink_toast_recovered"}
    assert ev["phonelink_toast_handler_registered"].timestamp == datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
    assert ev["phonelink_toast"].data["texts"] == ["Jordan Lee", "Running 10 min late"]
    assert ev["phonelink_toast_recovered"].data["texts"][1] == "Code 482913"
    assert all("Store" not in o.message for o in result.observations)


REAL = os.environ.get("CPF_MUSEUM")


@pytest.mark.skipif(not REAL, reason="CPF_MUSEUM not set")
@pytest.mark.parametrize("ver", ["Win10", "Win11"])
def test_real_wpndatabase(tmp_path, ver):
    src = Path(REAL) / "Windows" / "WindowsNotificationDB" / ver / "wpndatabase.db"
    dst = tmp_path / "C" / "Users" / "u" / "AppData" / "Local" / "Microsoft" / "Windows" / "Notifications"
    dst.mkdir(parents=True)
    (tmp_path / "C" / "Users" / "u" / "NTUSER.DAT").write_bytes(b"regf")
    (tmp_path / "C" / "Windows" / "System32" / "config").mkdir(parents=True)
    for s in ("", "-wal", "-shm"):
        if Path(str(src) + s).exists():
            shutil.copy(str(src) + s, dst / ("wpndatabase.db" + s))
    result = scan(tmp_path, plugin_ids=["win_wpn_phonelink"])
    reg = [o for o in result.observations if o.event == "phonelink_toast_handler_registered"]
    assert len(reg) == 1 and reg[0].timestamp.year == 2022
