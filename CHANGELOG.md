# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Found by scanning a live Windows 11 system with an Android phone linked in Phone Link.

### Added

- Phone Link RCS messages (`rcs_chat`) and RCS file transfers (`rcs_filetransfer`) in `phone.db`
  are reported like SMS: received/sent events, sender and thread recipients (`rcs_conversation`,
  with the SMS/MMS thread it falls back to), the phone's own number from the SIM subscription,
  attachments exported, and deleted or edited rows recovered from the WAL. Before, these tables
  were only listed in a warning.
- The Phone Link phone is labelled with the name, manufacturer, model and OS (for example
  "Android 15") that Phone Link records in `LocalCache\DeviceMetadataStorage.json`, with the
  registration and last-seen times. Before, it was labelled only by its profile id, such as
  "Phone (Phone Link 3fdf7366)". The computer's own entry in that file is reported but never
  becomes a phone.
- The phone's wallpaper in `deviceData.db` is reported and exported.

### Changed

- `continuity.db` (phone browser history, app hand-off contexts) and `sharedContent.db` (links and
  content shared from the phone) stay unrecognised because their value formats are not publicly
  documented, but the coverage note now says what they hold and how many rows each table has.

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

- **Critical**: recovery of deleted SQLite rows (Phone Link, Windows notifications) can report
  rows of another table that reused a page as recovered messages or notifications.
- USB serials that vendors reuse across devices (for example `0123456789ABCDEF`) are treated as
  unique, so two different phones can merge into one device.
- USB modems, MiFi devices and RNDIS gadgets can be counted as Android phones.
- A `#` or `?` in the temporary folder path makes a database open as empty.
- Velociraptor's escaped folder names are not decoded, so files in such collections are missed.
- Some deleted SQLite rows are not recovered.
- Mail and Messages attachments can be reported as AirDrop receipts.
- A symlinked `/home` (Fedora Silverblue and similar) hides Linux user profiles.
- About 30 lower-severity findings: timestamp handling, coverage notes, report wording.

[Unreleased]: https://github.com/sujayadkesar/connected-phone-forensics/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.1
[0.1.0]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.0
