# Tether

**Recover smartphone evidence from the computers a phone was connected to.**

Seized phones are increasingly unreadable: since iOS 18.1 a locked iPhone
reboots into Before-First-Unlock after 72 hours of inactivity, and
full-file-system extractions depend on exploits that come and go. The
suspect's laptop is usually easier, and phones leave a trail on every
computer they touch: pairing records, backups, USB and Bluetooth history,
hotspot connections, companion apps.

Tether finds that trail on Windows, macOS and Linux evidence and answers:

* **Which phones touched this computer?** Each phone is identified by its
  UDID, serial, IMEI, Bluetooth/Wi-Fi MAC or USB serial, with every
  identifier traced to the record that asserted it.
* **When, and over which channel?** USB, Bluetooth, hotspot, pairing,
  backup, companion app, with first and last seen per channel.
* **What phone data landed on the computer?** Every item is tagged with the
  phone it came from.

It correlates everything into a **device graph** and writes a self-contained
HTML report, a Timesketch-ready timeline and a chain-of-custody manifest.

```
pip install tether-dfir
tether scan /mnt/evidence -o case42_tether --case 42 --examiner "A. Examiner"
```

Input is a mounted image, an extracted image, or a triage collection (KAPE,
Velociraptor, UAC). Tether finds every Windows, macOS and Linux system root
inside it.

## What it parses

| Parser | Computer | Evidence of | Validation |
|---|---|---|---|
| `win_usb_phones` | Windows | Phones over USB (MTP/PTP/ADB/RNDIS, iPhone), install/arrival/removal times, serial/UDID, model | public sample |
| `win_setupapi_phones` | Windows | First driver install per phone (setupapi.dev.log) | synthetic |
| `win_bluetooth` | Windows | Bluetooth pairings (BTHPORT): name, class of device, last seen/connected, link keys | synthetic |
| `win_phone_hotspots` | Windows | Phone hotspots joined (NetworkList + DHCP; iPhone 172.20.10.0/28) | public sample |
| `win_apple_pairing` / `mac_apple_pairing` | Windows / macOS | iPhone trust (lockdown) records: UDID, Wi-Fi MAC, escrow keybag, pairing time | synthetic |
| `win_apple_backups` / `mac_apple_backups` | Windows / macOS | iPhone backups: identity, phone number, IMEI, apps, dates, encryption | synthetic |
| `linux_bluetooth` | Linux | BlueZ pairings and discovered devices | synthetic |
| `*_kdeconnect` | Linux / Windows / macOS | KDE Connect paired phones, with identity-creation date | synthetic |

Run `tether plugins` for the current list and `tether docs` for a full
artifact reference generated from the parsers themselves.

**Validation levels are printed in every report.** `synthetic` means tested
against fixtures built from public documentation; `public_sample` means also
tested against real public data (the DFIR Artifact Museum); `ground_truth`
means tested against data created on real devices with recorded actions.
See [docs/VALIDATION.md](docs/VALIDATION.md); contributions of ground-truth
datasets are the most valuable help the project can get.

## How Tether treats evidence

* **Read-only.** SQLite databases are copied together with their `-wal`
  and `-shm` files and the copy is opened, so the original is never modified
  and committed WAL records are not lost. Dirty registry hives are recovered
  from their transaction logs on a copy.
* **Hashed.** Every evidence file read is SHA-256 hashed; hashes, tool
  version and command line go into `manifest.json`.
* **Nothing skipped silently.** Every file a parser opened is listed in the
  coverage table. A file that exists but has a format or schema Tether does
  not recognise is reported as `unrecognised`, not dropped.
* **Every fact has provenance.** Each record carries the source file, its
  hash and a locator precise enough to re-find it by hand
  (`SYSTEM:\ControlSet001\Enum\USB\...\0066`, `table=message rowid=42`).
* **Defensible correlation.** Records merge into one phone only through
  strong identifiers asserted together by a single record, and the report
  shows which record caused each merge. Device names, models and phone
  numbers never merge records; shared values are shown as possible matches.
* **Honest time.** All times are UTC. Local-time artifacts are converted
  with the timezone recorded in the same image. Filesystem times are
  labelled as such and do not drive a phone's first/last seen.

## Outputs

| File | Contents |
|---|---|
| `report.html` | Single self-contained file (works offline): phones, identifiers, channels, timeline, coverage |
| `timeline.jsonl` / `timeline.csv` | Timestamped records; imports directly into Timesketch |
| `observations.jsonl` | Every record, timed or not |
| `devices.json` | The device graph, with merge reasons and possible matches |
| `coverage.csv` | Every evidence file touched and its parse status |
| `manifest.json` | Run metadata, evidence hashes, warnings |

## Collecting only what Tether needs

```
tether targets --format kape > Tether.tkape
tether targets --format velociraptor > Tether.yaml
tether targets --format list
```

The targets are generated from the parsers' own path declarations, so a
collection always matches what the installed version can parse.

## Validating before you rely on it

```
tether validate truth.json case_out/
```

scores a scan against a ground-truth file describing what was actually done
with real devices. See [docs/VALIDATION.md](docs/VALIDATION.md).

## Contributing

Parsers are small, self-registering modules. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the plugin API and the evidence-
handling rules every parser follows.

## License

MIT.
