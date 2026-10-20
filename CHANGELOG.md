# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.1] - 2026-10-20

### Changed

- A hotspot network now lists one row per connection (each Windows profile, or Mac known network)
  with its own name, how it was joined (Wi-Fi, or cable or Bluetooth) and its own times, instead
  of one row per access point. One access point saved under several names (an iPhone's hotspot
  takes the phone's name, so renaming the phone renames the network) shows those names and
  explains them, in the app, the PDF summary, the workbook and report.html.

### Fixed

- A network Windows reached through a phone by cable or Bluetooth (USB tethering; NameType other
  than Wi-Fi) was reported as a hotspot, and its generic Windows name ("Local Area Connection")
  was taken for one of the phone's names. It is now reported as USB tethering, and the name is not
  attached to the phone.
- A DHCP lease whose network name Windows kept with a trailing space, or kept no name with, became
  a row of its own; it is now attached to the connection it belongs to.

## [0.3.0] - 2026-10-20

### Added

- **Save files, verified.** On the Recovered data page, *Save files* copies photos, attachments,
  contact pictures and app icons (the selected items, everything shown, a whole kind, or every file
  in the case) into a new folder: byte for byte, hashed (SHA-256 and MD5) while written and read
  back from the disk, compared with the hash taken when the scan extracted each file, marked
  read-only, and given the time its record holds. `manifest.csv`, `manifest.json`,
  `SHA256SUMS.txt` and a Read me describe every file: phone, record time, deleted or present, the
  database and record it came from, and its hashes. Nothing is ever overwritten. From the command
  line: `cpf save-files`.
- **Column filters, as in Excel,** on every column of Recovered data and the Timeline: sort, a
  condition (contains, does not contain, equals, begins or ends with, a regular expression, empty;
  greater or less than for numbers; on, before or after for times) and a checklist of the values
  (times grouped by day). Right-click a cell to show only or hide its value, copy it, or save the
  selected items' files. The filters in force show as chips above the table, and exports carry
  them in their title.
- A **status filter** on Recovered data: present (live records), deleted and recovered, or with a
  saved file. Tables and the photo grid allow selecting several items.
- **Contact photos and app icons**: Phone Link's contact pictures and app icons are saved by the
  scan and shown in the tables and the detail panel (initials when there is no picture), with the
  recently used apps. A photo saved only as a thumbnail says so.
- **Hotspot networks.** Phones the computer knew only as a Wi-Fi hotspot are grouped by network
  name: Android and recent iPhones use a new random address each time the hotspot is switched on,
  so one phone appears as several access points. Each network lists its access points with when
  they were first and last joined, the Windows profile and the DHCP addresses: on the Devices page
  (Hotspots), in the PDF summary, in the workbook (a Hotspots sheet) and in report.html.
- **Wi-Fi location lookup, on request.** On a hotspot's page, or with `cpf locate BSSID`, ask
  Apple's Wi-Fi location service where an access point was seen. Nothing is sent without
  confirmation, randomised addresses are never sent, and every query and answer is logged in the
  case folder (`online-lookups.jsonl`).
- **Guide** page (F1): how the tool works, where phones leave traces, what is recovered and when it
  is not, how to read the results, hotspots and locations, saving and reports, the limitations,
  and every parser with how it has been tested.
- **Phones named the way people know them.** Android model codes become marketing names (CPH2585
  is a OnePlus 12R) from Google Play's device list; USB vendor IDs name the maker (from the Linux
  USB ID repository); phone names inside hotspot and device names are recognised ("Redmi 9i",
  "Galaxy S22 5G"); and a phone's own name ("Sujay's iPhone") is preferred over generic ones
  ("Localhost").
- New scan opens with an animated banner. The Overview shows the case name with its reports, and
  a tile and a list for phone hotspots. The Devices page separates phones, hotspots and other
  devices, with an icon for each kind of connection.
- THIRD-PARTY-NOTICES.md lists the components and data the tool is built on, and their license
  texts ship with the programs and the package.

### Changed

- *Case reference* is now *Case name* (for example ACME-Incident-EmpID39323): it titles the
  Overview, the reports and the output folder.
- The sidebar has a larger logo, larger and bolder navigation, and a Help section.
- PDF summary: count tiles fit five-digit numbers; phones known only as hotspots are a table per
  network instead of a page each of unknown fields.
- The demo case adds a phone known only by its hotspot, joined on three days with three random
  addresses.

### Fixed

- Switching from the dark to the light theme after working in the app restyled only some icons:
  widgets the pages had replaced stopped the change part-way. Widgets now unregister themselves
  when they are deleted, and the style sheet is applied first.
- Long device names wrap in the device list instead of being cut off.
- On Linux with Python 3.10 or 3.11, the app and the PDF summary could abort after enough drawing
  ("deallocating None"): PySide6 6.12.0's Linux builds drop a reference in every call that sets a
  pen, brush or font. That combination now installs PySide6 6.11, and a test watches for the leak.
- Times read from files (pair records, KDE Connect and Phone Link folders, BlueZ, iPhone
  Mirroring) are converted from exact nanoseconds, so a copy of the evidence gives the same
  microsecond as the original.

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

[Unreleased]: https://github.com/sujayadkesar/connected-phone-forensics/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.3.1
[0.3.0]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.3.0
[0.2.0]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.2.0
[0.1.3]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.3
[0.1.2]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.2
[0.1.1]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.1
[0.1.0]: https://github.com/sujayadkesar/connected-phone-forensics/releases/tag/v0.1.0
