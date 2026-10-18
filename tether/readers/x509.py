"""Minimal X.509 reader: validity dates, serial and subject CN, stdlib only.

Pairing records carry PEM certificates whose validity start is written by the
pairing host. Pulling in a full crypto library to read two dates is not worth
the dependency, so this walks just enough DER to reach the fields.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_PEM_RE = re.compile(rb"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", re.S)
_OID_CN = bytes.fromhex("550403")
_TIME_RE = re.compile(r"^(\d{12}|\d{14})(?:\.\d+)?(Z|[+-]\d{4})?$")


@dataclass
class CertInfo:
    not_before: datetime | None
    not_after: datetime | None
    serial: str
    subject_cn: str | None
    issuer_cn: str | None


def _tlv(buf: bytes, pos: int) -> tuple[int, int, int]:
    """Return (tag, content_start, content_end) of the DER element at pos."""
    tag = buf[pos]
    length = buf[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(buf[pos:pos + n], "big")
        pos += n
    return tag, pos, pos + length


def _children(buf: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    out = []
    pos = start
    while pos < end:
        tag, cs, ce = _tlv(buf, pos)
        out.append((tag, cs, ce))
        pos = ce
    return out


def _time(tag: int, raw: bytes) -> datetime | None:
    """UTCTime (0x17) or GeneralizedTime (0x18).

    UTCTime years follow RFC 5280 section 4.1.2.5.1: YY >= 50 is 19YY, else
    20YY (Python's %y would pivot at 69). An explicit +hhmm/-hhmm offset is
    applied; DER requires 'Z', but some generators write offsets.
    """
    text = raw.decode("ascii", errors="replace").strip()
    m = _TIME_RE.match(text)
    if not m or tag not in (0x17, 0x18):
        return None
    digits, zone = m.group(1), m.group(2) or "Z"
    try:
        if tag == 0x17 and len(digits) == 12:
            yy = int(digits[:2])
            year = 1900 + yy if yy >= 50 else 2000 + yy
            dt = datetime(year, int(digits[2:4]), int(digits[4:6]), int(digits[6:8]), int(digits[8:10]),
                          int(digits[10:12]))
        elif tag == 0x18 and len(digits) == 14:
            dt = datetime.strptime(digits, "%Y%m%d%H%M%S")
        else:
            return None
    except ValueError:
        return None
    if zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        dt -= sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[3:5]))
    return dt.replace(tzinfo=timezone.utc)


def _cn(buf: bytes, start: int, end: int) -> str | None:
    # Name ::= SEQUENCE OF SET OF SEQUENCE { type OID, value }
    for _t, s, e in _children(buf, start, end):
        for _t2, s2, e2 in _children(buf, s, e):
            parts = _children(buf, s2, e2)
            if len(parts) >= 2 and buf[parts[0][1]:parts[0][2]] == _OID_CN:
                return buf[parts[1][1]:parts[1][2]].decode("utf-8", errors="replace")
    return None


def parse_der(der: bytes) -> CertInfo | None:
    try:
        _tag, cs, ce = _tlv(der, 0)
        _tbs_tag, ts, te = _children(der, cs, ce)[0]
        fields = _children(der, ts, te)
        i = 1 if fields and fields[0][0] == 0xA0 else 0  # optional [0] version
        serial = der[fields[i][1]:fields[i][2]].hex()
        issuer = fields[i + 2]
        validity = fields[i + 3]
        subject = fields[i + 4]
        v = _children(der, validity[1], validity[2])
        nb = _time(v[0][0], der[v[0][1]:v[0][2]])
        na = _time(v[1][0], der[v[1][1]:v[1][2]])
        return CertInfo(nb, na, serial, _cn(der, subject[1], subject[2]), _cn(der, issuer[1], issuer[2]))
    except (IndexError, ValueError):
        return None


def parse_pem_or_der(data: bytes) -> CertInfo | None:
    if not data:
        return None
    m = _PEM_RE.search(data)
    if m:
        try:
            der = base64.b64decode(b"".join(m.group(1).split()))
        except ValueError:
            return None
        return parse_der(der)
    if data[:1] == b"\x30":
        return parse_der(data)
    return None
