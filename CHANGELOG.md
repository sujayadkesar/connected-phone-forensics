# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Recovery of deleted SQLite rows (Phone Link, Windows notifications) no longer reports rows of
  another table as recovered messages or notifications. A row is now attributed to a table only
  when its page belonged to that table's b-tree in the database state it was read from: the main
  file, or the end of one committed WAL transaction. Before, any old image of a page the table uses
  now was decoded as the table's rows, so a page that held conversation rows and was later reused
  for messages produced fabricated "recovered" SMS.
- Deleted rows on pages that are no longer part of the table (freed after a bulk delete, or a root
  page that has since split) are recovered; before, they were silently missed.
- Rows from a WAL transaction that never committed are no longer reported as recovered records; a
  warning says how many frames were not searched.
- When recovery could only read part of an earlier database state (for example because a
  checkpoint overwrote the pages it needs), a warning says so. Page images that a checkpoint
  replaced are never used for older states, including after a checkpoint interrupted part-way.
- A recovered record with more fields than the table has columns is reported in a warning instead
  of being cut to fit the columns.

## [0.1.1] - 2026-10-18

Found by scanning a live Windows 11 system.

### Fixed

- Folders that cannot be listed (permissions, as on a live system or an image mounted with its ACLs
  enforced) are reported as errors with a warning for every parser that needed them. Before, they
  were treated as empty, so USB, Bluetooth and hotspot history silently came back with nothing.
- Devices known only by an identifier get readable names, such as
  "iPhone (UDID …3E46801C)" or "Android phone (USB serial E28E9340)".
- macOS with older SQLite builds: OS build strings stored as numbers come back exactly.

### Changed

- `pip install connected-phone-forensics` installs the desktop app and the command line together.

### Known issues

The same as for 0.1.0 in [CHANGELOG.md](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/CHANGELOG.md#known-issues),
except that unreadable folders are now reported.

## [0.1.0] - 2026-10-18

First public release.

### Added

- **Desktop app** for Windows, macOS and Linux (PySide6): New scan with a live preview of the
  computers in the evidence, scan progress with cancel, Overview, Devices, Timeline (search,
  filters, per-record provenance, CSV export), Coverage and Issues pages; light and dark themes;
  reopening earlier results.
- **Windows executables**: the desktop app and the `cpf` command line as single-file downloads,
  with SHA-256 checksums.
- `pip install connected-phone-forensics` installs both the desktop app (`cpf-gui`) and the
  command line (`cpf`) on Windows, macOS and Linux.
- **23 parsers** across Windows, macOS and Linux: USB, setupapi, Bluetooth, phone hotspots,
  Phone Link, Windows notifications, Microsoft-account phones, iPhone pairing and backups, Biome,
  Messages, call history, AirDrop, Notification Center, knowledgeC, iPhone Mirroring, BlueZ and
  KDE Connect.
- Device correlation through strong identifiers only, with merge reasons, possible matches and
  examiner assertions (`--assume-same`).
- Outputs: self-contained HTML report, Timesketch timeline (JSONL and CSV), `devices.json`,
  `coverage.csv` and a manifest with the SHA-256 of every evidence file read.
- KAPE and Velociraptor collection targets generated from the parsers (`cpf targets`), ground-truth
  scoring (`cpf validate`) and a synthetic demo case (`cpf demo`).
- Parsers run in parallel (`--workers`); output is identical to a single-threaded run.

### Fixed

- Registry transaction logs are replayed by the REGF rules: only for dirty hives, only
  hash-verified entries that continue the hive's sequence, LOG1 and LOG2 in sequence order, grown
  hives extended. Stale log entries no longer roll back USB, Bluetooth and hotspot times or the
  computer name, and an incomplete recovery is reported instead of passing silently.
- Evidence deeper than 260 characters is read on Windows (extended-length paths); before, such
  files (often Phone Link databases) looked missing.

### Known issues

Found by a code review in October 2026 and not fixed in this release:

- USB serials that vendors reuse across devices (for example `0123456789ABCDEF`) are treated as
  unique, so two different phones can merge into one device.
- USB modems, MiFi devices and RNDIS gadgets can be counted as Android phones.
- A `#` or `?` in the temporary folder path makes a database open as empty.
- Velociraptor's escaped folder names are not decoded, so files in such collections are missed.
- Mail and Messages attachments can be reported as AirDrop receipts.
- A symlinked `/home` (Fedora Silverblue and similar) hides Linux user profiles.
- About 30 lower-severity findings: timestamp handling, coverage notes, report wording.

[Unreleased]: https://github.com/sujayadkesar/connected-phone-forensics/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.1
[0.1.0]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.0
