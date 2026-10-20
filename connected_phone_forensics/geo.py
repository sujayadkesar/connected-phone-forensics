"""Where was a Wi-Fi access point? An opt-in lookup in Apple's Wi-Fi location service.

Apple's location service (the one iPhones use to place themselves by the Wi-Fi networks
around them) answers, for an access point address (BSSID) it has seen, the position and
accuracy its crowd-sourced records hold. This module asks it about one address at a time.

Read this before relying on it:

- **It is an online query.** Apple learns which address was asked about and when. Nothing is
  sent unless the examiner asks, for each lookup; every lookup is written to
  ``online-lookups.jsonl`` in the case folder (what was sent, when, by whom, the answer).
- **Randomised addresses cannot be located** and are never sent: Android 11 and later, and
  recent iPhones, give their hotspot a new random address (a "locally administered" one) each
  time it is switched on. Most phone-hotspot BSSIDs are randomised.
- **A phone's hotspot moves with the phone**; a position, when there is one, is where devices
  that contribute to Apple's records saw that address, at times nobody records. It is a lead,
  not a measurement. No answer does not mean the access point does not exist.

The request format follows the public descriptions of Apple's ``gs-loc.apple.com/clls/wloc``
protocol (iSniff-GPS, apple_bssid_locator).
"""

from __future__ import annotations

import gzip
import json
import struct
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ENDPOINT = "https://gs-loc.apple.com/clls/wloc"
SERVICE = "Apple Wi-Fi location service (gs-loc.apple.com)"
_PREAMBLE = (b"\x00\x01\x00\x05en_US\x00\x13com.apple.locationd\x00\x0a8.1.12B411"
             b"\x00\x00\x00\x01\x00\x00")
_NOT_FOUND = -18000000000  # latitude Apple answers for an address it does not know


@dataclass
class Location:
    bssid: str
    found: bool
    latitude: float | None = None
    longitude: float | None = None
    accuracy_m: int | None = None
    note: str = ""

    @property
    def map_url(self) -> str | None:
        if not self.found:
            return None
        return (f"https://www.openstreetmap.org/?mlat={self.latitude:.6f}&mlon={self.longitude:.6f}"
                f"#map=17/{self.latitude:.6f}/{self.longitude:.6f}")


def normalise(bssid: str) -> str:
    hexa = "".join(ch for ch in str(bssid) if ch.lower() in "0123456789abcdef")
    if len(hexa) != 12:
        raise ValueError(f"not a MAC address: {bssid!r}")
    return ":".join(hexa[i:i + 2] for i in range(0, 12, 2)).lower()


def is_randomised(bssid: str) -> bool:
    """A locally administered address (second-least-significant bit of the first octet set)."""
    return bool(int(normalise(bssid)[:2], 16) & 0x02)


# -- a minimal protobuf codec (varints and length-delimited fields only) --------------------------


def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(no: int, wire: int, payload: bytes | int) -> bytes:
    key = _varint((no << 3) | wire)
    if wire == 0:
        return key + _varint(int(payload))
    return key + _varint(len(payload)) + payload


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = n = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7


def _fields(buf: bytes):
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        no, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _read_varint(buf, i)
        elif wire == 2:
            length, i = _read_varint(buf, i)
            value, i = buf[i:i + length], i + length
        elif wire == 1:
            value, i = buf[i:i + 8], i + 8
        elif wire == 5:
            value, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield no, wire, value


def _signed(n: int) -> int:
    return n - (1 << 64) if n >= 1 << 63 else n


def request_body(bssid: str) -> bytes:
    device = _field(1, 2, normalise(bssid).encode())
    message = _field(2, 2, device) + _field(3, 0, 0) + _field(4, 0, 1)  # 4 = one answer, no neighbours
    return _PREAMBLE + struct.pack(">H", len(message)) + message


def parse_response(body: bytes, asked: str) -> Location:
    target = normalise(asked)
    for no, wire, value in _fields(body[10:]):
        if no != 2 or wire != 2:
            continue
        mac, loc = None, {}
        for fno, fwire, fval in _fields(value):
            if fno == 1 and fwire == 2:
                mac = fval.decode("ascii", "replace")
            elif fno == 2 and fwire == 2:
                loc = {n: _signed(v) for n, w, v in _fields(fval) if w == 0}
        try:
            same = mac is not None and normalise(":".join(p.zfill(2) for p in mac.split(":"))) == target
        except ValueError:
            same = False
        if not same:
            continue
        lat = loc.get(1)
        if lat is None or lat == _NOT_FOUND:
            return Location(target, False, note="not in Apple's location records")
        return Location(target, True, lat * 1e-8, loc.get(2, 0) * 1e-8, loc.get(3), "")
    return Location(target, False, note="not in Apple's location records")


def locate(bssid: str, *, timeout: float = 20.0) -> Location:
    """Ask Apple's Wi-Fi location service where ``bssid`` is. Sends the address over the internet."""
    mac = normalise(bssid)
    if is_randomised(mac):
        return Location(mac, False, note="randomised address: no location service records it; not sent")
    req = urllib.request.Request(ENDPOINT, data=request_body(mac), method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "*/*", "Accept-Charset": "utf-8",
        "Accept-Encoding": "gzip, deflate", "Accept-Language": "en-us",
        "User-Agent": "locationd/1753.17 CFNetwork/711.1.12 Darwin/14.0.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https endpoint
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return parse_response(body, mac)


def record_lookup(case_dir: Path | None, location: Location, *, examiner: str = "", sent: bool = True) -> None:
    """Append one lookup to the case's online-lookups.jsonl (what was asked, when, by whom, the answer)."""
    if case_dir is None:
        return
    entry = {"time": datetime.now(timezone.utc).isoformat(), "service": SERVICE if sent else None,
             "sent": sent, "query": location.bssid, "examiner": examiner, "answer": asdict(location),
             "map": location.map_url}
    with open(Path(case_dir) / "online-lookups.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
