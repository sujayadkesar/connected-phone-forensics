# Contributing to Connected Phone Forensics

Connected Phone Forensics is only as good as its parsers, and every parser is only as good as the
data it was checked against. Contributions of **sample data and validation
results are as valuable as code**.

## Ground rules for parsers

1. **Never touch evidence directly.** Open files only through the plugin
   `Context`: `ctx.sqlite()`, `ctx.hive()`, `ctx.plist()`, `ctx.read_bytes()`.
   These hash every file read, copy SQLite databases (with `-wal`/`-shm`)
   before opening them, and recover dirty registry hives from their transaction logs on a copy.
2. **Never fail silently.** Wrap per-file work in `with ctx.file_scope(path)`.
   If a file exists but its format or schema is not one you recognise, call
   `ctx.cover(path, UNRECOGNISED, note=...)` and move on. The examiner must
   see that a file was present and not parsed.
3. **Every fact carries provenance.** `ctx.prov(path, locator)` takes a locator
   precise enough to re-find the record by hand: `table=messages rowid=42`,
   `SYSTEM:\ControlSet001\...\LastSeen`, `plist:$.DeviceCertificate`.
4. **Timestamps are UTC and say what they mean.** Convert with
   `connected_phone_forensics.timeutil` (exact integer arithmetic, rejects implausible values).
   Set `timestamp_desc` to the artifact's own meaning ("Last Connected"), and
   set `time_source="filesystem"` for file metadata times.
5. **Identify phones with the right identifier kinds.** Hardware or vendor ids
   (`UDID`, `SERIAL`, `IMEI`, `BT_MAC`, `USB_SERIAL`, namespaced
   `APP_DEVICE_ID`) merge records into one device. Device names, models and
   phone numbers never merge anything. Do not upgrade a weak identifier to a
   strong kind to make a merge happen.
6. **State what is inferred.** If a field's meaning comes from your own
   testing rather than documentation, say so in the plugin docstring and in
   `timestamp_desc`.

## Writing a plugin

```python
from connected_phone_forensics.model import Channel, IdKind, Observation, Validation, ident
from connected_phone_forensics.plugin import Context, Plugin, register
from connected_phone_forensics.timeutil import from_cocoa


@register
class MacExample(Plugin):
    id = "mac_example"                     # unique, <os>_<artifact>
    name = "Example artifact"
    os = "macos"                           # windows | macos | linux
    channel = Channel.CONTINUITY
    description = __doc__ or ""            # shown in `cpf docs`
    paths = ["Users/*/Library/Example/example.db"]  # drives `cpf targets`
    references = ["https://..."]           # where the format is documented
    validation = Validation.SYNTHETIC

    def run(self, ctx: Context) -> None:
        for user, path in ctx.user_files("Library/Example/example.db"):
            with ctx.file_scope(path) as n, ctx.sqlite(path) as db:
                if not db.schema.has("devices", "name", "last_seen"):
                    ctx.cover(path, "unrecognised", note=f"schema {db.schema.fingerprint()}")
                    continue
                for row in db.select("devices", ["name", "serial", "last_seen"]):
                    ctx.emit(Observation(
                        artifact=self.id, event="example_seen",
                        message=f"{row['name']} seen",
                        channel=self.channel,
                        timestamp=from_cocoa(row["last_seen"]), timestamp_desc="Last Seen",
                        provenance=ctx.prov(path, f"table=devices rowid={row['_rowid_']}"),
                        device_ids=[ident(IdKind.SERIAL, row["serial"]),
                                    ident(IdKind.DEVICE_NAME, row["name"])],
                    ))
                    n[0] += 1
```

Drop the module into `connected_phone_forensics/plugins/`; it is discovered automatically.

`paths` must list every file the parser reads: collection targets (`cpf targets`) and live
acquisition (`cpf live`) copy exactly those files, with their SQLite and registry journal files.
If the parser also needs something a file pattern cannot name, such as the names of sub-folders it
lists, declare it in `acquire` (for example `acquire = ["Users/*/AppData/Local/kdeconnect/**"]`,
where a trailing `**` copies the whole folder, empty sub-folders included).

## Validation levels

Each parser declares how far it has been checked. The level is printed in
every report next to its results.

| Level | Meaning |
|---|---|
| `synthetic` | Tested only against fixtures built from public documentation. |
| `public_sample` | Tested against a public real-world sample (e.g. the DFIR Artifact Museum). |
| `ground_truth` | Tested against data created on real devices with recorded actions (see `docs/VALIDATION.md`). |

A parser moves up a level only with a test that proves it: a fixture from a
public sample, or a ground-truth dataset plus the expected output.

## Tests

* Build fixtures in code (see `tests/fixtures/regf.py` for registry hives and
  `tests/fixtures/certs.py` for certificates) so every byte of test data is
  reviewable. Do not commit evidence of unknown provenance or containing real
  people's data.
* Real-data tests read from `CPF_REALDATA` and are skipped when it is unset.
* `python -m pytest` and `python -m ruff check connected_phone_forensics tests` must pass.
