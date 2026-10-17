# Architecture

```
evidence dir ──► discover_systems ──► SystemRoot (windows | macos | linux, users, details)
                                           │
                     for each plugin whose os matches
                                           ▼
                        Plugin.run(Context) ──► Observation(s) + coverage + warnings
                                           │
                                           ▼
                              correlate.build_graph ──► DeviceGraph
                                           │
                                           ▼
                 output: report.html, timeline.jsonl/csv, devices.json, coverage.csv, manifest.json
```

## Evidence access (`tether/evidence.py`)

`EvidenceFS` is a read-only view of a directory: a mounted image, an
extracted image, or a triage collection. Lookups are case-insensitive so a
Windows image extracted onto a case-sensitive filesystem behaves like
Windows. `discover_systems` walks a few levels down to find every system
root: a KAPE `C` folder, a Velociraptor `uploads/auto/C%3A` folder, an APFS
Data volume, a Linux root. Each root becomes a `SystemRoot`, labelled with
the computer name recorded in the evidence (Windows `ComputerName`, macOS
`SystemConfiguration`, Linux `/etc/hostname`).

## Readers (`tether/readers/`)

| Reader | Guarantee |
|---|---|
| `sqlite.open_db` | Copies the database with `-wal`, `-shm`, `-journal` to a temp dir; replays the WAL into the copy; opens the copy read-only. The original is never opened by SQLite. |
| `sqlite_wal.recover_table_versions` | Pure-Python reader of the WAL and b-tree (no SQLite involved): returns every version of every row of a table found in the main file and in WAL frames, with checksum-validated frame attribution. Used to report deleted and superseded rows. |
| `registry.open_hive` | regipy, with transaction logs applied to a copy when present; values are never truncated. |
| `plist` | Binary/XML plists and NSKeyedArchiver archives. |
| `x509` | Validity dates and names from PEM/DER certificates, standard library only. |

## Plugins (`tether/plugins/`)

A plugin is a class registered with `@register`. It declares:

* `os`: the computer OS it applies to;
* `channel`: how the phone reached the computer (usb, bluetooth, pairing,
  backup, phone_link, continuity, account_sync, …);
* `paths`: glob patterns of files it reads, which drive `tether targets`
  and `tether docs`;
* `validation`: `synthetic`, `public_sample` or `ground_truth`.

Plugins read evidence only through the `Context`, which hashes every file,
records coverage for every file touched (`parsed`, `empty`, `unrecognised`,
`error`), isolates per-file failures, and exports recovered content (e.g.
cached photos) with hashes.

## Data model (`tether/model.py`)

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

## Correlation (`tether/correlate.py`)

Union-find over strong identifiers that a single record asserts together.
Every union keeps the record that caused it, which the report shows under
"How these records were linked". Records with only weak or link
identifiers form unconfirmed devices. Possible matches are computed per
device pair (shared name, shared phone number or account, adjacent MAC
addresses, matching MAC suffixes) and grouped into candidate groups for
the examiner. Nothing is merged on those heuristics; an examiner can
record a conclusion with `--assume-same`, which appears as an explicit
merge reason and in the manifest.

## Outputs (`tether/output/`)

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
