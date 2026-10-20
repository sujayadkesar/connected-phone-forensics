"""Build the device-naming tables from public lists.

    python scripts/build_device_data.py supported_devices.csv usb.ids

- supported_devices.csv: Google Play's list of supported Android devices
  (https://storage.googleapis.com/play_public/supported_devices.csv; UTF-16). Writes
  connected_phone_forensics/data/android_models.tsv.gz: "<model code>\\t<brand>\\t<marketing name>",
  so a model code such as CPH2585 reads as "OnePlus 12R".
- usb.ids: the Linux USB ID repository (http://www.linux-usb.org/usb.ids; BSD-3-Clause or
  GPL-2.0-or-later). Writes connected_phone_forensics/data/usb_vendors.tsv.gz: "<vid>\\t<vendor>".
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "connected_phone_forensics" / "data"

# Brand spellings as people write them (the list mixes "Vivo", "Oppo", "LGE"...).
BRAND = {"vivo": "vivo", "oppo": "OPPO", "lge": "LG", "lg": "LG", "tct (alcatel)": "Alcatel", "realme": "realme",
         "oneplus": "OnePlus", "redmi": "Redmi", "poco": "POCO", "xiaomi": "Xiaomi", "huawei": "Huawei",
         "honor": "HONOR", "samsung": "Samsung", "google": "Google", "motorola": "Motorola", "nokia": "Nokia",
         "hmd global": "Nokia", "tecno": "TECNO", "infinix": "Infinix", "itel": "itel", "iqoo": "iQOO",
         "nothing": "Nothing", "sony": "Sony", "asus": "ASUS", "zte": "ZTE", "nubia": "nubia", "lenovo": "Lenovo",
         "lava": "Lava", "micromax": "Micromax", "fairphone": "Fairphone", "meizu": "Meizu", "htc": "HTC",
         "tcl": "TCL", "blackberry": "BlackBerry", "cmf": "CMF", "jio": "Jio"}


def _brand(raw: str) -> str:
    key = " ".join(raw.split()).lower()
    return BRAND.get(key, " ".join(raw.split()))


def android_models(path: Path) -> dict[str, tuple[str, str]]:
    text = path.read_bytes().decode("utf-16")
    names: dict[str, Counter] = defaultdict(Counter)
    brands: dict[str, Counter] = defaultdict(Counter)
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 4 or row[0] == "Retail Branding":
            continue
        brand, name, _device, model = (" ".join(c.split()) for c in row[:4])
        if not model:
            continue
        key = model.upper()
        brands[key][_brand(brand)] += 1
        if name and (name.upper() != key or " " in name):  # a marketing name ("Pixel 7a"), not a bare code
            names[key][name] += 1
    out = {}
    for key, bc in brands.items():
        brand = bc.most_common(1)[0][0]
        name = names[key].most_common(1)[0][0] if names[key] else ""
        # "Y02t" -> "vivo Y02t"; names that already carry a brand ("Galaxy S22", "iQOO Z7") keep theirs
        if (name and brand and not name.lower().startswith(brand.lower()) and brand not in ("Redmi", "POCO")
                and not re.match(r"(galaxy|iqoo|redmi|poco|pixel|moto|nord|xperia|zenfone|rog)\b", name, re.I)):
            name = f"{brand} {name}"
        out[key] = (brand, name)
    return out


def usb_vendors(path: Path) -> tuple[str, dict[str, str]]:
    version = ""
    vendors = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# Version:"):
            version = line.split(":", 1)[1].strip()
        m = re.match(r"^([0-9a-fA-F]{4})  (.+)$", line)
        if m:
            vendors[m.group(1).upper()] = m.group(2).strip()
        if line.startswith("C "):  # device classes follow the vendor list
            break
    return version, vendors


def _write_gz(path: Path, text: str) -> None:
    """Gzip without a timestamp or file name in the header: the same lists always give the same file."""
    with open(path, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=9,
                                                 mtime=0) as gz:
        gz.write(text.encode("utf-8"))


def main(args: list[str]) -> int:
    models = android_models(Path(args[0]))
    lines = [f"# Google Play supported devices list (storage.googleapis.com/play_public/supported_devices.csv), "
             f"retrieved {date.today()}"]
    lines += [f"{k}\t{b}\t{n}" for k, (b, n) in sorted(models.items())]
    out = DATA / "android_models.tsv.gz"
    _write_gz(out, "\n".join(lines) + "\n")
    print(f"{len(models)} Android models -> {out} ({out.stat().st_size // 1024} KB)")

    version, vendors = usb_vendors(Path(args[1]))
    lines = [f"# The Linux USB ID repository (www.linux-usb.org/usb.ids), version {version}; BSD-3-Clause"]
    lines += [f"{k}\t{v}" for k, v in sorted(vendors.items())]
    out = DATA / "usb_vendors.tsv.gz"
    _write_gz(out, "\n".join(lines) + "\n")
    print(f"{len(vendors)} USB vendors -> {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
