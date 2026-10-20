# Third-party notices

Connected Phone Forensics is built on the open-source components and public reference data
below. Each is used under its own license, and that license (not the one in [LICENSE](LICENSE))
applies to it. The full license texts are in [licenses/](licenses). The Windows programs and the
portable ZIP also carry each bundled package's own license files, under `licenses/packages/`.

## Python packages

| Component | Used for | Copyright | License |
|---|---|---|---|
| [regipy](https://github.com/mkorman90/regipy) | Reading Windows registry hives | Martin G. Korman | MIT |
| [construct](https://github.com/construct/construct) | Binary structures (used by regipy) | Arkadiusz Bulski, Tomer Filiba, Corbin Simpson | MIT |
| [inflection](https://github.com/jpvanhal/inflection) | Used by regipy | Janne Vanhala | MIT |
| [pytz](https://github.com/stub42/pytz) | Time zones (used by regipy) | Stuart Bishop | MIT |
| [PySide6 and shiboken6](https://pyside.org) (Qt for Python) | The desktop app; drawing the PDF summary | The Qt Company Ltd. | LGPL-3.0 ([below](#qt-for-python-and-the-qt-libraries)) |
| [Qt](https://www.qt.io) libraries shipped with PySide6 | Windows, drawing, SVG, PDF output | The Qt Company Ltd. and contributors | LGPL-3.0 |
| [QtAwesome](https://github.com/spyder-ide/qtawesome) | Icons in the app | The Spyder development team | MIT |
| [QtPy](https://github.com/spyder-ide/qtpy) | Used by QtAwesome | QtPy contributors | MIT |
| [packaging](https://github.com/pypa/packaging) | Used by QtPy | Donald Stufft and individual contributors | Apache-2.0 or BSD-2-Clause |
| [openpyxl](https://foss.heptapod.net/openpyxl/openpyxl) | The Excel workbook | openpyxl (Eric Gazoni, Charlie Clark) | MIT |
| [et_xmlfile](https://foss.heptapod.net/openpyxl/et_xmlfile) | Used by openpyxl | openpyxl (Elias Rabel, Charlie Clark) | MIT |

## Icon fonts shipped with QtAwesome

The app draws its icons from two of these fonts; QtAwesome ships the others with them.

| Font | In the app | Copyright | License |
|---|---|---|---|
| [Phosphor Icons](https://github.com/phosphor-icons/core) 1.3.0 | Most icons | Phosphor Icons | MIT |
| [Material Design Icons](https://github.com/Templarian/MaterialDesign) 6.9.96 | USB, Phone Link, pairing and Linux icons | Pictogrammers (Austin Andrews and contributors) | Apache-2.0 |
| [Font Awesome](https://github.com/FortAwesome/Font-Awesome) 5.15.4, 6.7.2 | Not used | Fonticons, Inc. | Fonts: OFL-1.1; icons: CC-BY-4.0 |
| [Elusive Icons](https://github.com/dovy/elusive-icons) 2.0 | Not used | Team Redux | OFL-1.1 |
| [Remix Icon](https://github.com/Remix-Design/RemixIcon) 2.5.0 | Not used | Remix Design | Apache-2.0 (the license of release 2.5.0) |
| [Codicons](https://github.com/microsoft/vscode-codicons) 0.0.36 | Not used | Microsoft Corporation | CC-BY-4.0 |

## Reference data in the package

| File | Source | Used for | Terms |
|---|---|---|---|
| `data/oui.tsv.gz` | IEEE Registration Authority, [MA-L public listing](https://standards-oui.ieee.org/oui/oui.csv) | The maker of a MAC address | Public listing published by the IEEE; assignment and organisation name only |
| `data/android_models.tsv.gz` | Google Play, [supported devices list](https://storage.googleapis.com/play_public/supported_devices.csv) | Marketing names of Android model codes (CPH2585 is a OnePlus 12R) | Published by Google LLC; brand, marketing name and model columns only |
| `data/usb_vendors.tsv.gz` | The [Linux USB ID repository](http://www.linux-usb.org/usb.ids), maintained by Stephen J. Gowdy | The maker of a USB vendor ID | GPL-2.0-or-later or BSD-3-Clause; used under BSD-3-Clause ([text](licenses/BSD-3-Clause-usb-ids.txt)) |

`scripts/build_device_data.py` rebuilds the last two from the published files.

## Inside the Windows programs

| Component | Used for | Copyright | License |
|---|---|---|---|
| [Python](https://www.python.org) (CPython) | The runtime inside the .exe programs and the portable ZIP | Python Software Foundation | PSF-2.0 (Python's own LICENSE, with the licenses of the libraries it includes, as `licenses/Python-LICENSE.txt` in the builds) |
| [PyInstaller](https://pyinstaller.org) bootloader | Starts the .exe programs | PyInstaller Development Team | GPL-2.0-or-later with the bootloader exception, which allows its use with programs under any license |

## Methods and references

No code from these is included; the parsers and the location lookup were written from their
published descriptions.

- The Wi-Fi location lookup follows the public descriptions of Apple's location protocol in
  [iSniff-GPS](https://github.com/hubert3/iSniff-GPS),
  [apple_bssid_locator](https://github.com/darkosancanin/apple_bssid_locator) and
  [Wi-Fi geolocation in forensics](https://thelocalh0st.com/posts/wifi-geolocation-forensics/).
  Answers come from Apple's location service; they are Apple's data, used for an examiner's
  own lookups.
- Each parser lists the research it builds on in [docs/ARTIFACTS.md](docs/ARTIFACTS.md)
  (generated with `cpf docs`).

## Qt for Python and the Qt libraries

PySide6, shiboken6 and the Qt libraries they include are used under the GNU Lesser General
Public License, version 3 ([LGPL-3.0](licenses/LGPL-3.0.txt), which adds permissions to the
[GPL-3.0](licenses/GPL-3.0.txt)). Their source code is available from
[code.qt.io](https://code.qt.io/cgit/pyside/pyside-setup.git/) and
[download.qt.io](https://download.qt.io/official_releases/qt/).

They are not modified, and they stay replaceable: in the portable ZIP, the Qt and PySide6
libraries are separate files (DLLs and Python extension modules) next to the programs and can be
exchanged for compatible versions; the single-file programs unpack the same files into a
temporary folder each time they start. Installed with pip, the application uses whichever
compatible PySide6 is installed.
