import shutil
import sqlite3
import struct

import pytest

from connected_phone_forensics.readers.sqlite_wal import (
    _wal_checksum,
    read_wal,
    recover_table_versions,
)


def _live(tmp_path, *ddl, page_size=4096):
    c = sqlite3.connect(tmp_path / "live.db", isolation_level=None)
    c.execute(f"PRAGMA page_size={page_size}")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    for sql in ddl:
        c.execute(sql)
    return c


def _acquire(tmp_path, c):
    """Image the files while the WAL is still live, as an acquisition would."""
    ev = tmp_path / "ev"
    ev.mkdir()
    for suffix in ("", "-wal"):
        shutil.copy(tmp_path / f"live.db{suffix}", ev / f"live.db{suffix}")
    c.close()
    return ev / "live.db"


def _scenario(tmp_path, page_size=4096):
    c = _live(tmp_path, "CREATE TABLE notifications(id INTEGER PRIMARY KEY AUTOINCREMENT, notification_id TEXT, "
                        "json TEXT, post_time INTEGER)", page_size=page_size)
    for i in range(1, 4):
        c.execute("INSERT INTO notifications(notification_id, json, post_time) VALUES (?,?,?)",
                  (f"n{i}", f'{{"title":"original {i}"}}', 133_000_000_000_000_000 + i))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # rows 1-3 now in the main file
    c.execute("UPDATE notifications SET json='{\"title\":\"edited 2\"}' WHERE id=2")
    c.execute("DELETE FROM notifications WHERE id=3")
    big = '{"title":"long","text":"' + "x" * (page_size * 3) + '"}'
    c.execute("INSERT INTO notifications(notification_id, json, post_time) VALUES ('n4', ?, 5)", (big,))
    return _acquire(tmp_path, c), big


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


# -- attribution: a page belongs to a table only while that table's b-tree reaches it ------------

@pytest.mark.parametrize("checkpointed", [True, False], ids=["main-file", "wal"])
def test_page_reused_by_another_table_is_not_attributed_to_it(tmp_path, checkpointed):
    # Both tables have three columns, so the record shape cannot tell their rows apart.
    c = _live(tmp_path, "CREATE TABLE note(title TEXT, body TEXT, n INTEGER)",
              "CREATE TABLE contact(name TEXT, number TEXT, n INTEGER)")
    for i in range(300):
        c.execute("INSERT INTO contact VALUES (?,?,?)", (f"contact {i}", f"+1555{i:07d}", i))
    if checkpointed:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # contact pages now in the main file
    c.execute("DELETE FROM contact")
    freed = c.execute("PRAGMA freelist_count").fetchone()[0]
    for i in range(250):
        c.execute("INSERT INTO note VALUES (?,?,?)", (f"note {i}", "text", i))
    assert c.execute("PRAGMA freelist_count").fetchone()[0] < freed  # note took over contact's pages
    db = _acquire(tmp_path, c)
    notes = recover_table_versions(db, "note")
    assert sorted(v.values[0] for v in notes.versions) == sorted(f"note {i}" for i in range(250))
    # The deleted contacts are still recovered, from the states in which the pages were contact's.
    contacts = recover_table_versions(db, "contact")
    assert sorted(v.values[0] for v in contacts.versions) == sorted(f"contact {i}" for i in range(300))
    assert notes.incomplete == contacts.incomplete == 0


def test_rows_on_pages_freed_after_the_checkpoint_are_recovered(tmp_path):
    c = _live(tmp_path, "CREATE TABLE notifications(id INTEGER PRIMARY KEY, json TEXT)")
    for i in range(1, 201):
        c.execute("INSERT INTO notifications VALUES (?,?)", (i, f'{{"title":"notification {i}"}}' + " " * 100))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("DELETE FROM notifications WHERE id BETWEEN 20 AND 180")  # the user clears them
    db = _acquire(tmp_path, c)
    rec = recover_table_versions(db, "notifications")
    assert sorted(v.rowid for v in rec.versions) == list(range(1, 201))
    assert rec.incomplete == 0


def test_rows_on_pages_freed_within_the_wal_are_recovered(tmp_path):
    c = _live(tmp_path, "CREATE TABLE notifications(id INTEGER PRIMARY KEY, json TEXT)")
    c.execute("BEGIN")
    for i in range(1, 201):
        c.execute("INSERT INTO notifications VALUES (?,?)", (i, f'{{"title":"notification {i}"}}' + " " * 100))
    c.execute("COMMIT")
    c.execute("DELETE FROM notifications WHERE id BETWEEN 20 AND 180")
    db = _acquire(tmp_path, c)
    assert sorted(v.rowid for v in recover_table_versions(db, "notifications").versions) == list(range(1, 201))


