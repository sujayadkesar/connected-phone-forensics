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
| `win_setupapi_phones` | Windows | First driver install per phone, later driver installs (setupapi.dev.log) | synthetic |
| `win_bluetooth` | Windows | Bluetooth pairings (BTHPORT): name, class of device, last seen/connected, link keys; phone services offered to the PC (Hands-Free, MAP, PBAP, Apple Notification Center) | synthetic |
| `win_phone_hotspots` | Windows | Phone hotspots joined (NetworkList + DHCP; iPhone 172.20.10.0/28) | public sample |
| `win_phonelink` | Windows | Microsoft Phone Link caches: SMS/MMS, calls, contacts, notifications, photos, app list, the phone's own number; deleted and edited rows recovered from the WAL | synthetic |
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

## Try it without evidence

```
tether demo ./demo
```

builds a synthetic case (a Windows laptop, a MacBook and a Linux desktop
that one iPhone and one Pixel touched) and scans it. Every byte is
synthetic; it shows what a report looks like and how records about the
same phone are linked across computers.

## Possible matches and examiner conclusions

Weak clues (the same device name, a phone number, MAC addresses that differ
by one, a hotspot BSSID derived from the phone's address) never merge
records automatically. The report lists them per pair of devices, with the
number of independent signals, and groups devices that are probably one
phone. When an examiner has verified that two identifiers belong to one
phone, the conclusion can be recorded:

```
tether scan /mnt/evidence -o out --assume-same udid=00008120001A2B3C4D5E801E,bt_mac=a4:c3:37:5e:10:2b
```

The merge is labelled as an examiner assertion in the report and manifest.

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
