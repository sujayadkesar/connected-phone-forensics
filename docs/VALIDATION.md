# Validating Tether with ground truth

Forensic tools must be shown to work before their output is relied on (for
example ISO/IEC 17025 accreditation, or the UK Forensic Science Regulator's
Code of Practice). Tether supports this directly:

1. You perform a scripted set of actions with real phones and a test
   computer, writing down **what** you did, **when**, and **with which
   device**.
2. You image or collect the computer, run `tether scan`.
3. `tether validate truth.json <scan output>` reports, item by item, what
   Tether found, missed, timed wrongly or attributed to the wrong phone.

Datasets made this way, published with their `truth.json`, are the most
valuable contribution you can make to the project: they move parsers from
`synthetic` to `ground_truth` validation.

## Before you start

* Use test phones and test accounts only. Never use personal devices: the
  dataset will contain everything the computer stored about the phone.
* Record each phone's identifiers before the session: UDID (Finder/Apple
  Devices app), serial, IMEI, Bluetooth and Wi-Fi addresses
  (Settings › General › About on iOS; Settings › About phone › Status on
  Android), device name, and `adb devices` serial for Android.
* Synchronise the clocks: note the computer's time zone and check both
  clocks against an NTP source. Record times in UTC.
* Write every action in a log as you do it (`2026-01-10T10:00:12Z plugged
  iPhone into USB-C port, tapped Trust`). The log becomes `truth.json`.
* Use unique marker strings in content you create, e.g. send an SMS
  `TETHER-GT-001`, so content can be matched unambiguously.

## Action scripts

Run the scripts that apply to your computer and phone. Leave at least two
minutes between actions so time tolerances can be tight.

### iPhone ↔ Windows

| # | Action | Expected Tether events |
|---|---|---|
| 1 | Install Apple Devices (or iTunes). Plug the iPhone in over USB, unlock, tap **Trust**, enter passcode. | `win_usb_phones: usb_first_install`, `win_apple_pairing: pairing_certificate_issued` |
| 2 | Make a local backup (unencrypted), then an encrypted one. | `win_apple_backups: device_backup` (×2, one encrypted) |
| 3 | Unplug. Wait 5 min. Plug in again. Unplug. | `usb_last_arrival`, `usb_last_removal` |
| 4 | Pair the iPhone with the PC over Bluetooth (Settings › Bluetooth). | `win_bluetooth: bt_last_connected`, paired |
| 5 | Turn on Personal Hotspot; join it from the PC over Wi-Fi; browse for a minute; disconnect. | `win_phone_hotspots: hotspot_first_connected`, `hotspot_dhcp_lease` (172.20.10.x) |
| 6 | Link the iPhone in Phone Link; send and receive an iMessage/SMS containing a marker; place a call. | Phone Link parsers (when available) |

### Android ↔ Windows

| # | Action | Expected Tether events |
|---|---|---|
| 1 | Plug in over USB, choose **File transfer (MTP)**. Copy one photo off the phone. | `win_usb_phones: usb_first_install` (model from MTP interface) |
| 2 | Enable USB debugging, run `adb devices`, accept the RSA prompt. | `win_usb_phones` with `adb` signal |
| 3 | Enable USB tethering for one minute. | `win_usb_phones` with `rndis` signal |
| 4 | Pair over Bluetooth. | `win_bluetooth: bt_last_connected` |
| 5 | Join the phone's Wi-Fi hotspot from the PC. | `win_phone_hotspots` |
| 6 | Link to Windows / Phone Link: sync messages, send an SMS with a marker, view photos, receive a notification. | Phone Link parsers (when available) |

### iPhone ↔ macOS

| # | Action | Expected Tether events |
|---|---|---|
| 1 | Connect over USB, Trust, back up in Finder. | `mac_apple_pairing`, `mac_apple_backups` |
| 2 | Sign in to the same Apple Account on both; use iPhone Mirroring, Handoff, Universal Clipboard; receive an SMS via Text Message Forwarding. | Continuity parsers (when available) |

### Android / iPhone ↔ Linux

| # | Action | Expected Tether events |
|---|---|---|
| 1 | Pair over Bluetooth. | `linux_bluetooth: bt_pairing_record` |
| 2 | Discover (scan) a second phone without pairing. | `linux_bluetooth: bt_cached_device` |

## Writing `truth.json`

```json
{
  "dataset": "GT-001 Windows 11 24H2 + iPhone 15 (iOS 18.1), 2026-01-10",
  "devices": {
    "iphone15": {"udid": "00008120-001A2B3C4D5E801E", "bt_mac": "a4:c3:37:11:22:33",
                 "serial": "F17ABCDEF123"}
  },
  "expected": [
    {"id": "usb-trust", "device": "iphone15", "event": "pairing_certificate_issued",
     "time": "2026-01-10T10:00:12Z", "tolerance_seconds": 120},
    {"id": "usb-first", "device": "iphone15", "event": "usb_first_install",
     "time": "2026-01-10T10:00:05Z", "tolerance_seconds": 120},
    {"id": "backup-enc", "device": "iphone15", "event": "device_backup", "contains": "encrypted"},
    {"id": "hotspot", "device": "iphone15", "channel": "wifi_hotspot",
     "time": "2026-01-10T10:20:00Z", "tolerance_seconds": 300}
  ]
}
```

Identifier keys under `devices` are Tether identifier kinds: `udid`,
`serial`, `imei`, `bt_mac`, `wifi_mac`, `usb_serial`, `phone_number`,
`device_name`.

Run:

```
tether scan /mnt/gt001 -o gt001_out
tether validate truth.json gt001_out
```

The report lists every item as `found`, `missing`, `time_off` (a matching
record exists but outside the tolerance) or `wrong_device`, and flags
**split devices** (one real phone reported as several), which point to a
correlation gap.

## Publishing a dataset

Share the collection (KAPE/UAC/Velociraptor output, or an image), the action
log, `truth.json` and the Tether version you validated with. Remove anything
you would not want public: test accounts only, no personal photos, no
real contacts.