def test_rows_on_a_root_page_that_became_an_interior_page_are_recovered(tmp_path):
    c = _live(tmp_path, "CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(1, 4):
        c.execute("INSERT INTO t(v) VALUES (?)", (f"original {i}",))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("DELETE FROM t WHERE id=2")
    c.execute("BEGIN")
    for _ in range(50):
        c.execute("INSERT INTO t(v) VALUES (?)", ("x" * 200,))  # the root leaf splits
    c.execute("COMMIT")
    db = _acquire(tmp_path, c)
    _, frames = read_wal((db.parent / "live.db-wal").read_bytes())
    # Page 2, the root, is a leaf in the main file and an interior page now.
    assert db.read_bytes()[4096] == 0x0D and [f for f in frames if f.page == 2][-1].data[0] == 0x05
    assert "original 2" in [v.values[1] for v in recover_table_versions(db, "t").versions]


def _messages_then_contacts(tmp_path):
    """Messages in the main file; in the WAL most are deleted and contacts reuse their pages."""
    c = _live(tmp_path, "CREATE TABLE message(body TEXT, n INTEGER)", "CREATE TABLE contact(name TEXT, n INTEGER)")
    pad = " " * 60
    c.execute("BEGIN")
    for i in range(300):
        c.execute("INSERT INTO message VALUES (?,?)", (f"message {i}{pad}", i))
    c.execute("COMMIT")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # message leaves in the main file
    c.execute("BEGIN")
    for i in range(300, 400):
        c.execute("INSERT INTO message VALUES (?,?)", (f"message {i}{pad}", i))  # splits a leaf: new root
    c.execute("COMMIT")
    c.execute("DELETE FROM message WHERE n < 290")
    freed = c.execute("PRAGMA freelist_count").fetchone()[0]
    c.execute("BEGIN")
    for i in range(300):
        c.execute("INSERT INTO contact VALUES (?,?)", (f"contact {i}{pad}", i))  # reuses message's pages
    c.execute("COMMIT")
    assert c.execute("PRAGMA freelist_count").fetchone()[0] < freed
    return c


def _attributed(db, table, prefix):
    rec = recover_table_versions(db, table)
    assert all(str(v.values[0]).startswith(prefix) for v in rec.versions), f"rows of another table in {table}"
    return rec


def test_checkpoint_does_not_lend_later_page_images_to_earlier_states(tmp_path):
    c = _messages_then_contacts(tmp_path)
    # Copies every frame into the main file; the WAL is only restarted by the next write.
    assert c.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[0] == 0
    db = _acquire(tmp_path, c)
    rec = _attributed(db, "message", "message ")
    # After the first WAL transaction the root (in the WAL) points at the old leaves, whose images
    # for that state the checkpoint overwrote with contact rows: those must not come back as messages.
    assert sorted(v.values[1] for v in rec.versions) == list(range(290, 400))
    assert rec.incomplete == 1  # the overwritten state is reported as read in part
    contacts = _attributed(db, "contact", "contact ")
    assert sorted(v.values[1] for v in contacts.versions) == list(range(300))


def test_checkpoint_interrupted_part_way_does_not_mix_states(tmp_path):
    c = _messages_then_contacts(tmp_path)
    before = (tmp_path / "live.db").read_bytes()
    assert c.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[0] == 0
    db = _acquire(tmp_path, c)
    after = db.read_bytes()
    for k in range(len(after) // 4096 + 1):
        # A checkpoint copies pages in ascending order; this one stopped after page k.
        torn = after[:k * 4096] + before[k * 4096:]
        db.write_bytes(torn + after[len(torn):])
        messages = _attributed(db, "message", "message ")
        _attributed(db, "contact", "contact ")
        if k == 0:  # nothing copied yet: every message is still there
            assert sorted(v.values[1] for v in messages.versions) == list(range(400))


def _append_commit(wal, pgno, data):
    """Append a one-page committed transaction to a WAL, with salts and checksums as SQLite writes them."""
    raw = wal.read_bytes()
    magic, _version, page_size, _seq, salt1, salt2 = struct.unpack(">6I", raw[:24])
    _, frames = read_wal(raw)
    assert frames and all(f.valid for f in frames)
    end = 32 + len(frames) * (24 + page_size)
    s0, s1 = struct.unpack(">II", raw[end - page_size - 8:end - page_size])
    head = struct.pack(">II", pgno, [f.commit_size for f in frames if f.commit_size][-1])
    s0, s1 = _wal_checksum(head, s0, s1, bool(magic & 1))
    s0, s1 = _wal_checksum(data, s0, s1, bool(magic & 1))
    wal.write_bytes(raw[:end] + head + struct.pack(">4I", salt1, salt2, s0, s1) + data)


def test_page_rewritten_unchanged_later_is_still_read_in_earlier_states(tmp_path):
    # SQLite can write a page back unchanged (seen in Phone Link's contacts.db). That frame
    # has the main file's bytes but is no sign of a checkpoint: lower pages written by then
    # are not in the main file as of that commit.
    c = _live(tmp_path, "CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    c.execute("BEGIN")
    for i in range(1, 301):
        c.execute("INSERT INTO t VALUES (?,?)", (i, f"row {i} " + "x" * 60))
    c.execute("COMMIT")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("DELETE FROM t WHERE id <= 290")  # these rows, and the leaves they filled, are gone
    db = _acquire(tmp_path, c)
    main = db.read_bytes()
    root = main[4096:8192]  # page 2, the table's root, an interior page
    assert root[0] == 0x05
    first_leaf = struct.unpack(">I", root[struct.unpack(">H", root[12:14])[0]:][:4])[0]
    leaf = main[(first_leaf - 1) * 4096:first_leaf * 4096]
    _append_commit(db.parent / "live.db-wal", first_leaf, leaf)
    rec = recover_table_versions(db, "t")
    assert sorted(v.rowid for v in rec.versions) == list(range(1, 301))
    assert rec.incomplete == 0


def test_uncommitted_transaction_is_counted_not_searched(tmp_path):
    c = _live(tmp_path, "CREATE TABLE call_history(call_id INTEGER PRIMARY KEY, phone_number TEXT, "
                        "call_type INTEGER)")
    c.execute("BEGIN")
    for i in range(1, 301):
        c.execute("INSERT INTO call_history VALUES (?,?,2)", (i, f"+1415555{i:04d}"))
    c.execute("COMMIT")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("PRAGMA cache_size=2")
    c.execute("BEGIN")
    c.execute("UPDATE call_history SET phone_number='+15557777', call_type=3")  # spills pages to the WAL
    db = _acquire(tmp_path, c)  # imaged mid-transaction; closing rolls it back
    rec = recover_table_versions(db, "call_history")
    assert rec.uncommitted_frames > 0
    assert sorted(v.values[1] for v in rec.versions) == [f"+1415555{i:04d}" for i in range(1, 301)]


def test_record_wider_than_the_table_is_not_mapped_onto_its_columns(tmp_path, monkeypatch):
    from connected_phone_forensics.plugins import win_phonelink
    from connected_phone_forensics.readers.sqlite import open_db
    from connected_phone_forensics.readers.sqlite_wal import Recovery, RowVersion

    db, _ = _scenario(tmp_path)
    wide = RowVersion(9, [None, "n9", '{"title":"wide"}', 9, "one field too many"], "db", None)
    monkeypatch.setattr(win_phonelink, "recover_table_versions", lambda path, table: Recovery(table, [wide], 0, 0))
    warnings = []
    with open_db(db) as safe:
        rows = win_phonelink.recovered_rows(safe, db, "notifications", lambda msg, path: warnings.append(msg))
    assert rows == []
    assert warnings == ["1 recovered record(s) of table notifications have more fields than the table has "
                        "columns and were not reported"]


def test_partial_recovery_is_reported(tmp_path):
    from connected_phone_forensics.plugins import win_phonelink
    from connected_phone_forensics.readers.sqlite import open_db

    c = _live(tmp_path, "CREATE TABLE call_history(call_id INTEGER PRIMARY KEY, phone_number TEXT)")
    c.execute("BEGIN")
    for i in range(1, 301):
        c.execute("INSERT INTO call_history VALUES (?,?)", (i, f"+1415555{i:04d}"))
    c.execute("COMMIT")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.execute("PRAGMA cache_size=2")
    c.execute("BEGIN")
    c.execute("UPDATE call_history SET phone_number='+15557777'")  # spills pages to the WAL
    db = _acquire(tmp_path, c)
    warnings = []
    with open_db(db) as safe:
        rows = win_phonelink.recovered_rows(safe, db, "call_history", lambda msg, path: warnings.append(msg))
    assert rows == []  # the never-committed numbers are not reported as earlier versions
    assert len(warnings) == 1
    assert "transaction that never committed were not searched for rows of table call_history" in warnings[0]
