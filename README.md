<div align="center">

<img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/logo.png" width="104" alt="Connected Phone Forensics logo">

# Connected Phone Forensics

**Find every phone that was connected to a computer, what it left there, and exactly where each fact came from.**

USB · Bluetooth · Wi-Fi hotspots · Phone Link · iPhone pairing and backups · Messages · AirDrop · KDE Connect<br>
from Windows, macOS and Linux disk images, triage collections, or the running computer itself.

[![Release](https://img.shields.io/github/v/release/sujayadkesar/connected-phone-forensics?include_prereleases&sort=semver&label=release&color=2f6bed)](https://github.com/sujayadkesar/connected-phone-forensics/releases)
[![CI](https://github.com/sujayadkesar/connected-phone-forensics/actions/workflows/ci.yml/badge.svg)](https://github.com/sujayadkesar/connected-phone-forensics/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/connected-phone-forensics?color=2f6bed&label=pypi)](https://pypi.org/project/connected-phone-forensics/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3776AB?logo=python&logoColor=white)](https://github.com/sujayadkesar/connected-phone-forensics#install-with-pip-windows-macos-linux)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555)](https://github.com/sujayadkesar/connected-phone-forensics#quick-start)

[![Download for Windows](https://img.shields.io/badge/Download-Windows%20app-2f6bed?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/sujayadkesar/connected-phone-forensics/releases/latest)
&nbsp;
[![Quick start](https://img.shields.io/badge/Quick%20start-1c222b?style=for-the-badge)](https://github.com/sujayadkesar/connected-phone-forensics#quick-start)
&nbsp;
[![Documentation](https://img.shields.io/badge/Docs-1c222b?style=for-the-badge)](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/docs/ARCHITECTURE.md)

<br>

<img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/devices.png" alt="A phone found on the computers, drawn as it looks, with its identity, decoded UDID and what was recovered from it" width="100%">

</div>

## Why

Seized phones are increasingly unreadable: since iOS 18.1 a locked iPhone reboots into
Before-First-Unlock after 72 hours without use, and full-file-system extractions depend on
exploits that come and go. The suspect's computer is usually easier to examine, and phones leave a
trail on every computer they touch: pairing records, backups, USB and Bluetooth history, hotspot
connections, companion apps, synced messages and calls.

Connected Phone Forensics finds that trail and answers three questions:

- **Which phones touched this computer?** Each phone is identified by its UDID, serial, IMEI,
  Bluetooth or Wi-Fi MAC, or USB serial, every identifier is decoded (an iPhone UDID names its
  chip and model family, an IMEI its model code, a MAC address its maker) and traced to the record
  that holds it.
- **When, and over which connection?** USB, Bluetooth, hotspot, pairing, backup, Phone Link,
  Continuity, companion apps, with first and last seen for each.
- **What phone data is on the computer?** Messages, calls, contacts, photos, notifications and
  apps, each tagged with the phone it came from, including records recovered after deletion.

## Highlights

- **23 parsers** for Windows, macOS and Linux, run in parallel. Point it at a mounted image or a
  KAPE, Velociraptor or UAC collection and every computer inside is found automatically.
- **Live acquisition** of the running Windows computer, for examining a computer on site: locked
  registry hives are saved through the registry API, protected files are read with the backup
  privilege, everything is hashed and logged, and the output goes to a USB drive, never to the
  drive being examined. A portable build runs from the USB drive without unpacking anything onto it.
- **Recovered data at a glance:** messages, calls, contacts with their photos, photos and files,
  notifications and apps with their icons, per phone; deleted and present records told apart.
  Search, **filter any column as in Excel** (contains, does not contain, a regular expression,
  dates, numbers, a checklist of values), and export to Excel or CSV.
- **Save the files themselves, verified:** the selected items, the current view, a whole kind or
  everything, copied byte for byte into a new folder, each copy hashed and checked against the hash
  taken at the scan, marked read-only and given its record's time, with a manifest and a
  `SHA256SUMS.txt`.
- **Phones named the way people know them:** "OnePlus 12R (CPH2585)", not a model code or a UUID,
  from Google Play's device list, USB vendor IDs, and the names people give their phones and
  hotspots.
- **Hotspots grouped by network:** a phone known only by its Wi-Fi hotspot is one network with a
  row per access point (phones use a new random address each time), and an optional, logged lookup
  of where an access point was seen.
- **Three reports from every scan:** a detailed, auditable HTML report; a clean **PDF summary** to
  draft a forensic report from; and an **Excel workbook** with a summary sheet and one sheet per
  phone.
- **One device per phone, defensibly.** Records merge only through strong identifiers named
  together in one record, and each merge shows the record that caused it. Shared names, numbers
  and neighbouring MAC addresses are shown as *possible* matches and never merged silently.
- **Provenance for every fact:** source file, SHA-256 and a locator precise enough to find the
  record by hand (`SYSTEM:\ControlSet001\Enum\USB\...\0066`, `table=message rowid=42`).
- **Read-only and verifiable.** Databases and hives are read from copies, every evidence file
  read is hashed into a manifest, and nothing is skipped silently: unknown formats are reported.
- **A guide built in:** what each connection leaves on a computer, what can be recovered and when
  it cannot, how to read the results, and the tool's limits.

## Screenshots

<table>
  <tr>
    <td width="50%"><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/overview.png" alt="Case overview: the case name, phones, hotspots, recovered data at a glance"></td>
    <td width="50%"><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/column-filter.png" alt="Recovered data: a column filter, as in Excel, on the messages of one phone"></td>
  </tr>
  <tr>
    <td align="center"><b>Overview</b>: the case at a glance, with its reports</td>
    <td align="center"><b>Recovered data</b>: filter any column, as in Excel</td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/new-scan.png" alt="New scan: the evidence folder or this computer, the case name and examiner"></td>
    <td><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/hotspots.png" alt="A phone known only by its Wi-Fi hotspot: one network, an access point per day it was joined"></td>
  </tr>
  <tr>
    <td align="center"><b>New scan</b>: a folder, or this computer live</td>
    <td align="center"><b>Hotspots</b>: one network, a row per random access point</td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/devices-light.png" alt="An Android phone linked with Phone Link, with its recovered wallpaper on the drawn screen"></td>
    <td><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/contacts-light.png" alt="Contacts recovered from Phone Link with their photos, in the light theme"></td>
  </tr>
  <tr>
    <td align="center"><b>Devices</b>: the phone drawn with its recovered wallpaper</td>
    <td align="center"><b>Contacts</b> with their photos, in the light theme</td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/guide.png" alt="The built-in guide: how records become devices"></td>
    <td><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/live.png" alt="Live acquisition of the running computer, as administrator, with the output on a USB drive"></td>
  </tr>
  <tr>
    <td align="center"><b>Guide</b>: how it works, what is recovered and when not, limits</td>
    <td align="center"><b>Live acquisition</b>: examine the running computer, output to a USB drive</td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/recovered-photos-light.png" alt="Photos and files recovered from Phone Link, as a grid, in the light theme"></td>
    <td><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/timeline.png" alt="Timeline with filters and the provenance of the selected record"></td>
  </tr>
  <tr>
    <td align="center"><b>Photos and files</b>, saved as verified copies</td>
    <td align="center"><b>Timeline</b>: every record with its file, locator and SHA-256</td>
  </tr>
  <tr>
    <td colspan="2"><img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/screenshots/pdf-summary.png" alt="Two pages of the PDF summary: the case at a glance and a page per phone"></td>
  </tr>
  <tr>
    <td colspan="2" align="center"><b>PDF summary</b>: the case at a glance, then a page per phone</td>
  </tr>
</table>

<sub>Screenshots show the built-in demo case: entirely synthetic data.</sub>

## Quick start

### Windows: download and run

From the [latest release](https://github.com/sujayadkesar/connected-phone-forensics/releases/latest):

| Download | Use it for |
|---|---|
| **`ConnectedPhoneForensics-<version>-windows-x64.exe`** | The desktop app. Nothing to install: download and run. |
| **`ConnectedPhoneForensics-<version>-windows-x64-portable.zip`** | Examining a running computer from a USB drive: both programs in one folder that runs in place (see [Live forensics](#live-forensics)). |
| `cpf-<version>-windows-x64.exe` | The command-line tool. |

The executables are not code-signed yet, so SmartScreen may say *Windows protected your PC*:
choose **More info → Run anyway**. Check the download against `SHA256SUMS.txt` first:

```powershell
certutil -hashfile ConnectedPhoneForensics-0.3.2-windows-x64.exe SHA256
```

Then click **Run the demo case** to see it work on synthetic data, or drop an evidence folder on
**New scan**.

### Install with pip (Windows, macOS, Linux)

```bash
pip install connected-phone-forensics
```

Then run `cpf-gui` for the desktop app or `cpf --help` for the command line. Needs Python 3.10 or
newer.

### Try it without evidence

```bash
cpf demo ./demo
```

builds a synthetic case (a Windows laptop, a MacBook and a Linux desktop that one iPhone and one
Pixel touched), scans it and writes the reports to `./demo/report`. Every byte is generated.

## Usage

### Desktop app

| Page | What it shows |
|---|---|
| **New scan** | An evidence folder (drop it), or **this computer (live)**; the computers found, case name, examiner, output folder, parsers |
| **Overview** | The case name and its reports (HTML, PDF summary, workbook); phones, phone hotspots, recovered data at a glance, computers, records by connection type, records that may be the same phone |
| **Devices** | Phones, hotspots and other devices, each marked by how it connected. Each phone drawn as it most likely looks, with its identity; every identifier in full with what it tells; connections with first and last seen; why its records were linked; possible matches. Each hotspot network with its access points, the times they were joined, and an optional location lookup |
| **Recovered data** | Messages, calls, contacts (with photos), photos and files, notifications, apps (with icons) and connection records, filtered by phone, connection, status (present, deleted and recovered, with a saved file), search and per-column filters; right-click a cell to show only or hide its value; the selected item in full with its source; **Save files** as verified copies; export of the rows shown to Excel or CSV |
| **Timeline** | All records, searchable and filterable by device, connection, computer, parser and per-column filters; provenance of the selected record; CSV export |
| **Coverage** | Every evidence file read, with status `parsed`, `empty`, `unrecognised` or `error`, and its full note |
| **Issues** | Parser warnings and errors, with the full message |
| **Guide** | How the tool works, where phones leave traces, what is recovered and when it is not, the limitations, and every parser |

**Open results folder** reopens any earlier scan. Keyboard: <kbd>Ctrl</kbd>+<kbd>N</kbd> new scan,
<kbd>Ctrl</kbd>+<kbd>O</kbd> open results, <kbd>Ctrl</kbd>+<kbd>F</kbd> search,
<kbd>Ctrl</kbd>+<kbd>1</kbd>…<kbd>8</kbd> switch pages, <kbd>F1</kbd> the guide.

### Command line

```bash
cpf scan /mnt/evidence -o ACME-case --case ACME-Incident-EmpID39323 --examiner "A. Examiner"
```

| Command | Purpose |
|---|---|
| `cpf scan INPUT -o OUT` | Scan a mounted image, extracted drive or triage collection. Options: `--case`, `--examiner`, `--plugins`, `--workers N`, `--workdir`, `--no-export`, `--no-html`, `--no-pdf`, `--no-xlsx`, `--assume-same` |
| `cpf live -o OUT` | Acquire this running Windows computer (as administrator), then scan the copy. Options: `--case`, `--examiner`, `--elevate`, `--allow-system-drive`, and those of `scan` |
| `cpf demo OUT` | Build and scan the synthetic demo case |
| `cpf save-files RESULTS -o OUT` | Save the recovered files as verified, read-only copies with a manifest. Options: `--kind`, `--device`, `--status present\|deleted` |
| `cpf locate BSSID…` | Ask Apple's Wi-Fi location service where access points were seen (online; asks first, never sends random addresses). Options: `--yes`, `--case-dir` (logs each lookup), `--json` |
| `cpf plugins` | List the parsers and how far each has been validated |
| `cpf targets --format kape\|velociraptor\|list` | Collection targets for exactly the files the parsers read |
| `cpf validate truth.json OUT` | Score a scan against a ground-truth file |
| `cpf docs -o artifacts.md` | Artifact reference generated from the parsers |

## Live forensics

For examining a computer where it stands, for example during a search, when the question is which
phones were connected to it and what they left:

1. Unzip **`…-portable.zip`** onto a USB drive (or an external disk). The portable folder runs in
   place, so the program is not unpacked onto the computer's own drive, and it keeps its settings
   next to itself instead of in the computer's registry.
2. On the computer, run **`ConnectedPhoneForensics.exe`** and choose **New scan → This computer
   (live)**. The app shows the computer, its Windows version and user profiles, and whether it runs
   as administrator; **Restart as administrator** asks Windows for the rights it needs.
3. Choose the output drive: the USB drive is suggested. The drive being examined is refused,
   because writing there overwrites free space that may still hold deleted evidence (it can be
   allowed explicitly; the report then records it).
4. **Start live acquisition.** Or from an administrator terminal:

   ```powershell
   E:\ConnectedPhoneForensics\cpf.exe live -o E:\cases\ACME-Incident-EmpID39323 --case ACME-Incident-EmpID39323 --examiner "A. Examiner"
   ```

What happens:

- The registry hives Windows keeps locked (SYSTEM, SOFTWARE and the NTUSER.DAT of every signed-in
  user) are saved through the registry API, RegSaveKeyEx, as `reg save` does: a consistent copy of
  each hive as Windows sees it at that moment.
- Every other file the parsers read is copied with the backup privilege, so folder permissions
  cannot hide it, with its SQLite and registry journal files. Each file is hashed while it is read,
  and the copies keep the original creation, access and modification times (parsers use them).
- The copy (`collection/`) is then scanned like any triage collection, and the reports are written
  to `report/`. `acquisition.json` and `acquisition.sha256` list every item with its source,
  method and SHA-256; `acquisition.log` has the timestamped steps.
- Any program run on a computer leaves traces of its own: Windows records a Prefetch entry for the
  program and event-log entries for the administrator prompt. They do not affect the files
  acquired; mention them in your notes.

## Reports

Every scan writes three reports from the same records:

| Report | For |
|---|---|
| `report.html` | The detailed, auditable account: each phone with every identifier decoded, its connections and why its records were linked; recovered data per phone; the full timeline with filters; every file examined and what came of it; the method. Self-contained; works offline. |
| `summary.pdf` | A short, readable summary to draft a forensic report from: the case at a glance, every phone in one table, the phone hotspots joined (a table per network), then a page per phone with a drawing of it, its identity, identifiers, connections and what was recovered, and a page on method and limits. |
| `workbook.xlsx` | Excel: a summary sheet (case, recovered data at a glance, the phones with links to their sheets, the hotspot networks, the computers), one sheet per phone with its identifiers, connections and a table per kind of recovered data, and a sheet of hotspot networks with every access point. Every block is an Excel table with sort and filter buttons. |

## What it finds

| Parser | Computer | Evidence of | Validation |
|---|---|---|---|
| `win_usb_phones` | Windows | Phones over USB (MTP/PTP/ADB/RNDIS, iPhone), install/arrival/removal times, serial/UDID, model | public sample |
| `win_setupapi_phones` | Windows | First driver install per phone, later driver installs (setupapi.dev.log) | synthetic |
| `win_bluetooth` | Windows | Bluetooth pairings (BTHPORT): name, class of device, last seen/connected, link keys; phone services offered to the PC (Hands-Free, MAP, PBAP, Apple Notification Center) | synthetic |
| `win_phone_hotspots` | Windows | Phone hotspots joined (NetworkList + DHCP; iPhone 172.20.10.0/28) | public sample |
| `win_phonelink` | Windows | Microsoft Phone Link caches: SMS/MMS/RCS messages and RCS file transfers, calls, contacts, notifications, photos, wallpaper, app list, the phone's own number; the phone's name, model and OS from DeviceMetadataStorage.json; deleted and edited rows recovered from the WAL | synthetic |
| `win_wpn_phonelink` | Windows | Phone Link toasts in the Windows notification database, including recovered expired toasts | public sample |
| `win_crossdevice` | Windows | Phones known to the Microsoft account (TaskFlow DeviceCache), cross-device settings | synthetic |
| `win_apple_pairing` / `mac_apple_pairing` | Windows / macOS | iPhone trust (lockdown) records: UDID, Wi-Fi MAC, escrow keybag, pairing time | synthetic |
| `win_apple_backups` / `mac_apple_backups` | Windows / macOS | iPhone backups: identity, phone number, IMEI, apps, dates, encryption | synthetic |
| `mac_biome_sync` | macOS | iPhones syncing with the Mac through Biome: OS build, last sync, data streams received | synthetic |
| `mac_messages` | macOS | SMS relayed from the iPhone (Text Message Forwarding) and iMessage; the iPhone's number | synthetic |
| `mac_callhistory` | macOS | Calls synced from the iPhone, with the originating device name | synthetic |
| `mac_airdrop` | macOS | Files received by AirDrop, with the sender's name | synthetic |
| `mac_phone_hotspots` | macOS | Phone hotspots joined (known networks, Personal Hotspot flag, DHCP leases) | synthetic |
| `mac_notifications` | macOS | Notifications forwarded from the iPhone (inferred: the app is not installed on the Mac) | synthetic |
| `mac_knowledgec_peers` | macOS | knowledgeC sync peers (older macOS) | synthetic |
| `mac_iphone_mirroring` | macOS | iPhone Mirroring pairing, the iPhone's app inventory | synthetic |
| `linux_bluetooth` | Linux | BlueZ pairings and discovered devices | synthetic |
| `*_kdeconnect` | Linux / Windows / macOS | KDE Connect paired phones; when the app on the phone created its identity, and whether that was the Android or the iOS app | synthetic |

**Validation levels are printed in every report.** `synthetic`: tested against fixtures built from
public documentation. `public_sample`: also tested against real public data (the DFIR Artifact
Museum). `ground_truth`: tested against data created on real devices with recorded actions. See
[docs/VALIDATION.md](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/docs/VALIDATION.md); ground-truth datasets are the most valuable contribution
the project can receive.

## How it works

<img src="https://raw.githubusercontent.com/sujayadkesar/connected-phone-forensics/main/docs/images/how-it-works.png" alt="How it works: evidence folder, find each computer, 23 parsers in parallel, records with provenance, correlate on strong identifiers, device graph, outputs" width="100%">

More in [docs/ARCHITECTURE.md](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/docs/ARCHITECTURE.md).

## How evidence is treated

- **Read-only.** SQLite databases are copied together with their `-wal` and `-shm` files and the
  copy is opened, so the original is never modified and committed WAL records are not lost. Dirty
  registry hives are recovered from their transaction logs on a copy, following the REGF
  specification: only entries that continue the hive's sequence and pass their hash checks are
  applied, and logs next to a clean hive are left alone.
- **Hashed.** Every evidence file read is SHA-256 hashed; hashes, tool version and command line go
  into `manifest.json`.
- **Nothing skipped silently.** Every file a parser opened is in the coverage table. A file whose
  format is not recognised is reported as `unrecognised`, not dropped, and a folder that cannot be
  read is reported as an error.
- **Honest time.** All times are UTC. Local-time artifacts are converted with the time zone recorded
  in the same image. Filesystem times are labelled as such and do not set a phone's first or last
  seen.
- **Long paths.** On Windows, evidence deeper than 260 characters (common for Phone Link) is read
  with extended-length paths, so it is never mistaken for missing.

## Outputs

| File | Contents |
|---|---|
| `report.html`, `summary.pdf`, `workbook.xlsx` | The three reports (see [Reports](#reports)) |
| `timeline.jsonl` / `timeline.csv` | Timestamped records; imports directly into Timesketch |
| `observations.jsonl` | Every record, timed or not |
| `devices.json` | The device graph, with decoded identifiers, merge reasons and possible matches |
| `coverage.csv` | Every evidence file read and its parse status |
| `manifest.json` | Run metadata, evidence hashes, warnings |
| `exports/` | Files recovered from the evidence (cached photos, attachments, wallpaper), with their SHA-256 in the records |
| `acquisition.json`, `.sha256`, `.log`, `collection/` | Live acquisition only (one folder above the report): every item acquired, its hash and the copies |
| `online-lookups.jsonl` | Only after a Wi-Fi location lookup: each address sent, when, by whom, and the answer |

## Possible matches and examiner conclusions

Weak clues (the same device name, a phone number, MAC addresses that differ by one, a hotspot
BSSID derived from the phone's address) never merge records automatically. They are listed per pair
of devices with the number of independent signals. When an examiner has verified that two
identifiers belong to one phone, the conclusion can be recorded, and it is labelled as an examiner
assertion in the report and manifest:

```bash
cpf scan /mnt/evidence -o out --assume-same udid=00008120001A2B3C4D5E801E,bt_mac=a4:c3:37:5e:10:2b
```

## Collecting only what it needs

```bash
cpf targets --format kape > CPF.tkape
cpf targets --format velociraptor > CPF.yaml
cpf targets --format list
```

The targets are generated from the parsers' own path declarations, so a collection always matches
what the installed version can parse.

## Project status

**Early release (0.3).** The tool runs end to end on Windows, macOS and Linux, and every parser has
automated tests, but only three parsers have been checked against real public data and none yet
against ground-truth data from real phones. A code review in October 2026 found issues that are not
all fixed yet. Its one critical finding, recovery of *deleted* SQLite rows (Phone Link, Windows
notifications) reporting rows from another table as recovered messages or notifications, was fixed
in 0.1.2: a row is now attributed to a table only when its page belonged to that table in the
database state it was read from. The open issues are listed in the
[changelog](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/CHANGELOG.md#known-issues).

Treat results as leads and verify them against the source records the report points to, which is
good practice with any forensic tool.

### Roadmap

- Fix the open review findings
- Ground-truth validation with real iPhone and Android devices
- Code-signed Windows builds
- Recording examiner conclusions (same phone / not the same phone) from the desktop app

## Building from source

```bash
git clone https://github.com/sujayadkesar/connected-phone-forensics
cd connected-phone-forensics
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest                                  # GUI tests run headless
python -m ruff check connected_phone_forensics tests
```

Windows executables: `pip install -e ".[build]"`, then `python packaging/build.py` and
`python packaging/smoke_test.py dist`. Releases are built the same way by
[GitHub Actions](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/.github/workflows/release.yml) when a version tag is pushed.

## Contributing

Parsers are small, self-registering modules. [CONTRIBUTING.md](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/CONTRIBUTING.md) describes the
plugin API and the evidence-handling rules every parser follows. Bug reports, artifact requests and
ground-truth datasets are welcome in the [issue tracker](https://github.com/sujayadkesar/connected-phone-forensics/issues).
Please report security problems privately, as described in [SECURITY.md](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/SECURITY.md).

## Citing

If you use Connected Phone Forensics in research or casework documentation, please cite it; GitHub
shows a citation from [CITATION.cff](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/CITATION.cff) under *Cite this repository*.

## License

See [LICENSE](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/LICENSE); the components it builds on are listed in
[THIRD-PARTY-NOTICES.md](https://github.com/sujayadkesar/connected-phone-forensics/blob/main/THIRD-PARTY-NOTICES.md). Provided as is, without warranty: validate
results before relying on them in legal proceedings.
