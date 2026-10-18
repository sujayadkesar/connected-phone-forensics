# Architecture

```
evidence dir ──► discover_systems ──► SystemRoot (windows | macos | linux, users, details)
                                           │
              for each plugin whose os matches, on a thread pool
                                           ▼
                        Plugin.run(Context) ──► Observation(s) + coverage + warnings
                                           │
                                           ▼
                              correlate.build_graph ──► DeviceGraph
                                           │
                                           ▼
                 output: report.html, timeline.jsonl/csv, devices.json, coverage.csv, manifest.json
```

## Evidence access (`connected_phone_forensics/evidence.py`)

`EvidenceFS` is a read-only view of a directory: a mounted image, an
extracted image, or a triage collection. Lookups are case-insensitive so a
Windows image extracted onto a case-sensitive filesystem behaves like
Windows. `discover_systems` walks a few levels down to find every system
root: a KAPE `C` folder, a Velociraptor `uploads/auto/C%3A` folder, an APFS
Data volume, a Linux root. Each root becomes a `SystemRoot`, labelled with
the computer name recorded in the evidence (Windows `ComputerName`, macOS
`SystemConfiguration`, Linux `/etc/hostname`).

## Readers (`connected_phone_forensics/readers/`)

| Reader | Guarantee |
|---|---|
| `sqlite.open_db` | Copies the database with `-wal`, `-shm`, `-journal` to a temp dir; replays the WAL into the copy; opens the copy read-only. The original is never opened by SQLite. |
| `sqlite_wal.recover_table_versions` | Pure-Python reader of the WAL and b-tree (no SQLite involved): returns every version of every row of a table found in the main file and in WAL frames, with checksum-validated frame attribution. Used to report deleted and superseded rows. |
| `registry.open_hive` | regipy on the hive, or on a copy recovered from its transaction logs; values are never truncated. `regf_log.replay` decides what to apply, following the REGF specification: only dirty hives, only hash-verified entries that continue the hive's sequence, LOG1/LOG2 in sequence order, old-format logs only with a matching timestamp. The result is reported as coverage per log and as warnings. |
| `plist` | Binary/XML plists and NSKeyedArchiver archives. |
| `x509` | Validity dates and names from PEM/DER certificates, standard library only. |

## Plugins (`connected_phone_forensics/plugins/`)

A plugin is a class registered with `@register`. It declares:

* `os`: the computer OS it applies to;
* `channel`: how the phone reached the computer (usb, bluetooth, pairing,
  backup, phone_link, continuity, account_sync, …);
* `paths`: glob patterns of files it reads, which drive `cpf targets`
  and `cpf docs`;
* `validation`: `synthetic`, `public_sample` or `ground_truth`.

Plugins read evidence only through the `Context`, which hashes every file,
records coverage for every file touched (`parsed`, `empty`, `unrecognised`,
`error`), isolates per-file failures, and exports recovered content (e.g.
cached photos) with hashes.

## Data model (`connected_phone_forensics/model.py`)

An `Observation` is one fact with a UTC timestamp (or none), a
`timestamp_desc`, a `time_source` (`artifact` or `filesystem`), the
identifiers of the phone it concerns, free-form `data`, and a `Provenance`
(source file, SHA-256, record locator, parser).

Identifiers have a kind and a strength:

| Strength | Kinds | Effect |
|---|---|---|
| strong | UDID, serial, IMEI, MEID, Bluetooth MAC, Wi-Fi MAC, USB serial, namespaced app device id | merge records into one device |
| link | phone number, ICCID, account | reported as possible matches only |
| weak | device name, model, OS version | descriptive; device names feed possible matches |

## Correlation (`connected_phone_forensics/correlate.py`)

Union-find over strong identifiers that a single record asserts together.
Every union keeps the record that caused it, which the report shows under
"How these records were linked". Records with only weak or link
identifiers form unconfirmed devices. Possible matches are computed per
device pair (shared name, shared phone number or account, adjacent MAC
addresses, matching MAC suffixes) and grouped into candidate groups for
the examiner. Nothing is merged on those heuristics; an examiner can
record a conclusion with `--assume-same`, which appears as an explicit
merge reason and in the manifest.

## Outputs (`connected_phone_forensics/output/`)

* `report.html`: a single offline file. Device cards and coverage are
  rendered statically. The timeline is column-encoded JSON rendered by a
  small inline script, capped at 100,000 rows with an explicit banner when
  truncated.
* `timeline.jsonl` / `timeline.csv`: Timesketch-compatible (`message`,
  `datetime`, `timestamp_desc`).
* `observations.jsonl`, `devices.json`, `coverage.csv`.
* `manifest.json`: tool version, command line, systems, plugins and their
  validation levels, examiner assertions, warnings, and the SHA-256 of
  every evidence file read.

## Scan engine (`connected_phone_forensics/engine.py`)

`scan()` runs every (system, plugin) pair on a thread pool (`workers`,
default one per CPU up to 8; `--workers 1` runs them one at a time). Results
are merged in a fixed order (systems as discovered, plugins by id), so the
output is identical whatever order the parsers finish in. Library warnings
are attributed to the plugin running on the thread that logged them.
Callers get one-line `progress` messages and structured `on_event` events
(`discovered`, `task_done`, `correlating`) on the calling thread, and can
stop a scan with a `cancel` event (`ScanCancelled` is raised once the
running parsers finish; nothing is written).

## Desktop app (`connected_phone_forensics/gui/`)

PySide6, installed with the package (entry point `cpf-gui`). The command line never imports Qt.

* `jobs.py`: scans, case loading and evidence previews run on Qt's thread
  pool; results come back as signals on the UI thread.
* `casedata.py`: the GUI works on the same records the output folder holds
  (`devices.json`, `observations.jsonl`, `coverage.csv`, `manifest.json`),
  built either from a finished `ScanResult` or by reading a saved folder, so
  a reopened case looks exactly like a fresh one.
* `models.py`: table models filter and sort a list of row indexes in plain
  Python rather than through `QSortFilterProxyModel`, so 100,000-record
  timelines filter in about a tenth of a second and only visible rows are
  formatted.
* `theme.py` / `widgets.py`: one palette per theme; widgets read colours
  when they paint, so switching theme needs no rebuild. Connection colours
  are a colour-blind-checked categorical palette and always sit next to a
  text label.
* `pages/`: New scan, scan progress, Overview, Devices, Timeline, Coverage,
  Issues.

## Packaging and releases (`packaging/`, `.github/workflows/release.yml`)

* `packaging/build.py` builds two single-file Windows executables with
  PyInstaller: the desktop app (no console) and the `cpf` command line.
  Parsers are discovered at run time, so their package is collected
  explicitly; Qt modules the app does not use are left out.
* `packaging/smoke_test.py` runs the built executables: the CLI must list
  every parser and scan the demo case; the desktop app runs the demo case
  through its own window off screen (`--smoke-test`).
* Pushing a tag `vX.Y.Z` runs the tests, builds and smoke-tests the
  executables on Windows, builds the wheel and source archive, and publishes
  a GitHub release with SHA-256 checksums and the CHANGELOG section as notes.
  Tags with a suffix (`v0.2.0-rc1`) become pre-releases.
* `scripts/make_brand_assets.py` renders the logo and `packaging/app.ico`
  from the app's own drawing code; `scripts/take_screenshots.py` regenerates
  the README screenshots from the demo case.
