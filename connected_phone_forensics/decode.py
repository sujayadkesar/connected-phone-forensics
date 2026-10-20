"""What an identifier is and what it tells: UDIDs, serials, IMEIs, ICCIDs, MAC addresses, app ids.

``decode(kind, value)`` returns a :class:`Decoded` with a one-line summary and a breakdown of
the identifier's fields. Everything here is derived from the value alone (plus bundled public
tables: the IEEE MAC registry, Apple chip ids, Apple model codes, calling codes); nothing is looked
up online. Where a scheme is only community-documented, the summary says so.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class Decoded:
    kind_label: str                 # "UDID", "IMEI", "Bluetooth address"...
    summary: str                    # one line: what this value tells
    fields: list[tuple[str, str]] = field(default_factory=list)  # breakdown, in reading order
    valid: bool | None = None       # check-digit / format validity where defined
    note: str = ""                  # caveat for the examiner

    def text(self) -> str:
        """Summary plus breakdown as one readable line (for tables and exports)."""
        parts = [self.summary] + [f"{k}: {v}" for k, v in self.fields]
        return " · ".join(p for p in parts if p)


KIND_LABEL = {
    "udid": "UDID", "serial": "Serial number", "imei": "IMEI", "meid": "MEID", "iccid": "ICCID (SIM card)",
    "phone_number": "Phone number", "bt_mac": "Bluetooth address", "wifi_mac": "Wi-Fi address",
    "usb_serial": "USB serial", "app_device_id": "App device ID", "device_name": "Device name",
    "model": "Model", "os_version": "OS version", "account": "Account",
}

# Apple SoC chip ids (CPID), the first part of a post-2018 UDID: 0000XXXX-<16-hex ECID>.
# Sources: The Apple Wiki (CHIP, UDID, chip pages); device lists of libimobiledevice / idevicerestore.
APPLE_CHIPS = {
    0x8020: ("A12 Bionic", "T8020", "iPhone XS, XS Max, XR; iPad Air (3rd gen), iPad mini (5th gen), iPad (8th gen)"),
    0x8027: ("A12X / A12Z Bionic", "T8027", "iPad Pro (2018, 2020)"),
    0x8030: ("A13 Bionic", "T8030", "iPhone 11, 11 Pro, 11 Pro Max, SE (2nd gen); iPad (9th gen)"),
    0x8101: ("A14 Bionic", "T8101", "iPhone 12 mini, 12, 12 Pro, 12 Pro Max; iPad Air (4th gen), iPad (10th gen)"),
    0x8103: ("M1", "T8103", "iPad Pro (2021), iPad Air (5th gen)"),
    0x8110: ("A15 Bionic", "T8110", "iPhone 13 mini, 13, 13 Pro, 13 Pro Max, 14, 14 Plus, SE (3rd gen); iPad mini (6th gen)"),
    0x8112: ("M2", "T8112", "iPad Pro (2022), iPad Air (2024)"),
    0x8120: ("A16 Bionic", "T8120", "iPhone 14 Pro, 14 Pro Max, 15, 15 Plus; iPad (A16)"),
    0x8122: ("M3", "T8122", "iPad Air (2025)"),
    0x8130: ("A17 Pro", "T8130", "iPhone 15 Pro, 15 Pro Max; iPad mini (A17 Pro)"),
    0x8132: ("M4", "T8132", "iPad Pro (2024)"),
    0x8140: ("A18 / A18 Pro", "T8140", "iPhone 16, 16 Plus, 16 Pro, 16 Pro Max, 16e"),
    0x8150: ("A19 / A19 Pro", "T8150", "iPhone 17, 17 Pro, 17 Pro Max, iPhone Air"),
}
# The iPhone generation each chip id points to, for naming a device that has no other name.
APPLE_CHIP_FAMILY = {
    0x8020: "iPhone XS / XR family", 0x8030: "iPhone 11 family", 0x8101: "iPhone 12 family",
    0x8110: "iPhone 13 / 14 family", 0x8120: "iPhone 14 Pro / 15 family", 0x8130: "iPhone 15 Pro family",
    0x8140: "iPhone 16 family", 0x8150: "iPhone 17 family",
}

# Apple product types (ProductType / hw.machine) to marketing names.
APPLE_MODELS = {
    "iPhone7,2": "iPhone 6", "iPhone7,1": "iPhone 6 Plus", "iPhone8,1": "iPhone 6s", "iPhone8,2": "iPhone 6s Plus",
    "iPhone8,4": "iPhone SE (1st gen)", "iPhone9,1": "iPhone 7", "iPhone9,3": "iPhone 7", "iPhone9,2": "iPhone 7 Plus",
    "iPhone9,4": "iPhone 7 Plus", "iPhone10,1": "iPhone 8", "iPhone10,4": "iPhone 8", "iPhone10,2": "iPhone 8 Plus",
    "iPhone10,5": "iPhone 8 Plus", "iPhone10,3": "iPhone X", "iPhone10,6": "iPhone X", "iPhone11,2": "iPhone XS",
    "iPhone11,4": "iPhone XS Max", "iPhone11,6": "iPhone XS Max", "iPhone11,8": "iPhone XR", "iPhone12,1": "iPhone 11",
    "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max", "iPhone12,8": "iPhone SE (2nd gen)",
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,4": "iPhone 12 Pro Max", "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13",
    "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max", "iPhone14,6": "iPhone SE (3rd gen)",
    "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus", "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max", "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max", "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus", "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone17,5": "iPhone 16e",
}

# IMEI reporting body identifiers (first two digits).
IMEI_BODIES = {
    "00": "test IMEI", "01": "PTCRB (United States)", "30": "Iridium", "33": "DGPT (France)",
    "35": "BABT (United Kingdom)", "44": "BABT (United Kingdom)", "45": "NTA (Denmark)", "49": "BNetzA (Germany)",
    "50": "BABT (United Kingdom)", "51": "Cetecom ICT (Germany)", "52": "Cetecom (Germany)",
    "53": "TÜV (Germany)", "54": "Phoenix Test Lab (United Kingdom)", "86": "TAF (China)",
    "91": "MSAI (India)", "98": "BABT (United Kingdom)", "99": "GHA (multi-standard)",
}

# ITU-T E.164 country calling codes (longest prefix wins).
CALLING_CODES = {
    "1": "United States, Canada and the Caribbean (NANP)", "7": "Russia / Kazakhstan", "20": "Egypt",
    "27": "South Africa", "30": "Greece", "31": "Netherlands", "32": "Belgium", "33": "France", "34": "Spain",
    "36": "Hungary", "39": "Italy", "40": "Romania", "41": "Switzerland", "43": "Austria",
    "44": "United Kingdom", "45": "Denmark", "46": "Sweden", "47": "Norway", "48": "Poland", "49": "Germany",
    "51": "Peru", "52": "Mexico", "53": "Cuba", "54": "Argentina", "55": "Brazil", "56": "Chile",
    "57": "Colombia", "58": "Venezuela", "60": "Malaysia", "61": "Australia", "62": "Indonesia",
    "63": "Philippines", "64": "New Zealand", "65": "Singapore", "66": "Thailand", "81": "Japan",
    "82": "South Korea", "84": "Vietnam", "86": "China", "90": "Turkey", "91": "India", "92": "Pakistan",
    "93": "Afghanistan", "94": "Sri Lanka", "95": "Myanmar", "98": "Iran", "211": "South Sudan",
    "212": "Morocco", "213": "Algeria", "216": "Tunisia", "218": "Libya", "220": "Gambia", "221": "Senegal",
    "233": "Ghana", "234": "Nigeria", "249": "Sudan", "251": "Ethiopia", "254": "Kenya", "255": "Tanzania",
    "256": "Uganda", "260": "Zambia", "263": "Zimbabwe", "351": "Portugal", "352": "Luxembourg",
    "353": "Ireland", "354": "Iceland", "355": "Albania", "356": "Malta", "357": "Cyprus", "358": "Finland",
    "359": "Bulgaria", "370": "Lithuania", "371": "Latvia", "372": "Estonia", "373": "Moldova",
    "374": "Armenia", "375": "Belarus", "380": "Ukraine", "381": "Serbia", "385": "Croatia",
    "386": "Slovenia", "420": "Czechia", "421": "Slovakia", "852": "Hong Kong", "853": "Macau",
    "855": "Cambodia", "856": "Laos", "880": "Bangladesh", "886": "Taiwan", "960": "Maldives",
    "961": "Lebanon", "962": "Jordan", "963": "Syria", "964": "Iraq", "965": "Kuwait", "966": "Saudi Arabia",
    "967": "Yemen", "968": "Oman", "970": "Palestine", "971": "United Arab Emirates", "972": "Israel",
    "973": "Bahrain", "974": "Qatar", "975": "Bhutan", "976": "Mongolia", "977": "Nepal",
    "992": "Tajikistan", "993": "Turkmenistan", "994": "Azerbaijan", "995": "Georgia", "996": "Kyrgyzstan",
    "998": "Uzbekistan",
}

APP_NAMESPACES = {
    "phonelink": ("Phone Link device ID",
                  "the ID Microsoft Phone Link gives a linked phone (one per phone and Windows user profile)"),
    "win_container": ("Windows container ID",
                      "the ContainerId Windows assigns to the phone's driver nodes; derived from the device's "
                      "identity, so it stays the same when the phone is plugged in again"),
    "kdeconnect": ("KDE Connect device ID", "generated by the KDE Connect app on the phone at first launch"),
    "biome_site": ("Biome site ID", "the ID Apple's Biome sync framework gives a device in the iCloud account"),
    "ids_device": ("Apple IDS device ID", "the device's ID in Apple's Identity Services (iMessage, Continuity)"),
    "knowledgec_peer": ("knowledgeC peer ID", "the ID of a device syncing knowledgeC data with this Mac"),
    "oneness": ("iPhone Mirroring pairing ID", "the ID macOS records for the paired iPhone (Oneness)"),
    "wifi_ap_mac": ("Hotspot BSSID", "the MAC address the phone's Personal Hotspot uses as its access point"),
    "cdp_platform_device": ("Connected Devices Platform ID",
                            "the ID Windows' Connected Devices Platform gives a device in the Microsoft account"),
}


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@lru_cache(maxsize=1)
def _oui() -> dict[str, str]:
    path = DATA / "oui.tsv.gz"
    if not path.is_file():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return dict(line.rstrip("\n").split("\t", 1) for line in f if line and not line.startswith("#"))


def mac_vendor(mac: str) -> str | None:
    """Organization the IEEE assigned this address block to (MA-S, MA-M, then MA-L)."""
    hexonly = re.sub(r"[^0-9A-Fa-f]", "", mac).upper()
    table = _oui()
    for n in (9, 7, 6):
        hit = table.get(hexonly[:n])
        if hit:
            return hit
    return None


@lru_cache(maxsize=1)
def _android_models() -> dict[str, tuple[str, str]]:
    path = DATA / "android_models.tsv.gz"
    if not path.is_file():
        return {}
    out = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            code, brand, name = (line.rstrip("\n").split("\t") + ["", ""])[:3]
            out[code] = (brand, name)
    return out


@lru_cache(maxsize=1)
def _marketing_names() -> dict[str, tuple[str, str]]:
    """Lower-case marketing name -> (brand, name), to recognise a model in free text."""
    out: dict[str, tuple[str, str]] = {}
    for brand, name in _android_models().values():
        if name and len(name) > 3:
            out.setdefault(name.lower(), (brand, name))
    return out


def android_model(code: str) -> tuple[str, str] | None:
    """(brand, marketing name) for an Android model code, from Google Play's device list:
    CPH2585 -> ("OnePlus", "OnePlus 12R"), SM-S901E -> ("Samsung", "Galaxy S22")."""
    key = " ".join(str(code).split()).upper()
    hit = _android_models().get(key)
    if hit is None:
        return None
    brand, name = hit
    if not name:  # the code is the marketing name ("Pixel 8")
        name = code if code.lower().startswith(brand.lower()) else f"{brand} {code}"
    return brand, name


# USB vendor IDs of phone makers, by the name people know them by. The USB ID repository spells
# some differently, and lacks a few (vivo's 2D95).
PHONE_USB_VENDORS = {
    "05AC": "Apple", "04E8": "Samsung", "18D1": "Google", "2717": "Xiaomi", "22D9": "OPPO", "2A70": "OnePlus",
    "2D95": "vivo", "12D1": "Huawei", "22B8": "Motorola", "17EF": "Lenovo", "0BB4": "HTC", "1004": "LG",
    "0FCE": "Sony", "19D2": "ZTE", "1BBB": "TCL", "2E04": "Nokia", "0489": "Foxconn", "2A45": "Meizu",
    "2AE5": "Fairphone", "0B05": "ASUS", "1949": "Amazon", "0E8D": "MediaTek", "05C6": "Qualcomm",
    "1782": "Unisoc",
}


@lru_cache(maxsize=1)
def _usb_vendors() -> dict[str, str]:
    path = DATA / "usb_vendors.tsv.gz"
    if not path.is_file():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return dict(line.rstrip("\n").split("\t", 1) for line in f if line.strip() and not line.startswith("#"))


def usb_vendor(vid: str | None) -> str | None:
    """The maker behind a USB vendor ID (2D95 -> vivo, 04E8 -> Samsung Electronics Co., Ltd)."""
    if not vid:
        return None
    key = str(vid).upper().removeprefix("0X").zfill(4)
    return PHONE_USB_VENDORS.get(key) or _usb_vendors().get(key)


def usb_brand(vid: str | None) -> str | None:
    """A phone brand from a USB vendor ID, or None for chip makers and unknown vendors."""
    key = str(vid or "").upper().removeprefix("0X").zfill(4)
    brand = PHONE_USB_VENDORS.get(key)
    return None if brand in (None, "Foxconn", "MediaTek", "Qualcomm", "Unisoc") else brand


# Phone brands, as words to find in an organisation, device or network name.
_BRAND_WORDS = [
    ("iphone", "Apple"), ("ipad", "Apple"), ("apple", "Apple"), ("galaxy", "Samsung"), ("samsung", "Samsung"),
    ("oneplus", "OnePlus"), ("one plus", "OnePlus"), ("nord", "OnePlus"), ("redmi", "Redmi"), ("poco", "POCO"),
    ("xiaomi", "Xiaomi"), ("realme", "realme"), ("narzo", "realme"), ("oppo", "OPPO"), ("iqoo", "iQOO"),
    ("vivo", "vivo"), ("pixel", "Google"), ("google", "Google"), ("motorola", "Motorola"), ("moto ", "Motorola"),
    ("nokia", "Nokia"), ("hmd global", "Nokia"), ("huawei", "Huawei"), ("honor", "HONOR"), ("infinix", "Infinix"),
    ("tecno", "TECNO"), ("itel", "itel"), ("nothing", "Nothing"), ("lava", "Lava"), ("micromax", "Micromax"),
    ("sony", "Sony"), ("xperia", "Sony"), ("asus", "ASUS"), ("zenfone", "ASUS"), ("lg electronics", "LG"),
    ("fairphone", "Fairphone"), ("meizu", "Meizu"), ("lenovo", "Lenovo"), ("zte", "ZTE"), ("nubia", "nubia"),
    ("tcl", "TCL"), ("alcatel", "Alcatel"), ("htc", "HTC"), ("blackberry", "BlackBerry"), ("jio", "Jio"),
    ("androidap", "Android"),
]
APPLE_BRANDS = {"Apple"}


def brand_from_org(org: str | None) -> str | None:
    """A phone brand from an IEEE organisation name ("Realme Chongqing Mobile..." -> "realme"), or None."""
    text = f" {(org or '').lower()} "
    for word, brand in _BRAND_WORDS:
        if word in ("moto ", "nord", "pixel", "galaxy", "iphone", "ipad", "androidap", "nothing", "lava", "jio",
                    "itel"):
            continue  # product words, not company names; "Nothing"/"Lava"/"Jio" are too generic here
        if re.search(rf"\b{re.escape(word)}\b", text):
            return brand
    if "nothing technology" in text:
        return "Nothing"
    if "transsion" in text:
        return "TECNO"
    if "guangdong oppo" in text:
        return "OPPO"
    return None


def phone_in_name(name: str | None) -> tuple[str, str | None] | None:
    """A phone brand, and the model when one is named, in free text such as a hotspot or device name:
    "OnePlus 13s 1D8C" -> ("OnePlus", "OnePlus 13s"); "Sujay’s iPhone" -> ("Apple", None)."""
    if not name:
        return None
    text = " ".join(str(name).replace("’", "'").split())
    low = text.lower()
    if low.startswith(("androidap", "android_", "android ap")):
        return "Android", None  # Android's default hotspot name, AndroidAP_1234
    words = low.split(" ")
    names = _marketing_names()
    for n in range(min(len(words), 6), 0, -1):  # the longest model name the text starts with
        hit = names.get(" ".join(words[:n]))
        if hit and n > 1:
            return hit[0], hit[1]
    for word, brand in _BRAND_WORDS:
        pattern = rf"\b{re.escape(word.strip())}\b" if word.strip() else None
        if pattern and re.search(pattern, low):
            if word == "nothing" and "phone" not in low:
                continue  # "nothing" alone is not a phone
            if word in ("lava", "jio", "itel", "honor", "tcl", "htc", "zte") and len(words) > 4:
                continue
            return brand, None
    return None


def calling_code(number: str) -> tuple[str, str] | None:
    digits = re.sub(r"\D", "", number)
    for n in (3, 2, 1):
        country = CALLING_CODES.get(digits[:n])
        if country:
            return digits[:n], country
    return None


def apple_model(product_type: str) -> str | None:
    return APPLE_MODELS.get(product_type.strip())


def ios_from_build(build: str) -> str | None:
    """iOS major version from a build string such as 22B83 (iOS 18) or 23A341 (iOS 26)."""
    m = re.match(r"^(\d{2})[A-Z]", build.strip())
    if not m:
        return None
    major = int(m.group(1))
    if 15 <= major <= 22:
        return f"iOS {major - 4}"
    if major == 23:
        return "iOS 26"
    return None


def udid_chip(value: str) -> tuple[int, str] | None:
    """(chip id, ECID hex) of a post-2018 UDID, else None."""
    m = re.fullmatch(r"0000([0-9A-Fa-f]{4})-?([0-9A-Fa-f]{16})", value.strip())
    if not m:
        return None
    return int(m.group(1), 16), m.group(2).upper()


def display_value(kind: str, value: str) -> str:
    """An identifier as people write it: UDIDs with their dash, MAC addresses upper-case with colons."""
    kind = getattr(kind, "value", kind)
    v = str(value)
    if kind in ("udid", "usb_serial") and re.fullmatch(r"0000[0-9A-Fa-f]{20}", v):
        return f"{v[:8].upper()}-{v[8:].upper()}"
    if kind in ("bt_mac", "wifi_mac") and re.fullmatch(r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", v):
        return v.upper()
    if kind == "app_device_id":
        namespace, _, rest = v.partition(":")
        if namespace == "wifi_ap_mac" and re.fullmatch(r"[0-9a-fA-F]{12}", rest.replace(":", "")):
            hexa = rest.replace(":", "").upper()
            return ":".join(hexa[i:i + 2] for i in range(0, 12, 2))
        return rest.strip("{}") or v
    return v


def device_family(kind: str, value: str) -> str | None:
    """A short model family for naming an unnamed device: 'iPhone 16 family', 'iPhone (2007-2018 model)'."""
    if kind in ("udid", "usb_serial"):
        chip = udid_chip(value)
        if chip:
            return APPLE_CHIP_FAMILY.get(chip[0])
        if re.fullmatch(r"[0-9A-Fa-f]{40}", value.strip()):
            return "iPhone (2007–2018 model)"
    if kind == "model":
        return apple_model(value)
    return None


# -- decoders --------------------------------------------------------------------------


def _udid(v: str) -> Decoded:
    chip = udid_chip(v)
    if chip:
        cpid, ecid = chip
        known = APPLE_CHIPS.get(cpid)
        fields = [("Format", "chip ID + ECID (devices from 2018, iPhone XS and later)"),
                  ("Chip ID", f"0x{cpid:04X}" + (f" · {known[0]} ({known[1]})" if known else "")),
                  ("ECID", f"0x{ecid} (exclusive chip ID, unique per device)")]
        if known:
            return Decoded("UDID", f"Apple {known[0]} chip: {known[2]}", fields)
        return Decoded("UDID", f"Apple device with chip ID 0x{cpid:04X} (not in this tool's chip table)", fields)
    if re.fullmatch(r"[0-9A-Fa-f]{40}", v.strip()):
        return Decoded("UDID", "Legacy UDID: a SHA-1 hash, the format of iPhones and iPads released before "
                       "the iPhone XS (2007–2018)",
                       [("Format", "40 hex digits, SHA-1 of the serial number, ECID/IMEI and MAC addresses"),
                        ("Generation", "iPhone X or earlier")])
    return Decoded("UDID", "Not a recognised UDID format", [("Length", str(len(v)))], valid=False)


_YEAR_CODES = "CDFGHJKLMNPQRSTVWXYZ"
_WEEK_CODES = "123456789CDFGHJKLMNPQRTVWXY"


def _apple_serial(v: str) -> Decoded | None:
    s = v.strip().upper()
    if len(s) == 10 and s.isalnum():
        return Decoded("Serial number", "Apple randomised serial (devices from 2021): carries no date or factory",
                       [("Format", "10 random characters")])
    if len(s) == 12 and s.isalnum() and s[3] in _YEAR_CODES and s[4] in _WEEK_CODES:
        i = _YEAR_CODES.index(s[3])
        year, half = 2010 + i // 2, i % 2
        week = _WEEK_CODES.index(s[4]) + 1 + (26 if half else 0)
        years = f"{year}" if year >= 2012 else f"{year} or {year + 10}"
        return Decoded("Serial number", f"Apple 12-character serial: made in week {week} of {years}",
                       [("Factory code", s[:3]), ("Year", f"{years}, {'second' if half else 'first'} half"),
                        ("Week", str(week)), ("Unit", s[5:8]), ("Model code", s[8:])],
                       note="decoded with the community-documented scheme Apple used until 2021")
    return None


def _imei(v: str) -> Decoded:
    d = re.sub(r"\D", "", v)
    if len(d) not in (14, 15, 16):
        return Decoded("IMEI", "Not a valid IMEI length", [("Digits", str(len(d)))], valid=False)
    tac, rest = d[:8], d[8:]
    body = IMEI_BODIES.get(d[:2])
    fields = [("TAC (model code)", tac + (f" · reporting body {body}" if body else "")), ("Serial", rest[:6])]
    if len(d) == 15:
        ok = luhn_ok(d)
        fields.append(("Check digit", f"{d[14]} ({'valid' if ok else 'INVALID'})"))
        return Decoded("IMEI", "Mobile equipment identity (the handset's radio)" + ("" if ok else ": check digit fails"),
                       fields, valid=ok)
    if len(d) == 16:
        fields.append(("Software version", d[14:]))
        return Decoded("IMEI", "IMEISV: handset identity with software version", fields)
    return Decoded("IMEI", "IMEI without its check digit", fields)


def _meid(v: str) -> Decoded:
    h = re.sub(r"[^0-9A-Fa-f]", "", v).upper()
    return Decoded("MEID", "CDMA mobile equipment identifier",
                   [("Manufacturer code", h[:8]), ("Serial", h[8:14])], valid=len(h) in (14, 15))


def _iccid(v: str) -> Decoded:
    d = re.sub(r"\D", "", v)
    fields = [("Industry", "89 · telecommunications")] if d.startswith("89") else []
    cc = calling_code(d[2:]) if d.startswith("89") else None
    if cc:
        fields.append(("Country", f"+{cc[0]} · {cc[1]}"))
    ok = luhn_ok(d) if 18 <= len(d) <= 20 else None
    if ok is not None:
        fields.append(("Check digit", f"{d[-1]} ({'valid' if ok else 'INVALID'})"))
    return Decoded("ICCID (SIM card)", "Identifies the SIM card, not the handset", fields, valid=ok)


def _phone(v: str) -> Decoded:
    s = v.strip()
    if s.startswith("+") or s.startswith("00"):
        cc = calling_code(s.lstrip("+0"))
        if cc:
            return Decoded("Phone number", f"International number, country code +{cc[0]} ({cc[1]})",
                           [("Subscriber number", re.sub(r"\D", "", s)[len(cc[0]):] if s.startswith("+") else s)])
    return Decoded("Phone number", "National-format number: the country is not recorded",
                   [("Digits", re.sub(r"\D", "", s))])


def _mac(kind: str, v: str) -> Decoded:
    hexonly = re.sub(r"[^0-9A-Fa-f]", "", v).lower()
    label = KIND_LABEL[kind]
    if len(hexonly) != 12:
        return Decoded(label, "Not a 48-bit MAC address", valid=False)
    first = int(hexonly[:2], 16)
    local, multicast = bool(first & 0x02), bool(first & 0x01)
    vendor = None if local else mac_vendor(hexonly)
    fields = [("Vendor block", hexonly[:6].upper() + (f" · {vendor}" if vendor else " · not registered" if not local else "")),
              ("Type", "locally administered (randomised / private address)" if local
               else "globally unique (assigned by the manufacturer)")]
    if multicast:
        fields.append(("Group bit", "multicast address"))
    if local:
        summary = ("Randomised (private) address: chosen by the phone's software, so it does not reveal the "
                   "hardware or the manufacturer")
    else:
        summary = f"Hardware address from {vendor}" if vendor else "Hardware address (vendor block not in the IEEE registry)"
    return Decoded(label, summary, fields)


def _usb_serial(v: str, os: str | None) -> Decoded:
    if udid_chip(v) or re.fullmatch(r"[0-9A-Fa-f]{40}", v.strip()) or re.fullmatch(r"0000[0-9A-Fa-f]{20}", v.strip()):
        d = _udid(v if "-" in v or len(v) == 40 else f"{v[:8]}-{v[8:]}")
        return Decoded("USB serial", "The iPhone's UDID, reported as its USB serial number. " + d.summary, d.fields)
    if os == "android" or os is None:
        return Decoded("USB serial", "Serial number the phone reports over USB (on Android, the adb serial "
                       "ro.serialno set by the manufacturer)", [("Characters", str(len(v.strip())))],
                       note="some vendors reuse the same value on many devices (e.g. 0123456789ABCDEF)")
    return Decoded("USB serial", "Serial number the device reports over USB", [("Characters", str(len(v.strip())))])


def _app_id(v: str) -> Decoded:
    ns, _, rest = v.partition(":")
    name, meaning = APP_NAMESPACES.get(ns, (ns or "App device ID", "an identifier recorded by an application"))
    fields = [("Value", rest.strip("{}"))]
    if ns == "wifi_ap_mac":
        mac = _mac("wifi_mac", rest)
        fields += mac.fields
    return Decoded(name, meaning[0].upper() + meaning[1:], fields)


def _account(v: str) -> Decoded:
    service, _, ident = v.partition(":")
    names = {"apple": "Apple Account (iCloud)", "msa": "Microsoft account", "google": "Google account"}
    return Decoded("Account", f"{names.get(service, service)} the phone is signed in to", [("Identifier", ident)])


def decode(kind: str, value: str, os: str | None = None) -> Decoded:
    """Explain an identifier. ``kind`` is an IdKind value; ``os`` ('ios'/'android') helps with serials."""
    kind = getattr(kind, "value", kind)
    v = str(value)
    if kind == "udid":
        return _udid(v)
    if kind == "serial":
        apple = _apple_serial(v) if os in (None, "ios") else None
        return apple or Decoded("Serial number", "Manufacturer serial number", [("Characters", str(len(v)))])
    if kind == "imei":
        return _imei(v)
    if kind == "meid":
        return _meid(v)
    if kind == "iccid":
        return _iccid(v)
    if kind == "phone_number":
        return _phone(v)
    if kind in ("bt_mac", "wifi_mac"):
        return _mac(kind, v)
    if kind == "usb_serial":
        return _usb_serial(v, os)
    if kind == "app_device_id":
        return _app_id(v)
    if kind == "account":
        return _account(v)
    if kind == "model":
        name = apple_model(v)
        return Decoded("Model", f"Apple {name}" if name else "Model as recorded by the device", [])
    if kind == "os_version":
        ios = ios_from_build(v)
        return Decoded("OS version", f"Build of {ios}" if ios else "Operating system version as recorded", [])
    if kind == "device_name":
        return Decoded("Device name", "Name the owner gave the device: chosen freely, not unique", [])
    return Decoded(KIND_LABEL.get(kind, kind), "", [])
