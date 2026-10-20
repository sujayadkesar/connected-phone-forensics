"""Saving recovered files: byte-identical copies, hash checks, a manifest, never overwriting."""

import csv
import hashlib
import json
import os
from datetime import datetime, timezone

from connected_phone_forensics import export


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _case(tmp_path):
    root = tmp_path / "case"
    folder = root / "exports" / "PC" / "win_phonelink" / "dev"
    (folder / "photos").mkdir(parents=True)
    (folder / "rcs").mkdir(parents=True)
    one, two = b"\xff\xd8\xff\xe0 one", b"\xff\xd8\xff\xe0 two"
    (folder / "photos" / "IMG_1.jpg").write_bytes(one)
    (folder / "rcs" / "IMG_1.jpg").write_bytes(two)  # the same name from another table
    base = {"phone": "Pixel 8", "device": "DEV-001", "system": "PC", "user": "alex", "artifact": "win_phonelink",
            "source_path": "Users/alex/photos.db", "sha256": "ab" * 32}
    items = [
        {**base, "category": "media", "name": "IMG_1.jpg", "time": "2025-01-25T13:05:50+00:00", "status": "",
         "file": "PC/win_phonelink/dev/photos/IMG_1.jpg", "file_sha256": _sha(one), "locator": "table=media rowid=1"},
        {**base, "category": "media", "name": "IMG_1.jpg", "time": None, "status": "Deleted, recovered",
         "file": "PC/win_phonelink/dev/rcs/IMG_1.jpg", "file_sha256": "0" * 64, "locator": "table=rcs rowid=9"},
        {**base, "category": "media", "name": "gone.jpg", "file": "PC/win_phonelink/dev/photos/gone.jpg"},
        {**base, "category": "messages", "text": "hello", "file": ""},
    ]
    return root, items, one, two


def test_saved_copies_are_identical_checked_and_described(tmp_path):
    root, items, one, two = _case(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()
    res = export.save_files(root, items, dest, title="Case 7 - photos", case={"case": "Case 7", "examiner": "AB"})

    assert res.folder.parent == dest and res.folder.name.startswith("Case 7 - photos ")
    assert len(res.saved) == 2 and len(res.skipped) == 1 and len(res.mismatched) == 1
    first = res.folder / "Photos and files" / "Pixel 8" / "IMG_1.jpg"
    second = res.folder / "Photos and files" / "Pixel 8" / "IMG_1 (2).jpg"  # renamed, never replaced
    assert first.read_bytes() == one and second.read_bytes() == two
    assert not os.access(first, os.W_OK)  # read-only
    when = datetime.fromtimestamp(first.stat().st_mtime, timezone.utc)
    assert when.strftime("%Y-%m-%d %H:%M:%S") == "2025-01-25 13:05:50"  # the record's time

    with open(res.folder / "manifest.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    by_name = {r["saved_as"]: r for r in rows}
    ok = by_name["Photos and files/Pixel 8/IMG_1.jpg"]
    assert ok["hash_check"].startswith("matches") and ok["sha256"] == _sha(one)
    assert ok["md5"] == hashlib.md5(one).hexdigest() and ok["status"] == "Present (live record)"  # noqa: S324
    assert ok["record"] == "table=media rowid=1" and ok["source_path"] == "Users/alex/photos.db"
    bad = by_name["Photos and files/Pixel 8/IMG_1 (2).jpg"]
    assert bad["hash_check"].startswith("DIFFERS") and bad["status"] == "Deleted, recovered"
    assert bad["modified_time_set_to"] == ""  # no record time: the copy keeps its own

    sums = (res.folder / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    for line in sums:
        digest, name = line.split(" *", 1)
        assert _sha((res.folder / name).read_bytes()) == digest
    meta = json.loads((res.folder / "manifest.json").read_text(encoding="utf-8"))
    assert meta["case"] == "Case 7" and meta["saved"] == 2 and meta["skipped"][0]["item"] == "gone.jpg"
    assert "DO NOT" in (res.folder / "Read me.txt").read_text(encoding="utf-8")

    again = export.save_files(root, items[:1], dest, title="Case 7 - photos")
    assert again.folder != res.folder and first.read_bytes() == one  # a new folder every time


def test_file_names_from_evidence_are_made_safe():
    assert "/" not in export.safe_name("../../etc/passwd") and ":" not in export.safe_name("C:evil")
    assert export.safe_name("CON") == "_CON" and export.safe_name("  ...  ") == "_"
    assert export.with_files([{"file": "a"}, {"file": ""}, {}]) == [{"file": "a"}]


def test_cli_saves_a_scanned_cases_files(tmp_path, capsys):
    from connected_phone_forensics import cli
    from connected_phone_forensics.demo import build_demo_evidence
    from connected_phone_forensics.engine import scan
    from connected_phone_forensics.output import write_all

    evidence = build_demo_evidence(tmp_path / "case")
    out = tmp_path / "out"
    write_all(scan(evidence, export_dir=out / "exports"), out, html=False, pdf=False, xlsx=False)
    assert cli.main(["save-files", str(out), "-o", str(tmp_path / "saved"), "--kind", "contacts,apps"]) == 0
    folder = next((tmp_path / "saved").iterdir())
    with open(folder / "manifest.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows and {r["category"] for r in rows} <= {"contacts", "apps"}
    assert all(r["hash_check"].startswith("matches") for r in rows)
    assert "saved" in capsys.readouterr().err
    assert cli.main(["save-files", str(out), "-o", str(tmp_path / "saved"), "--kind", "nonsense"]) == 2
