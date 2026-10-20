# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-20

### Added

- **Live acquisition** of the Windows computer the tool runs on (`cpf live`, and *This computer
  (live)* on the app's New scan page), for examining a computer on site. As administrator, the
  registry hives Windows keeps locked (SYSTEM, SOFTWARE, the NTUSER.DAT of signed-in users) are
  saved through the registry API (RegSaveKeyEx, as `reg save` does); every other file the parsers
  read is copied with the backup privilege (so folder permissions cannot hide it), hashed while it
  is read and given its original times. The copy is then scanned like any collection.
  `acquisition.json`, `acquisition.sha256` and a timestamped `acquisition.log` record every item.
  The output must be on another drive than the one examined (a USB drive or external disk):
  writing to the examined drive would overwrite free space that may hold deleted evidence, so it
  is refused unless explicitly allowed, and then recorded. The app can restart itself as
  administrator, and suggests a folder on the drive it runs from. Scanning the running system's
  drive as a plain folder now explains why that misses evidence and offers live acquisition.
- **Portable build** (`…-portable.zip`): both programs in one folder that runs in place from a USB
  drive, so nothing is unpacked to the examined computer's temporary folder. In portable mode the
  app keeps its settings next to it and uses Qt's own folder dialogs, so nothing is added to the
  examined user's registry (Windows' dialogs record folders in the user's recently-used lists).
- **Recovered data** page: messages, calls, contacts, photos and files, notifications, apps and
  connection records per phone, with tiles that count each kind for the phone, connection,
  search and *deleted, recovered only* filters chosen; a sortable table (or a photo grid with
  thumbnails); the selected item in full with a preview, its source file, record and SHA-256 and a
  link to the timeline; and export of the rows shown to Excel or CSV. Contact names are filled in
  from the phone's own address book.
- **PDF summary** (`summary.pdf`, written by every scan): the case at a glance, every phone in one
  table and the computers examined, then a page per phone with a drawing of it, its identity, its
  identifiers decoded, how it connected and what was recovered, and a page on method and limits.
  Text stays selectable and names in any script (for example Hindi or Cyrillic) are shaped
  properly.
- **Excel workbook** (`workbook.xlsx`, written by every scan): a summary sheet (case, recovered data
  at a glance, the phones with links to their sheets, the computers) and one sheet per phone with
  its identifiers, connections and a table per kind of recovered data. Every block is an Excel
  table with sort and filter buttons; times are real date-times; evidence text that starts with
  `=` stays text.
- The device page draws the phone as it most likely looks (Dynamic Island, notch or Home button
  from its model or the chip in its UDID; a punch-hole camera for Android), with the wallpaper
  recovered from Phone Link on its screen, beside its identity and counts of what was recovered
  from it.
- Identifiers are decoded and shown in full everywhere: an iPhone UDID names its chip and model
  family and its ECID, an IMEI its check digit and model code, a MAC address its maker (or that it
  is randomised), a SIM or phone number its country, an Apple serial its format. Unnamed phones
  are labelled by model family and full identifier instead of a shortened one.
- New logo, app icon and colours; `--no-pdf` and `--no-xlsx` skip the new outputs.

### Changed

- report.html lists every identifier with what it tells, adds a recovered-data table per phone and
  a live-acquisition section, and links to the PDF summary and the workbook.
- The demo case has generated pictures (a photo, a photographed document, a floor plan, a
  wallpaper) instead of 1-pixel placeholders.

### Fixed

- Notes and warnings show Windows errors readably ("PermissionError: Permission denied:
  C:\Windows\System32\config\SYSTEM") instead of an escaped `\\?\` path.
- Coverage notes were cut off: the Coverage page now shows the selected file's full note and path.
- Icons that did not exist in the icon font drew empty circles; a test now checks every name.
- The executables now include the package's data files (logo, style images, the MAC vendor table).

## [0.1.3] - 2026-10-19

### Fixed

- The project page on PyPI shows the logo, screenshots and diagram: the README now links images
  and documents by absolute URLs, which PyPI needs, and the Mermaid flowchart (which PyPI cannot
  render) is an image.

## [0.1.2] - 2026-10-18

### Added

Found by scanning a live Windows 11 system with an Android phone linked in Phone Link:

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

### Changed

- `continuity.db` (phone browser history, app hand-off contexts) and `sharedContent.db` (links and
  content shared from the phone) stay unrecognised because their value formats are not publicly
  documented, but the coverage note now says what they hold and how many rows each table has.

### Known issues

Still open from the October 2026 code review:

- USB serials that vendors reuse across devices (for example `0123456789ABCDEF`) are treated as
  unique, so two different phones can merge into one device.
- USB modems, MiFi devices and RNDIS gadgets can be counted as Android phones.
- A `#` or `?` in the temporary folder path makes a database open as empty.
- Velociraptor's escaped folder names are not decoded, so files in such collections are missed.
- Mail and Messages attachments can be reported as AirDrop receipts.
- A symlinked `/home` (Fedora Silverblue and similar) hides Linux user profiles.
- About 30 lower-severity findings: timestamp handling, coverage notes, report wording.

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
  rows of another table that reused a page as recovered messages or notifications (fixed in 0.1.2).
- USB serials that vendors reuse across devices (for example `0123456789ABCDEF`) are treated as
  unique, so two different phones can merge into one device.
- USB modems, MiFi devices and RNDIS gadgets can be counted as Android phones.
- Folders that cannot be read (permissions) are treated as empty, without a warning (fixed in
  0.1.1).
- A `#` or `?` in the temporary folder path makes a database open as empty.
- Velociraptor's escaped folder names are not decoded, so files in such collections are missed.
- Some deleted SQLite rows are not recovered (fixed in 0.1.2).
- Mail and Messages attachments can be reported as AirDrop receipts.
- A symlinked `/home` (Fedora Silverblue and similar) hides Linux user profiles.
- About 30 lower-severity findings: timestamp handling, coverage notes, report wording.

[Unreleased]: https://github.com/sujayadkesar/connected-phone-forensics/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.2.0
[0.1.3]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.3
[0.1.2]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.2
[0.1.1]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.1
[0.1.0]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.0
