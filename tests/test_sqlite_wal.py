import shutil
import sqlite3

from connected_phone_forensics.readers.sqlite_wal import read_wal, recover_table_versions


def _scenario(tmp_path, page_size=4096):
    db = tmp_path / "live.db"
    c = sqlite3.connect(db, isolation_level=None)
    c.execute(f"PRAGMA page_size={page_size}")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE notifications(id INTEGER PRIMARY KEY AUTOINCREMENT, notification_id TEXT, "
              "json TEXT, post_time INTEGER)")
    for i in range(1, 4):
        c.execute("INSERT INTO notifications(notification_id, json, post_time) VALUES (?,?,?)",
                  (f"n{i}", f'{{"title":"original {i}"}}', 133_000_000_000_000_000 + i))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # rows 1-3 now in the main file
    c.execute("UPDATE notifications SET json='{\"title\":\"edited 2\"}' WHERE id=2")
    c.execute("DELETE FROM notifications WHERE id=3")
    big = '{"title":"long","text":"' + "x" * (page_size * 3) + '"}'
    c.execute("INSERT INTO notifications(notification_id, json, post_time) VALUES ('n4', ?, 5)", (big,))
    # Image the files while the WAL is still live, as an acquisition would.
    ev = tmp_path / "ev"
    ev.mkdir()
    for suffix in ("", "-wal"):
        shutil.copy(tmp_path / f"live.db{suffix}", ev / f"live.db{suffix}")
    c.close()
    return ev / "live.db", big


def test_wal_frames_have_valid_checksums(tmp_path):
    db, _ = _scenario(tmp_path)
    page_size, frames = read_wal((db.parent / "live.db-wal").read_bytes())
    assert page_size == 4096 and frames
    assert all(f.valid and f.committed for f in frames)


def test_recovers_deleted_and_superseded_rows(tmp_path):
    db, big = _scenario(tmp_path)
    rec = recover_table_versions(db, "notifications")
    by = {}
    for v in rec.versions:
        by.setdefault(v.rowid, []).append(v)
    titles = {rid: sorted(str(v.values[2]) for v in vs) for rid, vs in by.items()}
    assert titles[2] == ['{"title":"edited 2"}', '{"title":"original 2"}']  # both versions
    assert titles[3] == ['{"title":"original 3"}']                           # deleted row still found
    assert by[3][0].source == "db"
    assert by[4][0].source == "wal" and by[4][0].values[2] == big            # overflow pages followed
    assert by[1][0].values[0] is None  # INTEGER PRIMARY KEY alias is stored as NULL; rowid carries it
    # The live database agrees on the current state.
    con = sqlite3.connect(f"file:{tmp_path / 'live.db'}?mode=ro", uri=True)
    assert [r[0] for r in con.execute("SELECT id FROM notifications ORDER BY id")] == [1, 2, 4]


def test_small_pages_and_missing_table(tmp_path):
    db, _ = _scenario(tmp_path, page_size=1024)
    assert len(recover_table_versions(db, "notifications").versions) == 5
    assert recover_table_versions(db, "nope").note == "table not found"


def test_database_living_entirely_in_the_wal(tmp_path):
    db = tmp_path / "new.db"
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    c.execute("INSERT INTO t(v) VALUES ('only in wal')")
    ev = tmp_path / "ev"
    ev.mkdir()
    (ev / "new.db").write_bytes(b"")  # main file never checkpointed
    shutil.copy(tmp_path / "new.db-wal", ev / "new.db-wal")
    c.close()
    rec = recover_table_versions(ev / "new.db", "t")
    assert [v.values[1] for v in rec.versions] == ["only in wal"]


def test_utf16_database_text_is_decoded(tmp_path):
    db = tmp_path / "u16.db"
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("PRAGMA encoding='UTF-16le'")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    c.execute("INSERT INTO t(v) VALUES ('héllo'), ('wörld')")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("DELETE FROM t WHERE v='héllo'")
    ev = tmp_path / "ev"
    ev.mkdir()
    for suffix in ("", "-wal"):
        shutil.copy(tmp_path / f"u16.db{suffix}", ev / f"u16.db{suffix}")
    c.close()
    rec = recover_table_versions(ev / "u16.db", "t")
    assert sorted(v.values[1] for v in rec.versions) == ["héllo", "wörld"]


def test_recovery_failure_is_reported_not_silent(tmp_path, monkeypatch):
    from connected_phone_forensics.plugins import win_phonelink
    from connected_phone_forensics.readers.sqlite import open_db
    from connected_phone_forensics.readers.sqlite_wal import Recovery

    db, _ = _scenario(tmp_path)
    monkeypatch.setattr(win_phonelink, "recover_table_versions",
                        lambda path, table: Recovery(table, [], 0, 0, "no database header"))
    warnings = []
    with open_db(db) as safe:
        rows = win_phonelink.recovered_rows(safe, db, "notifications", lambda msg, path: warnings.append(msg))
    assert rows == []
    assert warnings == ["deleted/superseded-row recovery not performed for table notifications: no database header"]
